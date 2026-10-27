"""管理端改善措施追蹤 API（P4-T05，規格 F10）。

- `GET /api/v1/admin/actions`：追蹤項目列表，最近更新的在前；可依狀態、事件篩選；每筆附事件標題
  與是否逾期。需要 `events:read_all`。
- `GET /api/v1/admin/actions/{id}`：追蹤項目與狀態變更歷史（舊到新）。需要 `events:read_all`。
- `POST /api/v1/admin/events/{id}/actions`：新增追蹤項目——帶 report_id、measure_index 時從小報告的
  S08 校方措施建立，否則手動新增（必須有 title）。需要 `events:verify`，
  寫入稽核 `response_action.create`。
- `PATCH /api/v1/admin/actions/{id}`：修改負責人、期限。需要 `events:verify`，寫入稽核
  `response_action.update`。
- `POST /api/v1/admin/actions/{id}/transitions`：變更狀態（發現→處理→恢復→改進，或不追蹤）；
  非法轉移回 CONFLICT，恢復與改進缺佐證回 VALIDATION_ERROR。需要 `events:verify`，寫入稽核
  `response_action.transition`。

負責人只是使用者自己的紀錄，系統不寄信給任何單位。規則見 app/resilience/actions.py。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.resilience.actions import (
    ActionConflictError,
    ActionError,
    ActionInvalidError,
    ActionState,
    action_history,
    create_action,
    get_action,
    is_overdue,
    list_actions,
    transition_action,
    update_action,
)

router = APIRouter(prefix="/actions", tags=["admin"])
event_router = APIRouter(prefix="/events", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]
CanWrite = Annotated[Principal, Depends(require_permission(Permission.EVENTS_VERIFY))]

LIST_LIMIT = 200


class EventRef(BaseModel):
    """追蹤項目所屬的事件。"""

    id: uuid.UUID
    title: str


class ActionOut(BaseModel):
    """一項改善措施；measure 為從 S08 建立時的措施快照（手動新增為 null）。"""

    id: uuid.UUID
    event_id: uuid.UUID
    origin: str
    report_id: uuid.UUID | None
    measure_index: int | None
    measure: dict[str, Any] | None
    title: str
    owner: str | None
    due_at: AwareDatetime | None
    state: str
    proof: str | None
    overdue: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ActionListItem(ActionOut):
    """列表的一筆，附事件標題。"""

    event: EventRef


class TransitionOut(BaseModel):
    """一次狀態變更。"""

    from_state: str | None
    to_state: str
    note: str | None
    proof: str | None
    actor: str
    created_at: AwareDatetime


class ActionDetail(ActionOut):
    """追蹤項目與狀態變更歷史。"""

    history: list[TransitionOut]


class ActionIn(BaseModel):
    """新增追蹤項目；report_id + measure_index 從小報告的校方措施建立，否則用 title 手動新增。"""

    title: Annotated[str | None, Field(max_length=500)] = None
    report_id: uuid.UUID | None = None
    measure_index: Annotated[int | None, Field(ge=0)] = None
    owner: Annotated[str | None, Field(max_length=500)] = None
    due_at: AwareDatetime | None = None


class ActionPatch(BaseModel):
    """修改負責人、期限；只更新有出現的欄位，null 代表清除。"""

    owner: Annotated[str | None, Field(max_length=500)] = None
    due_at: AwareDatetime | None = None


class TransitionIn(BaseModel):
    """狀態變更；恢復與改進必須有 proof。備註與佐證不得含個資。"""

    to: ActionState
    note: Annotated[str | None, Field(max_length=500)] = None
    proof: Annotated[str | None, Field(max_length=5000)] = None


def _api_error(error: ActionError) -> ApiError:
    if isinstance(error, ActionInvalidError):
        return ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(error),
            details=[{"field": f"body.{error.field}", "message": str(error)}],
        )
    if isinstance(error, ActionConflictError):
        return ApiError(ErrorCode.CONFLICT, str(error))
    return ApiError(ErrorCode.NOT_FOUND, str(error))


def _out(action: ResponseAction, now: datetime | None = None) -> dict[str, Any]:
    return {
        "id": action.id,
        "event_id": action.event_id,
        "origin": action.origin,
        "report_id": action.report_id,
        "measure_index": action.measure_index,
        "measure": action.measure,
        "title": action.title,
        "owner": action.owner,
        "due_at": action.due_at,
        "state": action.state,
        "proof": action.proof,
        "overdue": is_overdue(action, now),
        "created_at": action.created_at,
        "updated_at": action.updated_at,
    }


def _history(rows: list[ResponseActionTransition]) -> list[TransitionOut]:
    return [
        TransitionOut(
            from_state=r.from_state,
            to_state=r.to_state,
            note=r.note,
            proof=r.proof,
            actor=r.actor,
            created_at=r.created_at,
        )
        for r in rows
    ]


async def _detail(session: AsyncSession, action: ResponseAction) -> dict[str, Any]:
    await session.refresh(action)
    history = list(await action_history(session, action.id))
    return ActionDetail(**_out(action), history=_history(history)).model_dump(mode="json")


@router.get("")
async def list_response_actions(
    session: Session,
    _: CanRead,
    state: Annotated[ActionState | None, Query()] = None,
    event_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    """追蹤項目列表（最多 200 筆）。"""
    rows = await list_actions(session, state=state, event_id=event_id, limit=LIST_LIMIT)
    data = [
        ActionListItem(**_out(a), event=EventRef(id=a.event_id, title=t)).model_dump(mode="json")
        for a, t in rows
    ]
    return envelope(data)


@router.get("/{action_id}")
async def get_response_action(action_id: uuid.UUID, session: Session, _: CanRead) -> dict[str, Any]:
    """追蹤項目與狀態變更歷史；不存在回 NOT_FOUND。"""
    try:
        action = await get_action(session, action_id)
    except ActionError as e:
        raise _api_error(e) from None
    return envelope(await _detail(session, action))


@event_router.post("/{event_id}/actions", status_code=status.HTTP_201_CREATED)
async def create_response_action(
    event_id: uuid.UUID, body: ActionIn, session: Session, principal: CanWrite
) -> dict[str, Any]:
    """新增追蹤項目；事件或報告不存在回 NOT_FOUND，不實、封存、報告失效或重複回 CONFLICT。"""
    try:
        action = await create_action(
            session,
            principal,
            event_id,
            title=body.title,
            report_id=body.report_id,
            measure_index=body.measure_index,
            owner=body.owner,
            due_at=body.due_at,
        )
    except ActionError as e:
        raise _api_error(e) from None
    await session.commit()
    return envelope(await _detail(session, action))


@router.patch("/{action_id}")
async def update_response_action(
    action_id: uuid.UUID, body: ActionPatch, session: Session, principal: CanWrite
) -> dict[str, Any]:
    """修改負責人、期限。"""
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        action = await update_action(session, principal, action_id, changes)
    except ActionError as e:
        raise _api_error(e) from None
    await session.commit()
    return envelope(await _detail(session, action))


@router.post("/{action_id}/transitions")
async def transition_response_action(
    action_id: uuid.UUID, body: TransitionIn, session: Session, principal: CanWrite
) -> dict[str, Any]:
    """變更狀態並記錄歷史。"""
    try:
        action = await transition_action(
            session, principal, action_id, body.to, note=body.note, proof=body.proof
        )
    except ActionError as e:
        raise _api_error(e) from None
    await session.commit()
    return envelope(await _detail(session, action))
