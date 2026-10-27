"""改善措施追蹤（P4-T05，規格 F10）。

- 追蹤項目可從事件小報告中 S08 的校方措施建立（同一份報告的同一項措施只能建立一次，保存措施
  快照，負責人預設為建議單位），也可手動新增。同學措施不追蹤；事件不實或已封存時不能新增。
- 生命週期：discovered（發現）→ handling（處理）→ recovered（恢復）→ improved（改進）；
  發現、處理、恢復都可改為 dropped（不追蹤）；恢復後若問題再出現可回到處理。
  改進與不追蹤為終點。轉到恢復、改進必須附佐證（例如公告內容或照片說明）。
- 每次狀態變更（含建立時的「發現」）新增一筆 response_action_transitions，
  與稽核紀錄共用 request_id。
- 名稱、負責人、備註、佐證含個資時拒絕（與模型輸出的 PII 規則相同）。

本模組只 flush、不 commit，由呼叫端決定交易邊界；所有檢查都在修改之前完成，
失敗時不留下半套變更。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.validate import find_pii
from app.core.logging import request_id_var
from app.core.security import Principal
from app.domain.states import EventStatus
from app.models.event import Event
from app.models.report import EventReport
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.services.audit import record_audit

TITLE_MAX = 200
OWNER_MAX = 100
NOTE_MAX = 200
PROOF_MAX = 2000


class ActionState(StrEnum):
    """改善措施的狀態；值存入資料庫，已上線的值不可改名。"""

    DISCOVERED = "discovered"
    HANDLING = "handling"
    RECOVERED = "recovered"
    IMPROVED = "improved"
    DROPPED = "dropped"


_S = ActionState

ACTION_TRANSITIONS: Mapping[ActionState, frozenset[ActionState]] = {
    _S.DISCOVERED: frozenset({_S.HANDLING, _S.DROPPED}),
    _S.HANDLING: frozenset({_S.RECOVERED, _S.DROPPED}),
    _S.RECOVERED: frozenset({_S.IMPROVED, _S.HANDLING, _S.DROPPED}),
    _S.IMPROVED: frozenset(),
    _S.DROPPED: frozenset(),
}

PROOF_REQUIRED: frozenset[ActionState] = frozenset({_S.RECOVERED, _S.IMPROVED})

# 已結束的項目不算逾期
CLOSED_STATES: frozenset[ActionState] = frozenset({_S.IMPROVED, _S.DROPPED})

_CLOSED_EVENT_STATUSES = frozenset({EventStatus.FALSE.value, EventStatus.ARCHIVED.value})


class ActionError(Exception):
    """改善措施操作失敗；由 API 轉成對應的錯誤碼。"""


class ActionNotFoundError(ActionError):
    """事件、報告或追蹤項目不存在。"""


class ActionConflictError(ActionError):
    """與目前狀態衝突（非法轉移、重複建立、事件不實或封存、報告失效）。"""


class ActionInvalidError(ActionError):
    """輸入不合格；`field` 為出問題的欄位。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _text(value: str | None, field: str, limit: int) -> str | None:
    """去頭尾空白；空字串視為沒有；超長或含個資時拒絕。"""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise ActionInvalidError(field, f"超過 {limit} 字")
    if find_pii(cleaned):
        raise ActionInvalidError(field, "疑似含個資")
    return cleaned


def _request_id() -> str:
    return request_id_var.get() or str(uuid.uuid4())


async def _event(session: AsyncSession, event_id: uuid.UUID) -> Event:
    event = await session.get(Event, event_id)
    if event is None:
        raise ActionNotFoundError("找不到事件")
    return event


async def get_action(session: AsyncSession, action_id: uuid.UUID) -> ResponseAction:
    """取得追蹤項目；不存在時拋出 ActionNotFoundError。"""
    action = await session.get(ResponseAction, action_id)
    if action is None:
        raise ActionNotFoundError("找不到改善措施")
    return action


async def _measure(
    session: AsyncSession, event_id: uuid.UUID, report_id: uuid.UUID, index: int
) -> dict[str, Any]:
    report = await session.get(EventReport, report_id, populate_existing=True)
    if report is None or report.event_id != event_id:
        raise ActionNotFoundError("找不到這個事件的小報告")
    if report.invalidated_at is not None:
        raise ActionConflictError("小報告已失效，不能從中建立改善措施")
    if not 0 <= index < len(report.measures):
        raise ActionInvalidError("measure_index", "報告中沒有這項措施")
    measure = dict(report.measures[index])
    if measure.get("audience") != "school":
        raise ActionInvalidError("measure_index", "只有校方措施可以建立追蹤")
    exists = await session.scalar(
        select(ResponseAction.id).where(
            ResponseAction.report_id == report_id, ResponseAction.measure_index == index
        )
    )
    if exists is not None:
        raise ActionConflictError("這項措施已經在追蹤中")
    return measure


