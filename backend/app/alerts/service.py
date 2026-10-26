"""個人通知的狀態機（P3-T08；規格 §6.3，狀態見 docs/architecture/state-machines.md）。

`sync_event_alert` 依事件目前的狀態，讓該事件的通知走完生命週期：

- 符合 P3-T07 規則且沒有有效通知 → candidate → policy_checked → eligible → published，
  並遞送（published 表示已通知使用者本人）；
- 已有有效通知（published／updated）且內容改變 → updated，版本遞增並遞送新版本；內容相同 → 不動作；
- 查證結果為 false → retracted（系統自動撤回）；
- 事件 resolved／archived，或不再符合規則（例：風險降到門檻以下）→ expired；
- 最新一則通知已撤回（使用者或系統）→ 停止，不再為這個事件通知。

不符合規則時不建立通知列（`blocked` 只是記憶體中的判斷結果），避免每個低風險事件都在通知中心
留下一筆。expired 之後若事件再次符合規則，建立新的通知、版本接續。

冪等：idempotency_key ＝ hash(event_id, alert_version, audience)，alerts 表唯一；同一版本的遞送
由 alert_deliveries 的唯一約束保證只送一次（`app/alerts/delivery.py`）。同一事件的同步以事件列
`SELECT ... FOR UPDATE` 串行化，併發時也只會發布一次。

稽核（不含通知內容）：`alert.publish`、`alert.update`、`alert.retract`、`alert.expire`，執行者為
通知服務帳號 `service:notifier`。系統自動的改版、撤回與過期屬於發布生命週期，只需 `alerts:publish`；
`alerts:retract` 仍只給使用者本人手動撤回（P3-T03）。函式只 flush，由呼叫端 commit。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.delivery import DEFAULT_CHANNELS, OWNER, Channel, deliver
from app.alerts.policy import (
    NotificationDecision,
    NotificationInput,
    NotificationPolicy,
    evaluate,
    load_policy,
)
from app.core.security import Permission, Principal, Role, require
from app.domain.states import AlertState, EventStatus, VerificationStatus, transition
from app.models.ai import AIAnalysisRun, RiskAssessment
from app.models.alert import Alert
from app.models.event import Event, EventEvidence, Location
from app.services.audit import record_audit

NOTIFIER_PRINCIPAL = Principal(
    kind="service", subject="notifier", roles=frozenset({Role.SERVICE_NOTIFIER})
)
EVIDENCE_VERIFIER = "evidence-verifier"
ACTIVE_STATES = frozenset({AlertState.PUBLISHED, AlertState.UPDATED})
ENDED_EVENT_STATES = frozenset({EventStatus.RESOLVED, EventStatus.ARCHIVED})
AUTO_RETRACT_REASON = "查證結果為不實，系統自動撤回"

_VERIFICATION_OF = {
    EventStatus.VERIFIED: VerificationStatus.VERIFIED,
    EventStatus.DISPUTED: VerificationStatus.DISPUTED,
    EventStatus.FALSE: VerificationStatus.FALSE,
}
"""事件狀態對應的查證結果；其他狀態（含 monitoring）一律視為 unverified，用一般安全提醒。"""


class SyncAction(StrEnum):
    """`sync_event_alert` 這次做了什麼。"""

    NONE = "none"
    PUBLISHED = "published"
    UPDATED = "updated"
    RETRACTED = "retracted"
    EXPIRED = "expired"


@dataclass(frozen=True)
class SyncResult:
    """同步結果；`reasons` 是觸發的規則代碼（不含內容），`delivered` 是這次實際送出的管道。"""

    action: SyncAction
    alert_id: uuid.UUID | None
    version: int | None
    reasons: tuple[str, ...]
    delivered: tuple[str, ...] = ()


def idempotency_key(event_id: uuid.UUID, version: int, audience: str) -> str:
    """hash(event_id, alert_version, audience)：SHA-256 hex（64 字元）。"""
    return hashlib.sha256(f"{event_id}:{version}:{audience}".encode()).hexdigest()


@cache
def _default_policy() -> NotificationPolicy:
    return load_policy()


async def sync_event_alert(
    session: AsyncSession,
    event_id: uuid.UUID,
    *,
    request_id: str | None = None,
    policy: NotificationPolicy | None = None,
    channels: Sequence[Channel] = DEFAULT_CHANNELS,
) -> SyncResult:
    """依事件目前的狀態同步它的通知；事件不存在時拋出 LookupError。只 flush，不 commit。"""
    require(NOTIFIER_PRINCIPAL, Permission.ALERTS_PUBLISH)
    event = await session.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        raise LookupError(f"事件 {event_id} 不存在")

    latest = await session.scalar(
        select(Alert)
        .where(Alert.event_id == event_id, Alert.audience == OWNER)
        .order_by(Alert.version.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if latest is not None and latest.state == AlertState.RETRACTED.value:
        return SyncResult(SyncAction.NONE, latest.id, latest.version, ("retracted",))
    active = latest if latest is not None and AlertState(latest.state) in ACTIVE_STATES else None

    if EventStatus(event.status) in ENDED_EVENT_STATES:
        if active is None:
            return SyncResult(SyncAction.NONE, None, None, ("event_ended",))
        return await _end(session, active, AlertState.EXPIRED, ("event_ended",), request_id)

    rules = policy or _default_policy()
    if event.type not in rules.event_types:
        # S03 之外的類別（例：手動建立的事件）不通知，也不讓查證等呼叫端失敗
        decision = NotificationDecision(
            event.id, False, None, None, ("unknown_event_type",), rules.policy_version
        )
    else:
        decision = evaluate(await _notification_input(session, event), rules)
    if not decision.notify:
        if active is None:
            return SyncResult(SyncAction.NONE, None, None, decision.reasons)
        if "verification_false" in decision.reasons:
            return await _end(session, active, AlertState.RETRACTED, decision.reasons, request_id)
        return await _end(session, active, AlertState.EXPIRED, decision.reasons, request_id)

    body = decision.body
    if body is None:  # evaluate() 在 notify 時一定產生內容
        raise RuntimeError("通知規則判定要通知但沒有內容")
    if active is None:
        version = latest.version + 1 if latest is not None else 1
        return await _publish(session, event, version, body, decision, channels, request_id)
    if active.body == body:
        # 內容沒變：補送尚未遞送的管道（正常情況下不會有），不改版
        delivered = await deliver(session, active, channels)
        return SyncResult(SyncAction.NONE, active.id, active.version, decision.reasons, delivered)
    return await _update(session, active, body, decision, channels, request_id)


async def _publish(
    session: AsyncSession,
    event: Event,
    version: int,
    body: str,
    decision: NotificationDecision,
    channels: Sequence[Channel],
    request_id: str | None,
) -> SyncResult:
    state = AlertState.CANDIDATE
    for target in (AlertState.POLICY_CHECKED, AlertState.ELIGIBLE, AlertState.PUBLISHED):
        state = transition(state, target)
    alert = Alert(
        event_id=event.id,
        audience=OWNER,
        state=state.value,
        version=version,
        body=body,
        idempotency_key=idempotency_key(event.id, version, OWNER),
    )
    session.add(alert)
    await session.flush()
    delivered = await deliver(session, alert, channels)
    await _audit(session, "alert.publish", alert, decision, request_id, delivered=delivered)
    return SyncResult(SyncAction.PUBLISHED, alert.id, version, decision.reasons, delivered)


async def _update(
    session: AsyncSession,
    alert: Alert,
    body: str,
    decision: NotificationDecision,
    channels: Sequence[Channel],
    request_id: str | None,
) -> SyncResult:
    previous = AlertState(alert.state)
    alert.state = transition(previous, AlertState.UPDATED).value
    alert.version += 1
    alert.idempotency_key = idempotency_key(alert.event_id, alert.version, alert.audience)
    alert.body = body
    await session.flush()
    delivered = await deliver(session, alert, channels)
    await _audit(
        session, "alert.update", alert, decision, request_id, previous=previous, delivered=delivered
    )
    return SyncResult(SyncAction.UPDATED, alert.id, alert.version, decision.reasons, delivered)


async def _end(
    session: AsyncSession,
    alert: Alert,
    target: AlertState,
    reasons: tuple[str, ...],
    request_id: str | None,
) -> SyncResult:
    previous = AlertState(alert.state)
    alert.state = transition(previous, target).value
    if target is AlertState.RETRACTED:
        alert.retract_reason = AUTO_RETRACT_REASON
    await session.flush()
    action = SyncAction.RETRACTED if target is AlertState.RETRACTED else SyncAction.EXPIRED
    await record_audit(
        session,
        NOTIFIER_PRINCIPAL,
        "alert.retract" if target is AlertState.RETRACTED else "alert.expire",
        target=f"alert:{alert.id}",
        request_id=request_id,
        metadata={
            "event_id": str(alert.event_id),
            "from": previous.value,
            "version": alert.version,
            "reasons": list(reasons),
        },
    )
    return SyncResult(action, alert.id, alert.version, reasons)


async def _audit(
    session: AsyncSession,
    action: str,
    alert: Alert,
    decision: NotificationDecision,
    request_id: str | None,
    *,
    delivered: tuple[str, ...],
    previous: AlertState | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "event_id": str(alert.event_id),
        "version": alert.version,
        "template": decision.template.value if decision.template else None,
        "reasons": list(decision.reasons),
        "policy_version": decision.policy_version,
        "delivered": list(delivered),
    }
    if previous is not None:
        metadata["from"] = previous.value
    await record_audit(
        session,
        NOTIFIER_PRINCIPAL,
        action,
        target=f"alert:{alert.id}",
        request_id=request_id,
        metadata=metadata,
    )


async def _notification_input(session: AsyncSession, event: Event) -> NotificationInput:
    """從資料庫組出 P3-T07 規則需要的資料。"""
    level = await session.scalar(
        select(RiskAssessment.level)
        .where(RiskAssessment.event_id == event.id)
        .order_by(RiskAssessment.created_at.desc(), RiskAssessment.id.desc())
        .limit(1)
    )
    location = (
        await session.scalar(select(Location.name).where(Location.id == event.location_id))
        if event.location_id is not None
        else None
    )
    return NotificationInput(
        event_id=event.id,
        event_type=event.type,
        title=event.title,
        risk_level=level,
        verification=_VERIFICATION_OF.get(EventStatus(event.status), VerificationStatus.UNVERIFIED),
        location_name=location,
        occurrence_date=event.occurrence_start_date,
        has_official_source=await _has_official_source(session, event.id),
    )


async def _has_official_source(session: AsyncSession, event_id: uuid.UUID) -> bool:
    """最新一次成功的 S05 是否把目前仍連結的某份證據判為官方（official）且支持事件。"""
    result = await session.scalar(
        select(AIAnalysisRun.result)
        .where(
            AIAnalysisRun.event_id == event_id,
            AIAnalysisRun.skill_name == EVIDENCE_VERIFIER,
            AIAnalysisRun.status == "succeeded",
        )
        .order_by(AIAnalysisRun.created_at.desc(), AIAnalysisRun.id.desc())
        .limit(1)
    )
    if not result:
        return False
    official = {
        str(a.get("evidence_id"))
        for a in result.get("assessments", [])
        if isinstance(a, dict)
        and a.get("claim_type") == "official"
        and a.get("stance") == "supports"
    }
    if not official:
        return False
    linked = await session.scalars(
        select(EventEvidence.source_item_id).where(EventEvidence.event_id == event_id)
    )
    return any(item_id.hex in official for item_id in linked)
