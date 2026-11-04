"""資料覆蓋率（P7-T06）：S09 trend-forecaster 輸入的 `coverage`。

統計期間 [start, end]（台北日期，含兩端）內，每個核准來源的
「有成功擷取（ingestion_runs.status = succeeded）的台北日數」÷「應擷取天數」，
所有來源合計即為 ratio。應擷取天數從 max(start, 來源新增日) 起算，期間中才新增的來源
不把新增前的日子算成缺漏，但來源組成改變，會在 note 說明。停用且期間內沒有擷取的來源不列入；
停用但期間內有擷取的來源照算（停用後的日子算缺漏）。

分級門檻（實作時決定，記錄於驗收紀錄）：
- low：沒有可計算的來源，或合計 ratio < `LOW_RATIO`（大部分期間缺資料）；
- full：合計 ratio ≥ `FULL_RATIO`、每個來源都 ≥ `SOURCE_FULL_RATIO`，且期間內沒有新增來源；
- 其餘為 partial（部分來源有缺漏或來源組成有變）。

限制：manual_import（Facebook）每小時檢查資料夾，即使當天沒有新的擷取檔也會成功，
覆蓋率會高估該來源。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.types import AccessPolicy, RunStatus
from app.models.source import IngestionRun, Source

CoverageLevel = Literal["full", "partial", "low"]
TAIPEI = ZoneInfo("Asia/Taipei")

FULL_RATIO = 0.95
SOURCE_FULL_RATIO = 0.9
LOW_RATIO = 0.5
MAX_NOTE_LENGTH = 300
MAX_RANGES_PER_SOURCE = 2


@dataclass(frozen=True)
class SourceActivity:
    """一個核准來源的擷取紀錄摘要。"""

    code: str
    added_on: date
    """來源新增日（sources.created_at 的台北日期）。"""
    enabled: bool
    ingested_days: frozenset[date]
    """有成功擷取的台北日期。"""


@dataclass(frozen=True)
class SourceCoverage:
    """單一來源在統計期間的覆蓋情形。"""

    code: str
    expected_days: int
    ingested_days: int
    missing: tuple[date, ...]
    added_on: date | None
    """期間中才新增時為新增日，否則為 None。"""


@dataclass(frozen=True)
class Coverage:
    """統計期間的資料覆蓋率。"""

    start: date
    end: date
    level: CoverageLevel
    ratio: float | None
    note: str | None
    sources: tuple[SourceCoverage, ...]

    def to_input(self) -> dict[str, Any]:
        """S09 輸入的 `coverage`。"""
        return {"level": self.level, "ratio": self.ratio, "note": self.note}


def _day(d: date) -> str:
    return f"{d.month}/{d.day}"


def _ranges(missing: Sequence[date]) -> list[str]:
    """把連續的缺漏日合併成「10/12–10/14」的區間。"""
    out: list[tuple[date, date]] = []
    for d in missing:
        if out and d - out[-1][1] == timedelta(1):
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return [_day(a) if a == b else f"{_day(a)}–{_day(b)}" for a, b in out]


def _source_note(s: SourceCoverage) -> list[str]:
    parts = []
    if s.added_on is not None:
        parts.append(f"{s.code} 於 {_day(s.added_on)} 起新增")
    if s.missing:
        ranges = _ranges(s.missing)
        shown = "、".join(ranges[:MAX_RANGES_PER_SOURCE])
        more = "等" if len(ranges) > MAX_RANGES_PER_SOURCE else ""
        parts.append(f"{s.code} 缺 {len(s.missing)} 天（{shown}{more}）")
    return parts


def _join(parts: Sequence[str]) -> str | None:
    """以「；」串接，超過長度上限時截斷並註明剩下幾項。"""
    if not parts:
        return None
    text = "；".join(parts)
    if len(text) <= MAX_NOTE_LENGTH:
        return text
    for keep in range(len(parts) - 1, 0, -1):
        text = "；".join(parts[:keep]) + f"；等另 {len(parts) - keep} 項"
        if len(text) <= MAX_NOTE_LENGTH:
            return text
    return parts[0][: MAX_NOTE_LENGTH - 1] + "等"


def compute_coverage(sources: Iterable[SourceActivity], start: date, end: date) -> Coverage:
    """計算 [start, end] 的覆蓋率與分級。"""
    if end < start:
        raise ValueError("end 不得早於 start")
    rows: list[SourceCoverage] = []
    for src in sources:
        first = max(start, src.added_on)
        if first > end:
            continue
        window = {first + timedelta(n) for n in range((end - first).days + 1)}
        ingested = window & src.ingested_days
        if not src.enabled and not ingested:
            continue
        rows.append(
            SourceCoverage(
                code=src.code,
                expected_days=len(window),
                ingested_days=len(ingested),
                missing=tuple(sorted(window - ingested)),
                added_on=src.added_on if src.added_on > start else None,
            )
        )
    rows.sort(key=lambda s: (-len(s.missing), s.code))

    expected = sum(s.expected_days for s in rows)
    if expected == 0:
        return Coverage(start, end, "low", None, "統計期間沒有可計算覆蓋率的核准來源", ())
    actual = sum(s.ingested_days for s in rows)
    ratio = actual / expected

    level: CoverageLevel
    if ratio < LOW_RATIO:
        level = "low"
    elif (
        ratio >= FULL_RATIO
        and all(s.ingested_days / s.expected_days >= SOURCE_FULL_RATIO for s in rows)
        and all(s.added_on is None for s in rows)
    ):
        level = "full"
    else:
        level = "partial"

    if actual == 0:
        note = "統計期間沒有任何成功的擷取紀錄"
    elif level == "full":
        note = None
    else:
        note = _join([part for s in rows for part in _source_note(s)])
    return Coverage(start, end, level, ratio, note, tuple(rows))


def taipei_date(moment: datetime) -> date:
    """aware datetime 的台北日期。"""
    return moment.astimezone(TAIPEI).date()


async def load_coverage(session: AsyncSession, start: date, end: date) -> Coverage:
    """依 sources 與 ingestion_runs 計算 [start, end] 的覆蓋率。"""
    sources = (
        await session.scalars(
            select(Source)
            .where(Source.access_policy == AccessPolicy.APPROVED.value)
            .order_by(Source.code)
        )
    ).all()
    lower = datetime.combine(start, time.min, tzinfo=TAIPEI).astimezone(UTC)
    upper = datetime.combine(end + timedelta(1), time.min, tzinfo=TAIPEI).astimezone(UTC)
    runs = await session.execute(
        select(IngestionRun.source_id, IngestionRun.started_at).where(
            IngestionRun.status == RunStatus.SUCCEEDED.value,
            IngestionRun.started_at >= lower,
            IngestionRun.started_at < upper,
        )
    )
    by_source: dict[Any, set[date]] = {}
    for source_id, started_at in runs.all():
        by_source.setdefault(source_id, set()).add(taipei_date(started_at))
    return compute_coverage(
        (
            SourceActivity(
                code=s.code,
                added_on=taipei_date(s.created_at),
                enabled=s.enabled,
                ingested_days=frozenset(by_source.get(s.id, ())),
            )
            for s in sources
        ),
        start,
        end,
    )
