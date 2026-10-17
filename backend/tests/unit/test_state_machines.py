"""P0-T06：狀態機（規格 §6.3 與 2026-10-17 使用者決定）。

預期的合法轉移在此獨立寫出，不從實作讀取；窮舉所有 (from, to) 組合，
清單外的轉移一律必須拋出 StateTransitionError。
"""

from __future__ import annotations

from collections import deque
from enum import StrEnum
from typing import Any

import pytest

from app.domain.states import (
    INITIAL,
    AlertState,
    EventStatus,
    StateTransitionError,
    VerificationStatus,
    allowed_targets,
    can_transition,
    is_terminal,
    transition,
)

E = EventStatus
V = VerificationStatus
A = AlertState

JUDGED = {"verified", "disputed", "false"}

EXPECTED_EVENT: dict[str, set[str]] = {
    "detected": {"analyzing"},
    "analyzing": {"unverified"},
    # 查證：可改判（使用者決定），可直接封存
    "unverified": JUDGED | {"archived"},
    "verified": {"unverified", "disputed", "false", "monitoring", "resolved", "archived"},
    "disputed": {"unverified", "verified", "false", "monitoring", "resolved", "archived"},
    "false": {"unverified", "verified", "disputed", "monitoring", "resolved", "archived"},
    # 監看中可因新證據改判為 disputed／false
    "monitoring": {"resolved", "archived", "disputed", "false"},
    # 已解決可重新開啟（使用者決定）
    "resolved": {"monitoring", "archived"},
    "archived": set(),
}

EXPECTED_VERIFICATION: dict[str, set[str]] = {
    s: {"unverified", "verified", "disputed", "false"} - {s}
    for s in ("unverified", "verified", "disputed", "false")
}

EXPECTED_ALERT: dict[str, set[str]] = {
    "candidate": {"policy_checked"},
    # 規則阻擋為 blocked（使用者決定）
    "policy_checked": {"eligible", "blocked"},
    "eligible": {"published"},
    "published": {"updated", "retracted", "expired"},
    # 可多次改版
    "updated": {"updated", "retracted", "expired"},
    "blocked": set(),
    "retracted": set(),
    "expired": set(),
}

MACHINES: list[tuple[type[StrEnum], dict[str, set[str]]]] = [
    (EventStatus, EXPECTED_EVENT),
    (VerificationStatus, EXPECTED_VERIFICATION),
    (AlertState, EXPECTED_ALERT),
]


def _pairs() -> list[tuple[StrEnum, StrEnum, bool]]:
    out = []
    for enum, expected in MACHINES:
        for a in enum:
            for b in enum:
                out.append((a, b, b.value in expected[a.value]))
    return out


# --- 列舉值（資料庫以字串儲存，值不可任意更動）---------------------------------------


def test_enum_values_match_spec() -> None:
    assert {s.value for s in EventStatus} == set(EXPECTED_EVENT)
    assert {s.value for s in VerificationStatus} == set(EXPECTED_VERIFICATION)
    assert {s.value for s in AlertState} == set(EXPECTED_ALERT)


def test_verification_values_are_event_values() -> None:
    assert {s.value for s in VerificationStatus} <= {s.value for s in EventStatus}


def test_enums_are_strings() -> None:
    assert E.FALSE == "false"
    assert A.POLICY_CHECKED == "policy_checked"


# --- 轉移表：窮舉所有組合 ------------------------------------------------------------


@pytest.mark.parametrize(("current", "target", "legal"), _pairs())
def test_every_pair_matches_expected(current: StrEnum, target: StrEnum, legal: bool) -> None:
    assert can_transition(current, target) is legal
    if legal:
        assert transition(current, target) is target
    else:
        with pytest.raises(StateTransitionError):
            transition(current, target)


@pytest.mark.parametrize(("enum", "expected"), MACHINES)
def test_allowed_targets_match_expected(enum: type[StrEnum], expected: dict[str, set[str]]) -> None:
    for s in enum:
        assert {t.value for t in allowed_targets(s)} == expected[s.value]


# --- 結構性質 -----------------------------------------------------------------------


def test_initial_states() -> None:
    assert INITIAL[EventStatus] is E.DETECTED
    assert INITIAL[VerificationStatus] is V.UNVERIFIED
    assert INITIAL[AlertState] is A.CANDIDATE


@pytest.mark.parametrize(("enum", "expected"), MACHINES)
def test_every_state_reachable_from_initial(
    enum: type[StrEnum], expected: dict[str, set[str]]
) -> None:
    start = INITIAL[enum]
    seen = {start}
    queue = deque([start])
    while queue:
        for nxt in allowed_targets(queue.popleft()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    assert seen == set(enum)


def test_terminal_states() -> None:
    assert {s for s in EventStatus if is_terminal(s)} == {E.ARCHIVED}
    assert {s for s in AlertState if is_terminal(s)} == {A.BLOCKED, A.RETRACTED, A.EXPIRED}
    assert not any(is_terminal(s) for s in VerificationStatus)


# --- 錯誤處理 -----------------------------------------------------------------------


def test_error_carries_states_and_message() -> None:
    with pytest.raises(StateTransitionError) as info:
        transition(E.ARCHIVED, E.MONITORING)
    err = info.value
    assert err.current is E.ARCHIVED
    assert err.target is E.MONITORING
    assert "archived" in str(err)
    assert "monitoring" in str(err)


def test_mixing_machines_is_rejected() -> None:
    # EventStatus.VERIFIED 與 VerificationStatus.VERIFIED 值相同，但屬於不同狀態機
    # 型別上兩者都是 StrEnum，執行期才依狀態機類型拒絕
    with pytest.raises(StateTransitionError):
        transition(E.UNVERIFIED, V.VERIFIED)
    assert can_transition(V.UNVERIFIED, E.VERIFIED) is False


def test_plain_strings_are_rejected() -> None:
    # 刻意繞過型別檢查，模擬從資料庫讀出未轉換的字串
    current: Any = "detected"
    target: Any = "analyzing"
    with pytest.raises(StateTransitionError):
        transition(current, target)
