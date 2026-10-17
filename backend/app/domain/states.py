"""事件、查證與警示的狀態機（規格 §6.3；細節見 docs/architecture/state-machines.md）。

所有狀態變更都必須經過 `transition()`；不在轉移表中的轉移一律拋出 StateTransitionError，
包含自我轉移（唯一例外是警示的 updated → updated，代表再次改版）。
狀態值以字串存入資料庫，已上線的值不可改名。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import overload


class EventStatus(StrEnum):
    """事件生命週期：偵測 → 分析 → 查證 → 後續追蹤。"""

    DETECTED = "detected"
    ANALYZING = "analyzing"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    FALSE = "false"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class VerificationStatus(StrEnum):
    """查證結果（S05 evidence-verifier 的輸出；`confirmed` 統一為 `verified`）。"""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    FALSE = "false"


class AlertState(StrEnum):
    """警示生命週期；`blocked` 為發布規則阻擋（規格 §6.3 之外新增，見 state-machines.md）。"""

    CANDIDATE = "candidate"
    POLICY_CHECKED = "policy_checked"
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    PUBLISHED = "published"
    UPDATED = "updated"
    RETRACTED = "retracted"
    EXPIRED = "expired"


_E = EventStatus
_V = VerificationStatus
_A = AlertState

_JUDGED = frozenset({_E.VERIFIED, _E.DISPUTED, _E.FALSE})
_AFTER_JUDGEMENT = frozenset({_E.MONITORING, _E.RESOLVED, _E.ARCHIVED})

EVENT_TRANSITIONS: Mapping[EventStatus, frozenset[EventStatus]] = {
    _E.DETECTED: frozenset({_E.ANALYZING}),
    _E.ANALYZING: frozenset({_E.UNVERIFIED}),
    _E.UNVERIFIED: _JUDGED | {_E.ARCHIVED},
    # 查證結果可因新證據改判（例：官方澄清後 verified → false）
    _E.VERIFIED: (_JUDGED - {_E.VERIFIED}) | {_E.UNVERIFIED} | _AFTER_JUDGEMENT,
    _E.DISPUTED: (_JUDGED - {_E.DISPUTED}) | {_E.UNVERIFIED} | _AFTER_JUDGEMENT,
    _E.FALSE: (_JUDGED - {_E.FALSE}) | {_E.UNVERIFIED} | _AFTER_JUDGEMENT,
    _E.MONITORING: frozenset({_E.RESOLVED, _E.ARCHIVED, _E.DISPUTED, _E.FALSE}),
    # 已解決的事件再次發生時可重新開啟
    _E.RESOLVED: frozenset({_E.MONITORING, _E.ARCHIVED}),
    _E.ARCHIVED: frozenset(),
}

VERIFICATION_TRANSITIONS: Mapping[VerificationStatus, frozenset[VerificationStatus]] = {
    s: frozenset(VerificationStatus) - {s} for s in VerificationStatus
}

ALERT_TRANSITIONS: Mapping[AlertState, frozenset[AlertState]] = {
    _A.CANDIDATE: frozenset({_A.POLICY_CHECKED}),
    _A.POLICY_CHECKED: frozenset({_A.ELIGIBLE, _A.BLOCKED}),
    _A.ELIGIBLE: frozenset({_A.PUBLISHED}),
    _A.PUBLISHED: frozenset({_A.UPDATED, _A.RETRACTED, _A.EXPIRED}),
    _A.UPDATED: frozenset({_A.UPDATED, _A.RETRACTED, _A.EXPIRED}),
    _A.BLOCKED: frozenset(),
    _A.RETRACTED: frozenset(),
    _A.EXPIRED: frozenset(),
}

MACHINES: tuple[type[StrEnum], ...] = (EventStatus, VerificationStatus, AlertState)

INITIAL: Mapping[type[StrEnum], StrEnum] = {
    EventStatus: EventStatus.DETECTED,
    VerificationStatus: VerificationStatus.UNVERIFIED,
    AlertState: AlertState.CANDIDATE,
}


class StateTransitionError(ValueError):
    """非法的狀態轉移。"""

    def __init__(self, current: object, target: object) -> None:
        self.current = current
        self.target = target
        machine = type(current).__name__
        super().__init__(f"{machine} 不允許從 {current!s} 轉移到 {target!s}")


@overload
def allowed_targets(current: EventStatus) -> frozenset[EventStatus]: ...
@overload
def allowed_targets(current: VerificationStatus) -> frozenset[VerificationStatus]: ...
@overload
def allowed_targets(current: AlertState) -> frozenset[AlertState]: ...
@overload
def allowed_targets(current: StrEnum) -> frozenset[StrEnum]: ...
def allowed_targets(current: StrEnum) -> frozenset[StrEnum]:
    """回傳 `current` 可轉移到的狀態。"""
    if isinstance(current, EventStatus):
        return EVENT_TRANSITIONS[current]
    if isinstance(current, VerificationStatus):
        return VERIFICATION_TRANSITIONS[current]
    if isinstance(current, AlertState):
        return ALERT_TRANSITIONS[current]
    raise TypeError(f"未知的狀態類型：{type(current).__name__}")


def can_transition[S: StrEnum](current: S, target: S) -> bool:
    """`current → target` 是否合法；不同狀態機或非列舉值一律為 False。"""
    if type(current) is not type(target) or type(current) not in MACHINES:
        return False
    return target in allowed_targets(current)


def transition[S: StrEnum](current: S, target: S) -> S:
    """驗證並回傳新狀態；非法時拋出 StateTransitionError。"""
    if not can_transition(current, target):
        raise StateTransitionError(current, target)
    return target


def is_terminal(state: StrEnum) -> bool:
    """是否為終態（沒有任何合法的下一個狀態）。"""
    return not allowed_targets(state)