async def _record(
    session: AsyncSession,
    principal: Principal,
    action: ResponseAction,
    previous: ActionState | None,
    *,
    note: str | None = None,
    proof: str | None = None,
    audit_action: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    request_id = _request_id()
    session.add(
        ResponseActionTransition(
            action_id=action.id,
            from_state=previous.value if previous else None,
            to_state=action.state,
            note=note,
            proof=proof,
            actor=principal.actor,
            request_id=request_id,
        )
    )
    await record_audit(
        session,
        principal,
        audit_action,
        target=f"response_action:{action.id}",
        request_id=request_id,
        metadata={
            "from": previous.value if previous else None,
            "to": action.state,
            **(metadata or {}),
        },
    )


async def create_action(
    session: AsyncSession,
    principal: Principal,
    event_id: uuid.UUID,
    *,
    title: str | None = None,
    report_id: uuid.UUID | None = None,
    measure_index: int | None = None,
    owner: str | None = None,
    due_at: datetime | None = None,
) -> ResponseAction:
    """新增追蹤項目：有 report_id 時從該報告的校方措施建立，否則手動新增（必須有名稱）。"""
    event = await _event(session, event_id)
    if event.status in _CLOSED_EVENT_STATUSES:
        raise ActionConflictError("事件已被查證為不實或已封存，不能新增改善措施")
    owner = _text(owner, "owner", OWNER_MAX)
    measure: dict[str, Any] | None = None
    if report_id is not None or measure_index is not None:
        if report_id is None or measure_index is None:
            raise ActionInvalidError(
                "report_id", "從報告建立時 report_id 與 measure_index 都要提供"
            )
        measure = await _measure(session, event_id, report_id, measure_index)
        # S08 措施在產生報告時已遮蔽個資；名稱沿用措施內容，負責人預設為建議單位
        name = str(measure["action"])[:TITLE_MAX]
        owner = owner or measure.get("responsible_unit")
    else:
        name = _text(title, "title", TITLE_MAX) or ""
        if not name:
            raise ActionInvalidError("title", "請填寫改善措施名稱")
    action = ResponseAction(
        event_id=event_id,
        origin="measure" if measure is not None else "manual",
        report_id=report_id,
        measure_index=measure_index,
        measure=measure,
        title=name,
        owner=owner,
        due_at=due_at,
        state=ActionState.DISCOVERED.value,
    )
    session.add(action)
    await session.flush()
    await _record(
        session,
        principal,
        action,
        None,
        audit_action="response_action.create",
        metadata={"event_id": str(event_id), "origin": action.origin},
    )
    return action


async def transition_action(
    session: AsyncSession,
    principal: Principal,
    action_id: uuid.UUID,
    to: ActionState,
    *,
    note: str | None = None,
    proof: str | None = None,
) -> ResponseAction:
    """變更狀態；不在轉移表中回 ActionConflictError，恢復與改進缺佐證回 ActionInvalidError。"""
    action = await get_action(session, action_id)
    note = _text(note, "note", NOTE_MAX)
    proof = _text(proof, "proof", PROOF_MAX)
    previous = ActionState(action.state)
    if to not in ACTION_TRANSITIONS[previous]:
        raise ActionConflictError(f"改善措施不能從 {previous.value} 改為 {to.value}")
    if to in PROOF_REQUIRED and not proof:
        raise ActionInvalidError("proof", "恢復與改進必須附佐證")
    action.state = to.value
    if proof:
        action.proof = proof
    await session.flush()
    await _record(
        session,
        principal,
        action,
        previous,
        note=note,
        proof=proof,
        audit_action="response_action.transition",
    )
    return action


async def update_action(
    session: AsyncSession,
    principal: Principal,
    action_id: uuid.UUID,
    changes: Mapping[str, Any],
) -> ResponseAction:
    """修改負責人或期限（只改 changes 中出現的欄位；值為 None 代表清除）。"""
    action = await get_action(session, action_id)
    changed: list[str] = []
    if "owner" in changes:
        action.owner = _text(changes["owner"], "owner", OWNER_MAX)
        changed.append("owner")
    if "due_at" in changes:
        action.due_at = changes["due_at"]
        changed.append("due_at")
    await session.flush()
    await record_audit(
        session,
        principal,
        "response_action.update",
        target=f"response_action:{action.id}",
        metadata={"fields": changed},
    )
    return action


async def list_actions(
    session: AsyncSession,
    *,
    state: ActionState | None = None,
    event_id: uuid.UUID | None = None,
    limit: int = 200,
) -> Sequence[tuple[ResponseAction, str]]:
    """追蹤項目與事件標題，最近更新的在前。"""
    query = select(ResponseAction, Event.title).join(Event, Event.id == ResponseAction.event_id)
    if state is not None:
        query = query.where(ResponseAction.state == state.value)
    if event_id is not None:
        query = query.where(ResponseAction.event_id == event_id)
    query = query.order_by(ResponseAction.updated_at.desc(), ResponseAction.id.desc()).limit(limit)
    return [(a, t) for a, t in await session.execute(query)]


async def action_history(
    session: AsyncSession, action_id: uuid.UUID
) -> Sequence[ResponseActionTransition]:
    """狀態變更歷史，舊到新。"""
    rows = await session.scalars(
        select(ResponseActionTransition)
        .where(ResponseActionTransition.action_id == action_id)
        .order_by(ResponseActionTransition.created_at, ResponseActionTransition.id)
    )
    return rows.all()


def is_overdue(action: ResponseAction, now: datetime | None = None) -> bool:
    """有期限、已過期且尚未結束（改進或不追蹤）。"""
    if action.due_at is None or ActionState(action.state) in CLOSED_STATES:
        return False
    return action.due_at < (now or datetime.now(UTC))
