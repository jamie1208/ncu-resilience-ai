"""P8-T05：系列時間軸與前後比較（app/series/timeline.py，ADR-0018 決策 1-6、1-7）。

1. 粒度日／週（ISO 週一起算）／月，期間內沒有事件的週期補 0；
2. 4 期移動平均：含當期與前 3 期，範圍起點之前的事件也算進前幾期（不因範圍截斷而失真）；
3. 範圍 1M／3M／6M／1Y／全部；
4. 措施「開始處理」「完成」標記只列在範圍內；
5. 前後比較：措施開始前後各 8 週的每週平均；任一邊少於 5 件只回「資料還不夠」、不算比例；
   「變少不一定是措施的功勞」一律出現。
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.series.timeline import (
    CAVEAT,
    COMPARE_WEEKS,
    INSUFFICIENT_MESSAGE,
    MIN_EVENTS,
    MOVING_WINDOW,
    ActionMarker,
    before_after,
    build_timeline,
    range_start,
)

TODAY = date(2026, 11, 9)  # 週一


def _counts(timeline: object) -> list[tuple[date, int]]:
    return [(p.period_start, p.count) for p in timeline.points]  # type: ignore[attr-defined]


# ---------- 粒度 ----------


def test_week_buckets_start_on_monday_and_fill_zero() -> None:
    dates = [date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 29)]
    t = build_timeline(dates, granularity="week", range_key="1M", today=TODAY)
    counts = dict(_counts(t))
    assert all(p.weekday() == 0 for p in counts)
    assert counts[date(2026, 10, 12)] == 2
    assert counts[date(2026, 10, 19)] == 0
    assert counts[date(2026, 10, 26)] == 1
    assert counts[date(2026, 11, 9)] == 0
    assert sum(counts.values()) == 3


def test_day_buckets() -> None:
    dates = [date(2026, 11, 1), date(2026, 11, 1), date(2026, 11, 3)]
    t = build_timeline(dates, granularity="day", range_key="1M", today=TODAY)
    counts = dict(_counts(t))
    assert counts[date(2026, 11, 1)] == 2
    assert counts[date(2026, 11, 2)] == 0
    assert counts[date(2026, 11, 3)] == 1
    assert max(counts) == TODAY
    assert min(counts) == TODAY - timedelta(days=30)


def test_month_buckets() -> None:
    dates = [date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 31), date(2026, 11, 2)]
    t = build_timeline(dates, granularity="month", range_key="6M", today=TODAY)
    counts = dict(_counts(t))
    assert all(p.day == 1 for p in counts)
    assert counts[date(2026, 7, 1)] == 2
    assert counts[date(2026, 8, 1)] == 0
    assert counts[date(2026, 11, 1)] == 1
    assert counts[date(2026, 6, 1)] == 1
    assert min(counts) == date(2026, 5, 1)  # 6M 從 2026-05-11 起，所在月份為 5 月


def test_future_dates_are_ignored() -> None:
    t = build_timeline([TODAY + timedelta(days=3)], granularity="day", range_key="1M", today=TODAY)
    assert sum(c for _, c in _counts(t)) == 0


# ---------- 移動平均 ----------


def test_moving_average_uses_current_and_previous_three_periods() -> None:
    assert MOVING_WINDOW == 4
    # 每週件數 1、2、3、4、5（最後一週是本週）
    dates = [
        TODAY - timedelta(weeks=4 - i) for i, n in enumerate([1, 2, 3, 4, 5]) for _ in range(n)
    ]
    t = build_timeline(dates, granularity="week", range_key="all", today=TODAY)
    points = t.points
    assert [p.count for p in points] == [1, 2, 3, 4, 5]
    assert [p.moving_average for p in points] == [None, None, None, 2.5, 3.5]


def test_moving_average_includes_periods_before_range_start() -> None:
    # 範圍 1M 的第一週也有完整的 4 期平均：前 3 期來自範圍之外
    start = range_start("1M", TODAY, None)
    first_week = start - timedelta(days=start.weekday())
    before = [first_week - timedelta(weeks=k) for k in (1, 2, 3) for _ in range(4)]
    t = build_timeline(before, granularity="week", range_key="1M", today=TODAY)
    assert t.points[0].period_start == first_week
    assert t.points[0].count == 0
    assert t.points[0].moving_average == 3.0  # (4 + 4 + 4 + 0) / 4
    assert sum(p.count for p in t.points) == 0  # 範圍外的件數不出現在長條


# ---------- 範圍 ----------


@pytest.mark.parametrize(("key", "days"), [("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)])
def test_range_start(key: str, days: int) -> None:
    assert range_start(key, TODAY, date(2020, 1, 1)) == TODAY - timedelta(days=days)  # type: ignore[arg-type]


def test_range_all_starts_at_first_event() -> None:
    assert range_start("all", TODAY, date(2025, 3, 4)) == date(2025, 3, 4)
    assert range_start("all", TODAY, None) == TODAY
    t = build_timeline([date(2025, 3, 4)], granularity="month", range_key="all", today=TODAY)
    assert t.points[0].period_start == date(2025, 3, 1)
    assert t.points[-1].period_start == date(2026, 11, 1)


def test_unknown_range_or_granularity_rejected() -> None:
    with pytest.raises(ValueError):
        build_timeline([], granularity="year", range_key="1M", today=TODAY)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_timeline([], granularity="week", range_key="2Y", today=TODAY)  # type: ignore[arg-type]


# ---------- 措施標記 ----------


def test_markers_only_within_range_and_sorted() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    markers = [
        ActionMarker(a, "加裝照明", "completed", date(2026, 11, 1)),
        ActionMarker(a, "加裝照明", "started", date(2026, 10, 20)),
        ActionMarker(b, "更換變電箱", "started", date(2025, 1, 1)),  # 範圍外
    ]
    t = build_timeline([], granularity="week", range_key="1M", today=TODAY, markers=markers)
    assert [(m.kind, m.on) for m in t.markers] == [
        ("started", date(2026, 10, 20)),
        ("completed", date(2026, 11, 1)),
    ]


# ---------- 前後比較 ----------


def _spread(start: date, weeks: int, per_week: int, *, before: bool) -> list[date]:
    sign = -1 if before else 1
    out = []
    for w in range(weeks):
        base = start + timedelta(weeks=sign * (w + 1) if before else w)
        out += [base + timedelta(days=1)] * per_week
    return out


def test_before_after_weekly_averages() -> None:
    assert (COMPARE_WEEKS, MIN_EVENTS) == (8, 5)
    start = date(2026, 6, 1)
    dates = _spread(start, 8, 2, before=True) + _spread(start, 8, 1, before=False)
    c = before_after(dates, start, today=TODAY)
    assert c.status == "ok"
    assert (c.before_events, c.after_events) == (16, 8)
    assert c.before_weekly_average == 2.0
    assert c.after_weekly_average == 1.0
    assert c.change_ratio == -0.5
    assert c.message is None
    assert c.caveat == CAVEAT
    assert "變少不一定是措施的功勞" in CAVEAT


def test_events_outside_windows_are_not_counted() -> None:
    start = date(2026, 6, 1)
    far = [start - timedelta(weeks=9)] * 10 + [start + timedelta(weeks=8)] * 10
    dates = far + [start - timedelta(days=1)] * 5 + [start] * 5
    c = before_after(dates, start, today=TODAY)
    assert (c.before_events, c.after_events) == (5, 5)


@pytest.mark.parametrize(("before", "after"), [(4, 10), (10, 4), (0, 0), (4, 4)])
def test_fewer_than_five_on_either_side_is_insufficient(before: int, after: int) -> None:
    start = date(2026, 6, 1)
    dates = [start - timedelta(days=3)] * before + [start + timedelta(days=3)] * after
    c = before_after(dates, start, today=TODAY)
    assert c.status == "insufficient"
    assert c.message == INSUFFICIENT_MESSAGE == "資料還不夠"
    assert c.before_weekly_average is None
    assert c.after_weekly_average is None
    assert c.change_ratio is None
    assert c.caveat == CAVEAT  # 資料不夠時也一律附上
    assert (c.before_events, c.after_events) == (before, after)


def test_exactly_five_each_side_is_enough() -> None:
    start = date(2026, 6, 1)
    dates = [start - timedelta(days=3)] * 5 + [start + timedelta(days=3)] * 5
    c = before_after(dates, start, today=TODAY)
    assert c.status == "ok"
    assert c.change_ratio == 0.0


def test_after_window_is_cut_at_today() -> None:
    start = TODAY - timedelta(weeks=2)
    dates = [start - timedelta(days=3)] * 8 + [start + timedelta(days=1)] * 6
    c = before_after(dates, start, today=TODAY)
    assert c.after_weeks == pytest.approx(15 / 7)  # 含今天
    assert c.after_weekly_average == pytest.approx(6 / (15 / 7))
    assert c.before_weekly_average == 1.0


def test_action_started_today_or_in_future_is_insufficient() -> None:
    dates = [TODAY - timedelta(days=3)] * 20
    c = before_after(dates, TODAY + timedelta(days=1), today=TODAY)
    assert c.status == "insufficient"
    assert c.after_events == 0
    assert c.caveat == CAVEAT


def test_same_input_same_result() -> None:
    start = date(2026, 6, 1)
    dates = _spread(start, 8, 2, before=True) + _spread(start, 8, 1, before=False)
    assert before_after(dates, start, today=TODAY) == before_after(
        list(reversed(dates)), start, today=TODAY
    )
