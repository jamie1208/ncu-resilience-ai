"""趨勢回測（P6-T04；G6-2）：滾動切點，每個切點只用切點前已知的資料。

切點 c 是某週期的起日，代表「台北時間 c 當天 00:00 當下」。那時可用的只有已建立
（`created_at` 早於切點）且已發生（發生日期早於 c）的事件，晚到的回報不算；在這份快照上：

- 預測：以 EWMA 預測從 c 開始那一期的件數；
- 線上偵測：c 前一期是否被 `detect_changes` 判為突增。

評分用「事後」資料（`as_of` 當下已知的全部事件）：c 開始那一期的實際件數（只對已完整結束的
週期評分）、c 前一期事後是否判為突增，以及切點當下對 c 前一期件數的完整度（晚到回報的比例）。

限制：查證狀態與類別用目前的值（事件沒有逐版歷史），被改判為不實或 none 的事件從所有切點排除。
件數是被回報、被彙整成事件的數量，不等於實際發生次數（聲量≠事件數）。報告不含地點與內文。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.timeseries import (
    DetectionParams,
    EventPoint,
    Frequency,
    bucket,
    count_series,
    detect_changes,
    ewma,
    period_start,
    periods,
)
from app.models.event import Event

TAIPEI = ZoneInfo("Asia/Taipei")
ALL_SCOPE = "all"
# 與 0016_analytics_views 的排除條件一致
IGNORED_STATUSES = ("false",)
IGNORED_TYPES = ("none",)
NOTES = (
    "件數是被回報、被彙整成事件的數量，不等於實際發生次數（聲量≠事件數）。",
    "每個切點只用台北時間切點 00:00 前已建立且已發生的事件；"
    "實際值與事後判定用 as_of 當下的全部資料。",
    "查證狀態與類別用目前的值，被改判為不實或 none 的事件從所有切點排除。",
)


@dataclass(frozen=True)
class EventRecord:
    """回測用的事件：發生日期與系統得知的時間。"""

    occurred_on: date
    """台北時間的發生日期（與 `timeseries` 相同規則）。"""
    known_at: datetime
    """事件建立時間（aware）；早於切點才算切點當下已知。"""
    event_type: str

    def __post_init__(self) -> None:
        if self.known_at.tzinfo is None:
            raise ValueError("known_at 必須是 aware datetime")


@dataclass(frozen=True)
class OnlineResult:
    """某切點只用快照算出的結果。"""

    known_events: int
    """快照中的事件數。"""
    evaluated_period: date
    """切點前一期的起日。"""
    known_count: int
    """切點當下所知的前一期件數。"""
    forecast: float | None
    """從切點開始那一期的 EWMA 預測；歷史不足 `min_history` 期時為 None。"""
    online_anomaly: bool


@dataclass(frozen=True)
class BacktestRow(OnlineResult):
    """某切點的線上結果加上事後評分。"""

    cutoff: date
    final_count: int
    """事後所知的前一期件數。"""
    completeness: float | None
    """known_count / final_count；事後也是 0 件時為 None。"""
    hindsight_anomaly: bool
    actual: int | None
    """從切點開始那一期的事後件數；該期在 as_of 前尚未結束時為 None。"""


@dataclass(frozen=True)
class Summary:
    """一個範圍（全部或某類別）的回測摘要；分母為 0 時為 None。"""

    cutoffs: int
    forecast_scored: int
    forecast_mae: float | None
    mean_completeness: float | None
    online_anomalies: int
    hindsight_anomalies: int
    true_positives: int
    precision: float | None
    recall: float | None


@dataclass(frozen=True)
class ScopeResult:
    """一個範圍的逐切點結果與摘要。"""

    rows: list[BacktestRow]
    summary: Summary


@dataclass(frozen=True)
class BacktestResult:
    """整次回測。"""

    start: date
    end: date
    step: Frequency
    as_of: date
    params: DetectionParams
    events: int
    scopes: dict[str, ScopeResult] = field(default_factory=dict)


# ---------- 切點與快照 ----------


def _next_period(start: date, freq: Frequency) -> date:
    # 週期起日加 7 天（週）或 32 天（月）必落在下一期內
    return period_start(start + timedelta(days=7 if freq == "week" else 32), freq)


def _cutoff_moment(cutoff: date) -> datetime:
    return datetime.combine(cutoff, time.min, TAIPEI)


def cutoffs(start: date, end: date, freq: Frequency) -> list[date]:
    """`start`～`end` 的所有切點（對齊週期起日）。"""
    return periods(start, end, freq)


def snapshot(records: Iterable[EventRecord], cutoff: date) -> list[EventPoint]:
    """切點當下可用的事件：台北時間 `cutoff` 00:00 前已建立且發生日期早於 `cutoff`。"""
    moment = _cutoff_moment(cutoff)
    return [
        EventPoint(occurred_on=r.occurred_on, event_type=r.event_type, location_name=None)
        for r in records
        if r.known_at < moment and r.occurred_on < cutoff
    ]


def evaluate_cutoff(
    points: Sequence[EventPoint],
    cutoff: date,
    freq: Frequency,
    params: DetectionParams | None = None,
) -> OnlineResult:
    """只用快照 `points` 算出切點 `cutoff` 的預測與線上偵測；`points` 應來自 `snapshot()`。"""
    params = params or DetectionParams()
    if any(p.occurred_on >= cutoff for p in points):
        raise ValueError("points 含切點當天或之後發生的事件")
    previous = period_start(cutoff - timedelta(days=1), freq)
    if not points:
        return OnlineResult(0, previous, 0, None, online_anomaly=False)
    first = min(p.occurred_on for p in points)
    series = count_series(bucket(points, freq), freq, first, previous)
    forecast = (
        ewma([s.count for s in series], params.alpha)[-1]
        if len(series) >= params.min_history
        else None
    )
    flagged = {a.period_start for a in detect_changes(series, params)}
    return OnlineResult(
        known_events=len(points),
        evaluated_period=previous,
        known_count=series[-1].count,
        forecast=forecast,
        online_anomaly=previous in flagged,
    )


# ---------- 回測 ----------


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def _summarize(rows: Sequence[BacktestRow]) -> Summary:
    errors = [
        abs(r.forecast - r.actual) for r in rows if r.forecast is not None and r.actual is not None
    ]
    completeness = [r.completeness for r in rows if r.completeness is not None]
    online = sum(r.online_anomaly for r in rows)
    hindsight = sum(r.hindsight_anomaly for r in rows)
    tp = sum(r.online_anomaly and r.hindsight_anomaly for r in rows)
    return Summary(
        cutoffs=len(rows),
        forecast_scored=len(errors),
        forecast_mae=_ratio(sum(errors), len(errors)),
        mean_completeness=_ratio(sum(completeness), len(completeness)),
        online_anomalies=online,
        hindsight_anomalies=hindsight,
        true_positives=tp,
        precision=_ratio(tp, online),
        recall=_ratio(tp, hindsight),
    )


def _backtest_scope(
    records: Sequence[EventRecord],
    cut_dates: list[date],
    freq: Frequency,
    as_of: date,
    params: DetectionParams,
) -> ScopeResult:
    final = snapshot(records, as_of)
    final_counts = {
        s.period_start: s.count
        for s in (
            count_series(bucket(final, freq), freq, min(p.occurred_on for p in final), as_of)
            if final
            else []
        )
    }
    hindsight = (
        {a.period_start for a in detect_changes(sorted(final_counts.items()), params)}
        if final_counts
        else set()
    )
    rows = []
    for cutoff in cut_dates:
        online = evaluate_cutoff(snapshot(records, cutoff), cutoff, freq, params)
        final_count = final_counts.get(online.evaluated_period, 0)
        complete = _next_period(cutoff, freq) <= as_of
        rows.append(
            BacktestRow(
                **asdict(online),
                cutoff=cutoff,
                final_count=final_count,
                completeness=_ratio(online.known_count, final_count),
                hindsight_anomaly=online.evaluated_period in hindsight,
                actual=final_counts.get(cutoff, 0) if complete else None,
            )
        )
    return ScopeResult(rows, _summarize(rows))


def backtest(
    records: Sequence[EventRecord],
    start: date,
    end: date,
    freq: Frequency,
    *,
    as_of: date,
    params: DetectionParams | None = None,
) -> BacktestResult:
    """在 `start`～`end` 的每個切點回測全部事件與各類別；`as_of` 是事後評分的時間點。"""
    params = params or DetectionParams()
    cut_dates = cutoffs(start, end, freq)
    if cut_dates[-1] > as_of:
        raise ValueError("切點不得晚於 as_of")
    types = sorted({r.event_type for r in records})
    result = BacktestResult(
        start=cut_dates[0],
        end=cut_dates[-1],
        step=freq,
        as_of=as_of,
        params=params,
        events=len(records),
    )
    result.scopes[ALL_SCOPE] = _backtest_scope(records, cut_dates, freq, as_of, params)
    for t in types:
        subset = [r for r in records if r.event_type == t]
        result.scopes[t] = _backtest_scope(subset, cut_dates, freq, as_of, params)
    return result


# ---------- 資料庫與報告 ----------


async def load_records(session: AsyncSession) -> list[EventRecord]:
    """讀出列入統計的事件（排除不實與類別 none），不讀地點與內文。"""
    rows = await session.execute(
        select(Event.type, Event.occurrence_start_date, Event.created_at).where(
            Event.status.not_in(IGNORED_STATUSES), Event.type.not_in(IGNORED_TYPES)
        )
    )
    return [
        EventRecord(
            occurred_on=occurred or created.astimezone(TAIPEI).date(),
            known_at=created,
            event_type=event_type,
        )
        for event_type, occurred, created in rows.all()
    ]


def _jsonable(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def write_report(
    result: BacktestResult, out_dir: Path, *, generated_at: datetime | None = None
) -> Path:
    """把回測結果寫成 `out_dir/backtest-<step>-<start>.json`，回傳路徑。"""
    generated_at = generated_at or datetime.now(UTC)
    payload = {
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "start": result.start,
        "end": result.end,
        "step": result.step,
        "as_of": result.as_of,
        "events": result.events,
        "params": asdict(result.params),
        "notes": list(NOTES),
        "scopes": {
            name: {"summary": asdict(s.summary), "rows": [asdict(r) for r in s.rows]}
            for name, s in result.scopes.items()
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"backtest-{result.step}-{result.start.isoformat()}.json"
    path.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path
