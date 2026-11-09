"""系列時間軸與前後比較（P8-T05，ADR-0018 決策 1-6、1-7）。完全確定性，不呼叫模型。

時間軸：
- 粒度日／週（ISO 週一起算）／月；範圍 1M／3M／6M／1Y（今天往前 30／91／182／365 天）或全部
  （從第一起事件起）；範圍內沒有事件的週期補 0，未來的日期不計。
- 4 期移動平均：當期與前 3 期的平均。範圍起點之前的事件也算進前幾期，所以範圍的第一期不會因為
  截斷而偏低；4 期中有任何一期早於第一起事件所在的週期時為 None（沒有足夠的歷史）。
- 措施標記：第一次轉為「處理」（started）與「改進」（completed）的日期，只列出範圍內的。

前後比較：措施開始處理日的前 8 週與後 8 週（後段最多到今天）的每週平均件數。任一邊少於 5 件時
只回「資料還不夠」，不算比例；不論結果，一律附上「變少不一定是措施的功勞」的說明。

事件日期的規則與再發生、突增偵測一致（呼叫端提供）：S02 的發生日期（台北時間），沒有時用建立
時間的台北日期；不實（false）與被合併的事件不列入。
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, get_args

from app.analytics.timeseries import period_start as _week_or_month_start

Granularity = Literal["day", "week", "month"]
RangeKey = Literal["1M", "3M", "6M", "1Y", "all"]
MarkerKind = Literal["started", "completed"]

GRANULARITIES: tuple[str, ...] = get_args(Granularity)
RANGES: tuple[str, ...] = get_args(RangeKey)
RANGE_DAYS: dict[str, int] = {"1M": 30, "3M": 91, "6M": 182, "1Y": 365}
MOVING_WINDOW = 4
COMPARE_WEEKS = 8
MIN_EVENTS = 5
INSUFFICIENT_MESSAGE = "資料還不夠"
CAVEAT = (
    "變少不一定是措施的功勞：同一段時間也可能有天氣、學期時程、回報習慣或資料來源的變化，"
    "件數是被回報的數量，不等於實際發生次數。"
)


@dataclass(frozen=True)
class ActionMarker:
    """措施在時間軸上的垂直標記。"""

    action_id: uuid.UUID
    title: str
    kind: MarkerKind
    on: date
    """台北時間的日期。"""


@dataclass(frozen=True)
class TimelinePoint:
    """時間軸的一期。"""

    period_start: date
    count: int
    moving_average: float | None


@dataclass(frozen=True)
class Timeline:
    """一個系列（可依地點篩選後）的時間軸。"""

    granularity: Granularity
    range_key: RangeKey
    start: date
    end: date
    points: tuple[TimelinePoint, ...]
    markers: tuple[ActionMarker, ...]


@dataclass(frozen=True)
class Comparison:
    """措施開始前後的每週平均件數。"""

    start: date
    status: Literal["ok", "insufficient"]
    before_events: int
    after_events: int
    after_weeks: float
    """後段實際涵蓋的週數（最多 8 週，到今天為止）。"""
    before_weekly_average: float | None
    after_weekly_average: float | None
    change_ratio: float | None
    """（後 − 前）／前；資料不夠時為 None。"""
    message: str | None
    caveat: str


def _period_start(day: date, granularity: Granularity) -> date:
    if granularity == "day":
        return day
    return _week_or_month_start(day, granularity)


def _next(start: date, granularity: Granularity) -> date:
    if granularity == "day":
        return start + timedelta(days=1)
    if granularity == "week":
        return start + timedelta(weeks=1)
    return date(start.year + start.month // 12, start.month % 12 + 1, 1)


def _previous(start: date, granularity: Granularity) -> date:
    if granularity == "day":
        return start - timedelta(days=1)
    if granularity == "week":
        return start - timedelta(weeks=1)
    return (start - timedelta(days=1)).replace(day=1)


def range_start(range_key: RangeKey, today: date, first_event: date | None) -> date:
    """範圍的起日：固定天數往前推，或「全部」時第一起事件的日期（沒有事件時為今天）。"""
    if range_key == "all":
        return min(first_event, today) if first_event is not None else today
    if range_key not in RANGE_DAYS:
        raise ValueError(f"不支援的範圍：{range_key}")
    return today - timedelta(days=RANGE_DAYS[range_key])


def build_timeline(
    dates: Iterable[date],
    *,
    granularity: Granularity,
    range_key: RangeKey,
    today: date,
    markers: Sequence[ActionMarker] = (),
) -> Timeline:
    """依事件日期算出時間軸（件數、4 期移動平均、範圍內的措施標記）。"""
    if granularity not in GRANULARITIES:
        raise ValueError(f"不支援的粒度：{granularity}")
    if range_key not in RANGES:
        raise ValueError(f"不支援的範圍：{range_key}")
    days = [d for d in dates if d <= today]
    first = min(days, default=None)
    start = range_start(range_key, today, first)
    counts = Counter(_period_start(d, granularity) for d in days)
    first_period = _period_start(first, granularity) if first is not None else None

    current, last = _period_start(start, granularity), _period_start(today, granularity)
    window = [current]
    for _ in range(MOVING_WINDOW - 1):
        window.insert(0, _previous(window[0], granularity))
    points: list[TimelinePoint] = []
    while current <= last:
        average = None
        if first_period is not None and window[0] >= first_period:
            average = sum(counts[p] for p in window) / MOVING_WINDOW
        points.append(TimelinePoint(current, counts[current], average))
        current = _next(current, granularity)
        window = [*window[1:], current]
    shown = sorted(
        (m for m in markers if start <= m.on <= today),
        key=lambda m: (m.on, m.kind != "started", str(m.action_id)),
    )
    return Timeline(granularity, range_key, start, today, tuple(points), tuple(shown))


def before_after(
    dates: Iterable[date],
    start: date,
    *,
    today: date,
    weeks: int = COMPARE_WEEKS,
    min_events: int = MIN_EVENTS,
) -> Comparison:
    """措施開始日 `start` 前後各 `weeks` 週的每週平均；任一邊少於 `min_events` 件時資料不夠。"""
    span = timedelta(weeks=weeks)
    after_end = min(start + span, today + timedelta(days=1))
    after_days = max((after_end - start).days, 0)
    days = list(dates)
    before = sum(1 for d in days if start - span <= d < start)
    after = sum(1 for d in days if start <= d < after_end)
    after_weeks = after_days / 7
    if before < min_events or after < min_events or after_days == 0:
        return Comparison(
            start, "insufficient", before, after, after_weeks, None, None, None,
            INSUFFICIENT_MESSAGE, CAVEAT,
        )  # fmt: skip
    before_avg = before / weeks
    after_avg = after / after_weeks
    ratio = (after_avg - before_avg) / before_avg
    return Comparison(
        start, "ok", before, after, after_weeks, before_avg, after_avg, ratio, None, CAVEAT
    )


__all__ = [
    "CAVEAT",
    "COMPARE_WEEKS",
    "GRANULARITIES",
    "INSUFFICIENT_MESSAGE",
    "MIN_EVENTS",
    "MOVING_WINDOW",
    "RANGES",
    "ActionMarker",
    "Comparison",
    "Granularity",
    "MarkerKind",
    "RangeKey",
    "Timeline",
    "TimelinePoint",
    "before_after",
    "build_timeline",
    "range_start",
]
