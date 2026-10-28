"""P6-T03：時序統計與異常偵測（app/analytics/timeseries.py）。

1. 週（ISO 週一起算）／月分桶，期間內沒有事件的週期補 0；
2. 地點分布依件數排序，地點不明合併為一組；
3. EWMA 平滑；
4. Poisson 變化偵測：每個週期的期望值只用之前的週期（不看當期與未來），
   合成資料注入突增可被偵測，平穩資料不誤報，歷史不足或件數太少不判定。
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from app.analytics.timeseries import (
    UNKNOWN_LOCATION,
    CountRow,
    DetectionParams,
    EventPoint,
    bucket,
    count_series,
    detect_changes,
    ewma,
    location_distribution,
    period_start,
    periods,
    poisson_sf,
)

START = date(2026, 1, 5)  # 週一


def pt(day: date, event_type: str = "theft", location: str | None = "總圖書館") -> EventPoint:
    return EventPoint(occurred_on=day, event_type=event_type, location_name=location)


# ---------- 分桶 ----------


def test_period_start_week_is_iso_monday() -> None:
    assert period_start(date(2026, 1, 11), "week") == date(2026, 1, 5)  # 週日 → 當週週一
    assert period_start(date(2026, 1, 5), "week") == date(2026, 1, 5)
    assert period_start(date(2026, 1, 1), "week") == date(2025, 12, 29)  # 跨年


def test_period_start_month() -> None:
    assert period_start(date(2026, 2, 28), "month") == date(2026, 2, 1)
    assert period_start(date(2026, 12, 31), "month") == date(2026, 12, 1)


def test_unknown_frequency_rejected() -> None:
    with pytest.raises(ValueError):
        period_start(date(2026, 1, 1), "day")  # type: ignore[arg-type]


def test_periods_inclusive_and_cross_year() -> None:
    assert periods(date(2025, 11, 15), date(2026, 2, 1), "month") == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert periods(date(2026, 1, 7), date(2026, 1, 20), "week") == [
        date(2026, 1, 5),
        date(2026, 1, 12),
        date(2026, 1, 19),
    ]
    with pytest.raises(ValueError):
        periods(date(2026, 2, 1), date(2026, 1, 1), "week")


def test_bucket_groups_by_period_type_location() -> None:
    rows = bucket(
        [
            pt(date(2026, 1, 5)),
            pt(date(2026, 1, 9)),
            pt(date(2026, 1, 9), location=None),
            pt(date(2026, 1, 9), event_type="facility"),
            pt(date(2026, 1, 12)),
        ],
        "week",
    )
    assert set(rows) == {
        CountRow(date(2026, 1, 5), "theft", "總圖書館", 2),
        CountRow(date(2026, 1, 5), "theft", None, 1),
        CountRow(date(2026, 1, 5), "facility", "總圖書館", 1),
        CountRow(date(2026, 1, 12), "theft", "總圖書館", 1),
    }


# ---------- 序列與分布 ----------


def test_count_series_zero_fills_and_filters() -> None:
    rows = bucket(
        [
            pt(date(2026, 1, 6)),
            pt(date(2026, 1, 6), event_type="facility"),
            pt(date(2026, 1, 20), location="宿舍"),
        ],
        "week",
    )
    all_types = count_series(rows, "week", date(2026, 1, 5), date(2026, 1, 25))
    assert [(p.period_start, p.count) for p in all_types] == [
        (date(2026, 1, 5), 2),
        (date(2026, 1, 12), 0),
        (date(2026, 1, 19), 1),
    ]
    theft = count_series(rows, "week", date(2026, 1, 5), date(2026, 1, 25), event_type="theft")
    assert [p.count for p in theft] == [1, 0, 1]
    dorm = count_series(rows, "week", date(2026, 1, 5), date(2026, 1, 25), location="宿舍")
    assert [p.count for p in dorm] == [0, 0, 1]


def test_count_series_excludes_rows_outside_range() -> None:
    rows = bucket([pt(date(2025, 12, 1)), pt(date(2026, 3, 1)), pt(date(2026, 1, 6))], "month")
    series = count_series(rows, "month", date(2026, 1, 1), date(2026, 2, 28))
    assert [(p.period_start, p.count) for p in series] == [
        (date(2026, 1, 1), 1),
        (date(2026, 2, 1), 0),
    ]


def test_location_distribution_sorted_with_unknown_and_shares() -> None:
    rows = bucket(
        [
            pt(date(2026, 1, 6), location="宿舍"),
            pt(date(2026, 1, 7), location="總圖書館"),
            pt(date(2026, 1, 8), location="總圖書館"),
            pt(date(2026, 1, 9), location=None),
            pt(date(2026, 1, 9), event_type="facility", location="宿舍"),
            pt(date(2026, 3, 1), location="宿舍"),  # 範圍外
        ],
        "week",
    )
    dist = location_distribution(rows, date(2026, 1, 5), date(2026, 1, 31), event_type="theft")
    assert [(d.location, d.count) for d in dist] == [
        ("總圖書館", 2),
        ("宿舍", 1),
        (UNKNOWN_LOCATION, 1),
    ]
    assert sum(d.share for d in dist) == pytest.approx(1.0)
    assert dist[0].share == pytest.approx(0.5)
    assert location_distribution(rows, date(2026, 6, 1), date(2026, 6, 30)) == []


# ---------- EWMA ----------


def test_ewma_recurrence() -> None:
    assert ewma([], 0.5) == []
    assert ewma([4.0, 0.0, 2.0], 0.5) == pytest.approx([4.0, 2.0, 2.0])
    assert ewma([3.0, 3.0, 3.0], 0.3) == pytest.approx([3.0, 3.0, 3.0])


@pytest.mark.parametrize("alpha", [0.0, -0.1, 1.1])
def test_ewma_alpha_range(alpha: float) -> None:
    with pytest.raises(ValueError):
        ewma([1.0], alpha)


# ---------- Poisson ----------


def test_poisson_sf_matches_closed_form() -> None:
    lam = 2.5
    expected = 1 - sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(5))
    assert poisson_sf(5, lam) == pytest.approx(expected)
    assert poisson_sf(0, lam) == 1.0
    assert poisson_sf(80, 3.0) < 1e-60
    with pytest.raises(ValueError):
        poisson_sf(3, 0.0)


def _series(counts: list[int]) -> list[tuple[date, int]]:
    return [(START + timedelta(weeks=i), c) for i, c in enumerate(counts)]


def _synthetic(weeks: int, lam: float, seed: int) -> list[int]:
    """固定種子的 Poisson 合成週次件數。"""
    rng = random.Random(seed)  # noqa: S311 — 固定種子的合成資料
    out = []
    for _ in range(weeks):
        # Knuth 演算法，避免依賴 numpy
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                break
            k += 1
        out.append(k)
    return out


@pytest.mark.parametrize("seed", range(20))
def test_injected_spike_is_detected(seed: int) -> None:
    counts = _synthetic(30, 2.0, seed=seed)
    spike_at = 22
    counts[spike_at] += 10
    anomalies = detect_changes(_series(counts))
    hit = next(a for a in anomalies if a.period_start == START + timedelta(weeks=spike_at))
    assert hit.observed == counts[spike_at]
    assert hit.expected < hit.observed
    assert hit.p_value < DetectionParams().p_threshold


def test_stationary_series_false_alarm_rate_is_low() -> None:
    """平穩 Poisson(2) 資料 50 條 × 52 週，被判為突增的週期不超過 0.5%。"""
    params = DetectionParams()
    judged = flagged = 0
    for seed in range(50):
        counts = _synthetic(52, 2.0, seed=1000 + seed)
        judged += len(counts) - params.min_history
        flagged += len(detect_changes(_series(counts), params))
    assert flagged / judged <= 0.005


def test_expected_uses_only_past_periods() -> None:
    """當期與未來週期的值改變，不影響之前週期的期望值與判定。"""
    base = [2, 3, 2, 2, 3, 2, 2, 12, 2, 3]
    changed = [*base[:7], 12, 50, 0]
    a = detect_changes(_series(base))
    b = detect_changes(_series(changed))
    assert [x for x in a if x.period_start <= START + timedelta(weeks=7)] == [
        x for x in b if x.period_start <= START + timedelta(weeks=7)
    ]
    params = DetectionParams()
    flagged = next(x for x in a if x.period_start == START + timedelta(weeks=7))
    assert flagged.expected == pytest.approx(ewma([float(c) for c in base[:7]], params.alpha)[-1])


def test_insufficient_history_not_judged() -> None:
    params = DetectionParams(min_history=4)
    assert detect_changes(_series([1, 1, 1, 30]), params) == []
    assert detect_changes(_series([1, 1, 1, 1, 30]), params) != []


def test_small_counts_not_flagged_even_from_zero_baseline() -> None:
    """長期 0 件後出現 2 件不算異常（min_count），期望值有下限不會讓 p 值變成 0。"""
    assert detect_changes(_series([0] * 12 + [2])) == []
    flagged = detect_changes(_series([0] * 12 + [6]))
    assert len(flagged) == 1
    assert flagged[0].expected == pytest.approx(DetectionParams().min_expected)
    assert flagged[0].p_value > 0


def test_series_must_be_ordered_and_unique() -> None:
    with pytest.raises(ValueError):
        detect_changes([(START + timedelta(weeks=1), 1), (START, 1)])
    with pytest.raises(ValueError):
        detect_changes([(START, 1), (START, 2)])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"alpha": 0.0},
        {"min_history": 0},
        {"p_threshold": 0.0},
        {"p_threshold": 1.0},
        {"min_count": 0},
        {"min_expected": 0.0},
    ],
)
def test_detection_params_validated(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        DetectionParams(**kwargs)  # type: ignore[arg-type]
