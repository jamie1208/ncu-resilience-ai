"""P8-T03：再發生偵測依代表地點分組（ADR-0018 決策 1-5）。

- 別名（`canonical_id`）的事件併入代表地點一起判斷，提醒顯示代表地點的名稱與 id；
- 別名的別名沿著 `canonical_id` 找到最上層的代表地點；循環或不在清單中的地點維持自己；
- 上層地點（`parent_id`）只用於篩選，不併入計數；
- `config/recurrence.yaml` 的門檻不變。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.analytics.timeseries import LocationRef
from app.resilience.recurrence import (
    WITHIN_WINDOW,
    EventOccurrence,
    RecurrencePolicy,
    canonicalize,
    detect,
    load_policy,
)

TODAY = date(2026, 11, 5)
LIB = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
LIB_ALIAS = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
LIB_ALIAS2 = uuid.UUID("00000000-0000-0000-0000-0000000000b3")
DORM_AREA = uuid.UUID("00000000-0000-0000-0000-0000000000b4")
DORM_7 = uuid.UUID("00000000-0000-0000-0000-0000000000b5")
LOOP_A = uuid.UUID("00000000-0000-0000-0000-0000000000b6")
LOOP_B = uuid.UUID("00000000-0000-0000-0000-0000000000b7")
UNLISTED = uuid.UUID("00000000-0000-0000-0000-0000000000b8")

LOCATIONS = {
    LIB: LocationRef(LIB, "總圖書館"),
    LIB_ALIAS: LocationRef(LIB_ALIAS, "總圖", canonical_id=LIB),
    LIB_ALIAS2: LocationRef(LIB_ALIAS2, "圖書館", canonical_id=LIB_ALIAS),
    DORM_AREA: LocationRef(DORM_AREA, "宿舍區"),
    DORM_7: LocationRef(DORM_7, "女十四舍", parent_id=DORM_AREA),
    LOOP_A: LocationRef(LOOP_A, "甲", canonical_id=LOOP_B),
    LOOP_B: LocationRef(LOOP_B, "乙", canonical_id=LOOP_A),
}


@pytest.fixture(scope="module")
def policy() -> RecurrencePolicy:
    return load_policy()


_counter = iter(range(1, 10_000))


def occ(days_ago: int, location_id: uuid.UUID | None, name: str | None) -> EventOccurrence:
    return EventOccurrence(
        event_id=uuid.UUID(int=next(_counter)),
        title="停電",
        event_type="facility",
        status="unverified",
        location_id=location_id,
        location_name=name,
        occurred_on=TODAY - timedelta(days=days_ago),
        improved_on=None,
    )


def test_policy_thresholds_unchanged(policy: RecurrencePolicy) -> None:
    assert (policy.window_days, policy.min_events) == (30, 2)


def test_alias_events_are_grouped_under_representative(policy: RecurrencePolicy) -> None:
    events = [occ(1, LIB_ALIAS, "總圖"), occ(3, LIB, "總圖書館")]
    assert detect(events, policy, today=TODAY) == []

    found = detect(canonicalize(events, LOCATIONS), policy, today=TODAY)
    assert len(found) == 1
    assert found[0].location_id == LIB
    assert found[0].location_name == "總圖書館"
    assert found[0].reasons == (WITHIN_WINDOW,)
    assert {e.event_id for e in found[0].events} == {e.event_id for e in events}


def test_two_aliases_without_representative_event_are_grouped(policy: RecurrencePolicy) -> None:
    events = [occ(1, LIB_ALIAS, "總圖"), occ(2, LIB_ALIAS2, "圖書館")]
    found = detect(canonicalize(events, LOCATIONS), policy, today=TODAY)
    assert [(r.location_id, r.location_name) for r in found] == [(LIB, "總圖書館")]


def test_parent_is_not_merged(policy: RecurrencePolicy) -> None:
    events = [occ(1, DORM_7, "女十四舍"), occ(2, DORM_AREA, "宿舍區")]
    assert detect(canonicalize(events, LOCATIONS), policy, today=TODAY) == []


def test_cycle_and_unlisted_locations_stay_themselves() -> None:
    events = [occ(1, LOOP_A, "甲"), occ(1, UNLISTED, "未列"), occ(1, None, None)]
    mapped = canonicalize(events, LOCATIONS)
    assert [(e.location_id, e.location_name) for e in mapped] == [
        (LOOP_A, "甲"),
        (UNLISTED, "未列"),
        (None, None),
    ]


def test_canonicalize_keeps_other_fields() -> None:
    original = replace(occ(4, LIB_ALIAS, "總圖"), improved_on=TODAY - timedelta(days=2))
    (mapped,) = canonicalize([original], LOCATIONS)
    assert mapped == replace(original, location_id=LIB, location_name="總圖書館")


def test_canonicalize_is_identity_without_aliases(policy: RecurrencePolicy) -> None:
    events = [occ(1, LIB, "總圖書館"), occ(2, LIB, "總圖書館")]
    assert canonicalize(events, {}) == events
    assert detect(canonicalize(events, LOCATIONS), policy, today=TODAY) == detect(
        events, policy, today=TODAY
    )
