"""P4-T10：再發生偵測（app/resilience/recurrence.py、config/recurrence.yaml；G4-6）。

確定性規則，不呼叫模型：
1. 同地點、同類別的事件在時間窗（window_days）內達到門檻（min_events）→ 提醒
   「可能不是單純個案，要不要整批檢查？」；
2. 已有改善措施達到「改進」（improved）的事件，之後同地點、同類別再次發生 → 標示 after_improvement；
3. 不同地點或不同類別不會被湊成一組；地點不明、查證為不實（false）的事件不列入；
4. 只顯示最近一次發生在 active_days 內的組，避免舊紀錄一直佔著通知中心。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.resilience.recurrence import (
    AFTER_IMPROVEMENT,
    REMINDER,
    WITHIN_WINDOW,
    EventOccurrence,
    RecurrencePolicy,
    detect,
    load_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
POLICY_PATH = REPO_ROOT / "config" / "recurrence.yaml"
TODAY = date(2026, 10, 27)
LIB = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
DORM = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


@pytest.fixture(scope="module")
def policy() -> RecurrencePolicy:
    return load_policy()


_counter = iter(range(1, 10_000))


def occ(days_ago: int, **overrides: object) -> EventOccurrence:
    """合成事件：預設在總圖發生的設施事件。"""
    base = EventOccurrence(
        event_id=uuid.UUID(int=next(_counter)),
        title="總圖停電",
        event_type="facility",
        status="unverified",
        location_id=LIB,
        location_name="總圖書館",
        occurred_on=TODAY - timedelta(days=days_ago),
        improved_on=None,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --- 設定檔 ---


def test_policy_file_loads(policy: RecurrencePolicy) -> None:
    assert policy.policy_version
    assert policy.window_days >= 1
    assert policy.min_events >= 2
    assert policy.active_days >= 1
    assert policy.after_improvement_days >= policy.window_days


def test_policy_rejects_single_event_threshold() -> None:
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw["min_events"] = 1
    with pytest.raises(ValidationError):
        RecurrencePolicy.model_validate(raw)


def test_policy_rejects_unknown_keys() -> None:
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw["use_model"] = True
    with pytest.raises(ValidationError):
        RecurrencePolicy.model_validate(raw)


# --- 時間窗內再發生 ---


def test_recurrence_within_window_is_reminded(policy: RecurrencePolicy) -> None:
    first = occ(policy.window_days - 1)
    second = occ(0)
    [group] = detect([first, second], policy, today=TODAY)
    assert group.reasons == (WITHIN_WINDOW,)
    assert group.message == REMINDER == "可能不是單純個案，要不要整批檢查？"
    assert group.location_id == LIB
    assert group.event_type == "facility"
    assert [e.event_id for e in group.events] == [second.event_id, first.event_id]
    assert group.latest_on == TODAY
    assert group.policy_version == policy.policy_version


def test_single_event_is_not_reminded(policy: RecurrencePolicy) -> None:
    assert detect([occ(0)], policy, today=TODAY) == []


def test_outside_window_is_not_reminded(policy: RecurrencePolicy) -> None:
    events = [occ(policy.window_days + 1), occ(0)]
    assert detect(events, policy, today=TODAY) == []


def test_different_location_is_not_reminded(policy: RecurrencePolicy) -> None:
    events = [occ(3), occ(0, location_id=DORM, location_name="女十四舍")]
    assert detect(events, policy, today=TODAY) == []


def test_different_type_is_not_reminded(policy: RecurrencePolicy) -> None:
    events = [occ(3), occ(0, event_type="security", title="總圖腳踏車失竊")]
    assert detect(events, policy, today=TODAY) == []


def test_unknown_location_is_ignored(policy: RecurrencePolicy) -> None:
    events = [occ(3, location_id=None, location_name=None), occ(0, location_id=None)]
    assert detect(events, policy, today=TODAY) == []


def test_false_events_do_not_count(policy: RecurrencePolicy) -> None:
    events = [occ(3, status="false"), occ(0)]
    assert detect(events, policy, today=TODAY) == []


def test_non_event_type_is_ignored(policy: RecurrencePolicy) -> None:
    events = [occ(3, event_type="none"), occ(0, event_type="none")]
    assert detect(events, policy, today=TODAY) == []


def test_stale_group_is_not_shown(policy: RecurrencePolicy) -> None:
    oldest = policy.active_days + 5
    events = [occ(oldest), occ(oldest - 1)]
    assert detect(events, policy, today=TODAY) == []


def test_window_counts_back_from_latest_event(policy: RecurrencePolicy) -> None:
    """只把最近一次往回一個時間窗內的事件列入；更早的同類事件不湊進這一組。"""
    old = occ(policy.window_days * 3)
    first = occ(policy.window_days - 1)
    latest = occ(0)
    [group] = detect([old, first, latest], policy, today=TODAY)
    assert {e.event_id for e in group.events} == {first.event_id, latest.event_id}


def test_min_events_threshold(policy: RecurrencePolicy) -> None:
    strict = policy.model_copy(update={"min_events": 3})
    assert detect([occ(2), occ(0)], strict, today=TODAY) == []
    [group] = detect([occ(4), occ(2), occ(0)], strict, today=TODAY)
    assert len(group.events) == 3


def test_groups_are_separate_and_sorted(policy: RecurrencePolicy) -> None:
    lib = [occ(5), occ(4)]
    dorm = [occ(1, location_id=DORM, location_name="女十四舍"), occ(0, location_id=DORM)]
    groups = detect([*lib, *dorm], policy, today=TODAY)
    assert [g.location_id for g in groups] == [DORM, LIB]


def test_detect_is_deterministic(policy: RecurrencePolicy) -> None:
    events = [occ(2), occ(1), occ(0, location_id=DORM), occ(1, location_id=DORM)]
    assert detect(events, policy, today=TODAY) == detect(events[::-1], policy, today=TODAY)


def test_location_name_is_pii_masked(policy: RecurrencePolicy) -> None:
    name = "總圖 0912-345-678"
    [group] = detect([occ(1, location_name=name), occ(0, location_name=name)], policy, today=TODAY)
    assert group.location_name is not None
    assert "0912-345-678" not in group.location_name


# --- 改善後再發生 ---


def test_recurrence_after_improvement_is_marked(policy: RecurrencePolicy) -> None:
    improved = occ(120, improved_on=TODAY - timedelta(days=60))
    again = occ(0)
    [group] = detect([improved, again], policy, today=TODAY)
    assert group.reasons == (AFTER_IMPROVEMENT,)
    assert {e.event_id for e in group.events} == {improved.event_id, again.event_id}
    [marked] = [e for e in group.events if e.event_id == improved.event_id]
    assert marked.improved_on == TODAY - timedelta(days=60)


def test_both_rules_can_apply(policy: RecurrencePolicy) -> None:
    improved = occ(5, improved_on=TODAY - timedelta(days=3))
    again = occ(0)
    [group] = detect([improved, again], policy, today=TODAY)
    assert group.reasons == (WITHIN_WINDOW, AFTER_IMPROVEMENT)


def test_event_before_improvement_is_not_after_improvement(policy: RecurrencePolicy) -> None:
    """改進之前（或同一天）發生的事件不算「改善後再發生」。"""
    earlier = occ(policy.window_days + 10)
    improved = occ(policy.window_days + 6, improved_on=TODAY - timedelta(days=5))
    same_day = occ(5)
    groups = detect([earlier, improved, same_day], policy, today=TODAY)
    assert groups == []


def test_improvement_elsewhere_is_not_marked(policy: RecurrencePolicy) -> None:
    improved = occ(120, location_id=DORM, improved_on=TODAY - timedelta(days=60))
    assert detect([improved, occ(0)], policy, today=TODAY) == []


def test_improvement_too_long_ago_is_not_marked(policy: RecurrencePolicy) -> None:
    long_ago = TODAY - timedelta(days=policy.after_improvement_days + 1)
    improved = occ(policy.after_improvement_days + 10, improved_on=long_ago)
    assert detect([improved, occ(0)], policy, today=TODAY) == []
