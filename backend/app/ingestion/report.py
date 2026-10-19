"""擷取穩定運行報告（P1-T10；閘門 G1-2 一晚成功率 ≥ 98%、G1-3 重跑零重複）。

- 區間：報告時間往前 `hours` 小時（預設 12，ADR-0006）。
- 全網中斷時段（ADR-0012）：連網來源（`manual_import` 以外）的有結果執行依時間排序後，連續的
  連線失敗（`SourceUnavailableError`）且涉及 ≥ 2 個來源，範圍是第一筆到最後一筆失敗；中間只要有
  一次成功就斷開。單一網站自己的失敗不算。
- 成功率：成功 ÷（成功＋失敗＋放棄），**排除全網中斷時段內的執行**（ADR-0012）；執行中、重試中與
  略過的不計入。另外列出未排除的原始成功率。
- 連續性：每個來源在區間內，從區間開始到第一次執行、相鄰兩次執行之間、最後一次執行到區間結束，
  扣掉與全網中斷時段重疊的部分後，任一段間隔都不能超過「擷取週期 × 2 ＋ 10 分鐘」；
  區間內沒有任何執行的啟用來源視為不連續。
- 重複寫入：同一來源同一文章出現多筆、同一文章同一內容雜湊出現多個版本（唯一約束保證應為 0，
  報告實際查詢一次）；另列「不同文章、內文相同」的數量作為參考，不影響判定。
- 報告只含來源代碼與筆數，不含內文。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.normalize import TAIPEI
from app.ingestion.types import RunStatus
from app.models.source import IngestionRun, Source, SourceItem, SourceItemVersion

DEFAULT_INTERVAL = 3600
GAP_SLACK = timedelta(minutes=10)
OFFLINE_TYPES = frozenset({"manual_import"})
"""不需要連網的來源類型；不參與全網中斷的判斷，也不排除。"""
CONNECTION_ERRORS = frozenset({"SourceUnavailableError"})
_FINISHED_FAIL = frozenset({RunStatus.FAILED, RunStatus.ABANDONED})
_IN_FLIGHT = frozenset({RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.RETRYING})

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class SourceReport:
    """一個來源在區間內的統計；`failed` 不含全網中斷時段內被排除的失敗（`excluded`）。"""

    code: str
    poll_interval: int
    runs: int
    succeeded: int
    failed: int
    excluded: int
    in_flight: int
    new_items: int
    changed_items: int
    unchanged: int
    max_gap: timedelta
    continuous: bool

    @property
    def finished(self) -> int:
        """計入成功率的執行數（成功＋失敗＋放棄，不含排除的）。"""
        return self.succeeded + self.failed

    @property
    def success_rate(self) -> float | None:
        """成功率；沒有任何計入的執行時為 None。"""
        return self.succeeded / self.finished if self.finished else None


@dataclass(frozen=True)
class IngestionReport:
    """穩定運行報告。"""

    window_start: datetime
    window_end: datetime
    sources: list[SourceReport] = field(default_factory=list)
    outages: list[Interval] = field(default_factory=list)
    duplicate_items: int = 0
    duplicate_versions: int = 0
    content_duplicates: int = 0

    @property
    def succeeded(self) -> int:
        """全部來源的成功次數。"""
        return sum(s.succeeded for s in self.sources)

    @property
    def finished(self) -> int:
        """全部來源計入成功率的執行數。"""
        return sum(s.finished for s in self.sources)

    @property
    def excluded_runs(self) -> int:
        """因全網中斷而排除的執行數。"""
        return sum(s.excluded for s in self.sources)

    @property
    def success_rate(self) -> float | None:
        """整體成功率（排除全網中斷時段，ADR-0012）；沒有計入的執行時為 None。"""
        return self.succeeded / self.finished if self.finished else None

    @property
    def raw_success_rate(self) -> float | None:
        """未排除全網中斷的原始成功率。"""
        total = self.finished + self.excluded_runs
        return self.succeeded / total if total else None

    @property
    def continuous(self) -> bool:
        """所有列入的來源都連續。"""
        return bool(self.sources) and all(s.continuous for s in self.sources)


def detect_outages(runs: Sequence[IngestionRun], online: set[uuid.UUID]) -> list[Interval]:
    """找出全網中斷時段（ADR-0012）；`online` 是連網來源的 id。"""
    finished = sorted(
        (
            r
            for r in runs
            if r.source_id in online and r.status in (RunStatus.SUCCEEDED, *_FINISHED_FAIL)
        ),
        key=lambda r: (r.started_at, str(r.id)),
    )
    outages: list[Interval] = []
    streak: list[IngestionRun] = []

    def close() -> None:
        if len({r.source_id for r in streak}) >= 2:
            outages.append((streak[0].started_at, streak[-1].started_at))
        streak.clear()

    for r in finished:
        if r.status != RunStatus.SUCCEEDED and r.counts.get("error_type") in CONNECTION_ERRORS:
            streak.append(r)
        else:
            close()
    close()
    return outages


def _in_outage(at: datetime, outages: Sequence[Interval]) -> bool:
    return any(start <= at <= end for start, end in outages)


def _overlap(a: Interval, b: Interval) -> timedelta:
    start, end = max(a[0], b[0]), min(a[1], b[1])
    return max(end - start, timedelta(0))


def _max_gap(
    starts: Sequence[datetime], start: datetime, end: datetime, outages: Sequence[Interval]
) -> timedelta:
    points = [start, *sorted(starts), end]
    return max(
        (b - a) - sum((_overlap((a, b), o) for o in outages), timedelta(0))
        for a, b in pairwise(points)
    )


async def build_report(session: AsyncSession, *, hours: int, now: datetime) -> IngestionReport:
    """產生 `now` 往前 `hours` 小時的報告。"""
    start = now - timedelta(hours=hours)
    runs = (
        (
            await session.execute(
                select(IngestionRun).where(
                    IngestionRun.started_at >= start, IngestionRun.started_at < now
                )
            )
        )
        .scalars()
        .all()
    )
    by_source: dict[uuid.UUID, list[IngestionRun]] = {}
    for r in runs:
        by_source.setdefault(r.source_id, []).append(r)
    sources = (
        (
            await session.execute(
                select(Source)
                .where(or_(Source.enabled, Source.id.in_(list(by_source))))
                .order_by(Source.code)
            )
        )
        .scalars()
        .all()
    )
    online = {s.id for s in sources if s.type not in OFFLINE_TYPES}
    outages = detect_outages(runs, online)
    rows: list[SourceReport] = []
    for source in sources:
        mine = by_source.get(source.id, [])
        interval = source.poll_interval or DEFAULT_INTERVAL
        gap = _max_gap([r.started_at for r in mine], start, now, outages)
        excluded = [
            r
            for r in mine
            if source.id in online
            and RunStatus(r.status) in _FINISHED_FAIL
            and _in_outage(r.started_at, outages)
        ]
        statuses = [RunStatus(r.status) for r in mine if r not in excluded]

        def total(key: str, runs_: list[IngestionRun] = mine) -> int:
            return sum(
                int(r.counts.get(key, 0) or 0) for r in runs_ if r.status == RunStatus.SUCCEEDED
            )

        rows.append(
            SourceReport(
                code=source.code,
                poll_interval=interval,
                runs=len(mine),
                succeeded=statuses.count(RunStatus.SUCCEEDED),
                failed=sum(s in _FINISHED_FAIL for s in statuses),
                excluded=len(excluded),
                in_flight=sum(s in _IN_FLIGHT for s in statuses),
                new_items=total("new_items"),
                changed_items=total("changed_items"),
                unchanged=total("unchanged"),
                max_gap=gap,
                continuous=bool(mine) and gap <= timedelta(seconds=interval * 2) + GAP_SLACK,
            )
        )
    return IngestionReport(
        window_start=start,
        window_end=now,
        sources=rows,
        outages=outages,
        duplicate_items=await _duplicate_items(session),
        duplicate_versions=await _duplicate_versions(session),
        content_duplicates=await _content_duplicates(session),
    )


async def _count_groups(session: AsyncSession, grouped: Select[Any, Any]) -> int:
    subquery = grouped.subquery()
    return int((await session.execute(select(func.count()).select_from(subquery))).scalar_one())


async def _duplicate_items(session: AsyncSession) -> int:
    by_external = (
        select(SourceItem.source_id, SourceItem.external_id)
        .where(SourceItem.external_id.is_not(None))
        .group_by(SourceItem.source_id, SourceItem.external_id)
        .having(func.count() > 1)
    )
    by_url = (
        select(SourceItem.source_id, SourceItem.url)
        .where(SourceItem.external_id.is_(None))
        .group_by(SourceItem.source_id, SourceItem.url)
        .having(func.count() > 1)
    )
    return await _count_groups(session, by_external) + await _count_groups(session, by_url)


async def _duplicate_versions(session: AsyncSession) -> int:
    grouped = (
        select(SourceItemVersion.item_id, SourceItemVersion.content_hash)
        .group_by(SourceItemVersion.item_id, SourceItemVersion.content_hash)
        .having(func.count() > 1)
    )
    return await _count_groups(session, grouped)


async def _content_duplicates(session: AsyncSession) -> int:
    grouped = (
        select(SourceItem.source_id, SourceItemVersion.content_hash)
        .join(SourceItem, SourceItem.id == SourceItemVersion.item_id)
        .group_by(SourceItem.source_id, SourceItemVersion.content_hash)
        .having(func.count(func.distinct(SourceItemVersion.item_id)) > 1)
    )
    return await _count_groups(session, grouped)


def passes(report: IngestionReport, min_success: float) -> bool:
    """G1-2：網路正常時段的成功率 ≥ `min_success`，且所有來源連續；G1-3：重複寫入為 0。"""
    rate = report.success_rate
    return (
        rate is not None
        and rate >= min_success
        and report.continuous
        and report.duplicate_items == 0
        and report.duplicate_versions == 0
    )


def _taipei(value: datetime) -> str:
    return value.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _duration(value: timedelta) -> str:
    minutes = int(value.total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m"


def render_markdown(report: IngestionReport) -> str:
    """報告的 Markdown；時間以台北時間顯示。"""
    outages = "、".join(f"{_taipei(a)} – {_taipei(b)}" for a, b in report.outages) or "無"
    lines = [
        f"- 區間：{_taipei(report.window_start)} – {_taipei(report.window_end)}（台北時間）",
        f"- 整體成功率（網路正常時段，ADR-0012）：**{_pct(report.success_rate)}**"
        f"（成功 {report.succeeded}／計入 {report.finished}）",
        f"- 原始成功率（未排除）：{_pct(report.raw_success_rate)}；"
        f"全網中斷時段：{outages}（排除 {report.excluded_runs} 次執行）",
        f"- 連續性：{'全部連續' if report.continuous else '**有來源不連續**'}",
        f"- 重複寫入：文章 {report.duplicate_items}、版本 {report.duplicate_versions}"
        f"（參考：不同文章但內文相同 {report.content_duplicates}）",
        "",
        "| 來源 | 週期 | 執行 | 成功 | 失敗 | 排除 | 進行中 | 成功率 "
        "| 新增 | 變更 | 未變 | 最大間隔 | 連續 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in report.sources:
        lines.append(
            f"| {s.code} | {s.poll_interval // 60}m | {s.runs} | {s.succeeded} | {s.failed} "
            f"| {s.excluded} | {s.in_flight} | {_pct(s.success_rate)} | {s.new_items} "
            f"| {s.changed_items} | {s.unchanged} | {_duration(s.max_gap)} "
            f"| {'✓' if s.continuous else '✗'} |"
        )
    return "\n".join(lines) + "\n"
