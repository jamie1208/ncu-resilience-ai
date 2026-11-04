"""P6-T05：校園韌性成績單 CRI（app/resilience/cri.py；G6-3 不假精確）。

規格 §15：CRI = 0.25 Detection + 0.25 Response + 0.25 Recovery + 0.25 Adaptation，各構面 0–100。
1. 發現延遲、回應延遲、恢復時間取中位數，依固定錨點線性換算；調適取改善完成率與
   「1 − 重複事件比例」的平均；
2. 任何指標樣本數不足 → 該構面 None（顯示 N/A），不以其他構面重新加權；四個構面齊全才有 CRI；
3. 分數只到整數；每個構面附樣本數與資料覆蓋率，報告附時間窗與來源限制；
4. 只用時間窗內、as_of 當下已知的資料（之後建立的事件與狀態變更不列入）；不實與類別 none 不列入。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.resilience.cri import (
    DIMENSIONS,
    LIMITATIONS,
    PUBLIC_LIMITATION,
    REPORTS_NOT_RISK,
    ActionRecord,
    CriParams,
    CriReport,
    Dimension,
    EventRecord,
    compute_cri,
    linear_score,
)

TAIPEI = ZoneInfo("Asia/Taipei")
AS_OF = date(2026, 10, 28)
LIB = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
DORM = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


def at(day: date, hour: int = 9) -> datetime:
    return datetime.combine(day, time(hour), TAIPEI)


def ev(
    days_ago: int,
    *,
    delay: int | None = 0,
    location: uuid.UUID | None = None,
    type: str = "facility",
    status: str = "verified",
    handled_after_hours: float | None = None,
) -> EventRecord:
    """發生於 AS_OF 前 days_ago 天、發生後 delay 天建立的事件。"""
    occurred = AS_OF - timedelta(days=days_ago)
    created = at(occurred + timedelta(days=delay or 0))
    return EventRecord(
        event_id=uuid.uuid4(),
        event_type=type,
        status=status,
        location_id=location,
        occurred_on=occurred if delay is not None else None,
        created_at=created,
        first_handling_at=(
            created + timedelta(hours=handled_after_hours)
            if handled_after_hours is not None
            else None
        ),
    )


def act(
    e: EventRecord,
    *,
    state: str = "improved",
    handling_after_hours: float | None = 1,
    recovered_after_days: float | None = 2,
) -> ActionRecord:
    handling = (
        e.created_at + timedelta(hours=handling_after_hours)
        if handling_after_hours is not None
        else None
    )
    recovered = (
        handling + timedelta(days=recovered_after_days)
        if handling is not None and recovered_after_days is not None
        else None
    )
    return ActionRecord(
        action_id=uuid.uuid4(),
        event_id=e.event_id,
        state=state,
        handling_at=handling,
        recovered_at=recovered,
    )


def full_data(n: int = 6) -> tuple[list[EventRecord], list[ActionRecord]]:
    """每個構面都有 n 筆樣本、表現理想的資料。"""
    events = [
        ev(10 + i, delay=0, location=LIB if i == 0 else uuid.uuid4(), handled_after_hours=1)
        for i in range(n)
    ]
    actions = [act(e) for e in events]
    return events, actions


def dim(report: CriReport, key: str) -> Dimension:
    return next(d for d in report.dimensions if d.key == key)


# --- 換算 -------------------------------------------------------------------------------


def test_linear_score_anchors() -> None:
    assert linear_score(0.5, best=1, worst=14) == 100
    assert linear_score(1, best=1, worst=14) == 100
    assert linear_score(14, best=1, worst=14) == 0
    assert linear_score(30, best=1, worst=14) == 0
    assert linear_score(7.5, best=1, worst=14) == pytest.approx(50)


def test_dimensions_and_weights_follow_spec() -> None:
    assert DIMENSIONS == ("detection", "response", "recovery", "adaptation")
    assert CriParams().weights == (0.25, 0.25, 0.25, 0.25)


# --- 不假精確（G6-3） ---------------------------------------------------------------------


def test_no_data_is_all_na() -> None:
    report = compute_cri([], [], as_of=AS_OF)
    assert report.score is None
    for d in report.dimensions:
        assert d.score is None
        assert d.sample_size == 0
        assert d.coverage is None
        assert d.reason == "insufficient_samples"
        for m in d.metrics:
            assert m.value is None
            assert m.score is None


def test_insufficient_samples_give_na_not_a_number() -> None:
    events, actions = full_data(4)  # 少於 min_samples=5
    report = compute_cri(events, actions, as_of=AS_OF)
    assert report.score is None
    for d in report.dimensions:
        assert d.score is None
        assert d.sample_size == 4
        assert d.reason == "insufficient_samples"


def test_missing_one_dimension_hides_overall_without_reweighting() -> None:
    events, actions = full_data(6)
    # 沒有任何措施恢復 → 恢復構面樣本為 0
    actions = [replace(a, recovered_at=None, state="handling") for a in actions]
    report = compute_cri(events, actions, as_of=AS_OF)
    assert dim(report, "recovery").score is None
    assert dim(report, "detection").score is not None
    assert dim(report, "response").score is not None
    assert report.score is None


def test_adaptation_needs_both_metrics() -> None:
    events, actions = full_data(6)
    # 只有 4 項措施（未達門檻）→ 完成率 N/A，調適構面 N/A，即使重複比例有分數
    report = compute_cri(events, actions[:4], as_of=AS_OF)
    adaptation = dim(report, "adaptation")
    completion, repeat = adaptation.metrics
    assert completion.key == "completion_rate" and completion.score is None
    assert repeat.key == "repeat_ratio" and repeat.score is not None
    assert adaptation.score is None
    assert report.score is None


def test_scores_are_integers_and_metrics_are_rounded() -> None:
    events, actions = full_data(6)
    events = [replace(e, created_at=e.created_at + timedelta(days=3)) for e in events]
    report = compute_cri(events, actions, as_of=AS_OF)
    assert isinstance(report.score, int)
    for d in report.dimensions:
        assert isinstance(d.score, int)
        assert d.coverage is not None and d.coverage == round(d.coverage, 2)
        for m in d.metrics:
            assert m.value is not None and m.value == round(m.value, 2)


def test_ideal_data_scores_full_marks() -> None:
    events, actions = full_data(6)
    report = compute_cri(events, actions, as_of=AS_OF)
    assert [d.score for d in report.dimensions] == [100, 100, 100, 100]
    assert report.score == 100


# --- 指標定義 ---------------------------------------------------------------------------


def test_detection_median_delay_and_coverage() -> None:
    delays = [0, 1, 2, 5, 9, 14, 20, None]  # 一件沒有發生日期
    events = [ev(25, delay=d) for d in delays]
    report = compute_cri(events, [], as_of=AS_OF)
    detection = dim(report, "detection")
    [metric] = detection.metrics
    assert metric.key == "delay_days"
    assert metric.value == 5  # 中位數
    assert detection.sample_size == 7
    assert detection.eligible == 8
    assert detection.coverage == round(7 / 8, 2)
    assert detection.score == round(linear_score(5, best=1, worst=14))


def test_created_before_occurrence_counts_as_zero_delay() -> None:
    events = [ev(20, delay=0) for _ in range(5)]
    early = [replace(e, created_at=e.created_at - timedelta(days=3)) for e in events]
    report = compute_cri(early, [], as_of=AS_OF)
    assert dim(report, "detection").metrics[0].value == 0


def test_response_uses_first_handling_and_coverage_counts_unhandled() -> None:
    handled = [ev(20, handled_after_hours=h) for h in (24, 48, 72, 96, 120)]
    unhandled = [ev(20) for _ in range(5)]
    report = compute_cri(handled + unhandled, [], as_of=AS_OF)
    response = dim(report, "response")
    assert response.metrics[0].value == 3  # 中位數 72 小時
    assert response.sample_size == 5
    assert response.eligible == 10
    assert response.coverage == 0.5
    assert response.score == round(linear_score(3, best=1, worst=30))


def test_recovery_only_counts_recovered_actions() -> None:
    base = [ev(60) for _ in range(7)]
    actions = [
        act(e, recovered_after_days=d) for e, d in zip(base, (3, 5, 10, 20, 40), strict=False)
    ]
    actions += [act(e, state="handling", recovered_after_days=None) for e in base[5:]]
    actions.append(act(base[0], state="discovered", handling_after_hours=None))
    report = compute_cri(base, actions, as_of=AS_OF)
    recovery = dim(report, "recovery")
    assert recovery.metrics[0].value == 10
    assert recovery.sample_size == 5
    assert recovery.eligible == 7  # 進入過處理的措施
    assert recovery.score == round(linear_score(10, best=3, worst=60))


def test_adaptation_completion_rate_and_repeat_ratio() -> None:
    events = [
        ev(20, location=LIB),
        ev(30, location=LIB),  # 同地點同類別 → 重複
        ev(25, location=LIB, type="security"),  # 同地點不同類別 → 不重複
        ev(20, location=DORM),
        ev(21, location=uuid.uuid4()),
        ev(22, location=uuid.uuid4()),
        ev(23),  # 地點不明，不列入重複比例
    ]
    actions = [
        act(e, state=s)
        for e, s in zip(events, ["improved"] * 3 + ["recovered"] * 2 + ["handling"], strict=False)
    ]
    actions.append(act(events[0], state="dropped"))  # 不追蹤的不列入完成率
    report = compute_cri(events, actions, as_of=AS_OF)
    completion, repeat = dim(report, "adaptation").metrics
    assert completion.value == 0.5
    assert completion.sample_size == 6
    assert completion.score == 50
    assert repeat.value == round(2 / 6, 2)
    assert repeat.sample_size == 6
    assert repeat.score == round(100 * (1 - 2 / 6))
    adaptation = dim(report, "adaptation")
    assert adaptation.score == round((50 + 100 * (1 - 2 / 6)) / 2)
    assert adaptation.sample_size == 6


def test_overall_is_weighted_mean_of_unrounded_dimensions() -> None:
    events = [
        ev(20 + i, delay=4, location=uuid.uuid4(), handled_after_hours=24 * 8) for i in range(6)
    ]
    actions = [
        act(e, recovered_after_days=10, state="improved" if i < 3 else "recovered")
        for i, e in enumerate(events)
    ]
    report = compute_cri(events, actions, as_of=AS_OF)
    expected = (
        linear_score(4, best=1, worst=14)
        + linear_score(8, best=1, worst=30)
        + linear_score(10, best=3, worst=60)
        + (50 + 100) / 2
    ) / 4
    assert report.score == round(expected)


# --- 範圍與時間 -------------------------------------------------------------------------


def test_false_none_and_out_of_window_events_are_excluded() -> None:
    events, actions = full_data(5)
    noise = [
        ev(10, status="false"),
        ev(10, type="none"),
        ev(200),  # 超出 180 天時間窗
    ]
    report = compute_cri(events + noise, actions, as_of=AS_OF)
    assert report.event_count == 5
    assert dim(report, "detection").eligible == 5
    assert report.window_start == AS_OF - timedelta(days=179)
    assert report.window_end == AS_OF


def test_future_records_after_as_of_are_ignored() -> None:
    events, actions = full_data(5)
    later = ev(-2)  # as_of 之後才發生
    late_created = replace(ev(5), created_at=at(AS_OF + timedelta(days=1)))
    # as_of 之後才恢復的措施：視為尚未恢復
    pending = [replace(a, recovered_at=at(AS_OF + timedelta(days=3))) for a in actions]
    report = compute_cri([*events, later, late_created], pending, as_of=AS_OF)
    assert report.event_count == 5
    assert dim(report, "recovery").sample_size == 0
    assert dim(report, "recovery").score is None


def test_actions_of_excluded_events_do_not_count() -> None:
    events, actions = full_data(5)
    stray = ev(10, status="false")
    report = compute_cri(events, [*actions, act(stray)], as_of=AS_OF)
    assert report.action_count == 5


def test_min_samples_and_window_are_parameters() -> None:
    events, actions = full_data(3)
    params = CriParams(min_samples=3, window_days=30)
    report = compute_cri(events, actions, as_of=AS_OF, params=params)
    assert report.score is not None
    assert report.min_samples == 3
    assert report.window_start == AS_OF - timedelta(days=29)


def test_params_validate_anchors() -> None:
    with pytest.raises(ValueError):
        CriParams(detection_anchor=(14, 1))
    with pytest.raises(ValueError):
        CriParams(min_samples=0)
    with pytest.raises(ValueError):
        CriParams(window_days=0)


# --- 限制說明 ---------------------------------------------------------------------------


def test_limitations_state_reports_are_not_risk() -> None:
    report = compute_cri([], [], as_of=AS_OF)
    assert REPORTS_NOT_RISK in report.limitations
    assert "回報變多不等於校園變危險" in REPORTS_NOT_RISK
    assert report.limitations == LIMITATIONS
    assert PUBLIC_LIMITATION not in report.limitations
    public = compute_cri([], [], as_of=AS_OF, published_only=True)
    assert public.limitations == (*LIMITATIONS, PUBLIC_LIMITATION)
    assert public.scope == "published"
    assert report.scope == "all"
