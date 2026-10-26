"""管理端通知 API（P3-T03）。

- `GET /api/v1/admin/alerts`：通知列表（個人通知中心），新到舊，cursor 分頁；可依狀態篩選。
  需要 `alerts:read_all`。每筆附 `flags`：疑似指控（possible_allegation，P3-T07 的確定性規則）
  給管理端人工判斷（附錄 A05，P3-T10）；讀取時依事件標題重算，不另存欄位。
- `POST /api/v1/admin/alerts/{id}/retract`：撤回通知並記錄原因；只有 published、updated 可撤回，
  其他狀態回 CONFLICT。需要 `alerts:retract`，寫入稽核 `alert.retract`（含原因）。

通知的建立與遞送只能經 app/alerts/（P3-T07、P3-T08），管理者沒有發布權限（ADR-0008）。
"""

from __future__ import annotations

import uuid
from functools import cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.policy import NotificationPolicy, load_policy
from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, keyset_after, split_page
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.domain.states import AlertState, StateTransitionError, transition
from app.models.alert import Alert
from app.models.event import Event
from app.services.audit import AuditMetadataError, prepare_metadata, record_audit

router = APIRouter(prefix="/alerts", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.ALERTS_READ_ALL))]
CanRetract = Annotated[Principal, Depends(require_permission(Permission.ALERTS_RETRACT))]


class AlertOut(BaseModel):
    """通知列表的一筆。"""

    id: uuid.UUID
    event_id: uuid.UUID
    event_title: str
    audience: str
    state: str
    version: int
    body: str
    retract_reason: str | None
    flags: list[str]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RetractIn(BaseModel):
    """撤回原因；會寫入稽核紀錄，不得含個資。"""

    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1), Field(max_length=200)
    ]


POSSIBLE_ALLEGATION = "possible_allegation"


@cache
def _policy() -> NotificationPolicy:
    return load_policy()


def _flags(title: str) -> list[str]:
    """管理端要人工判斷的標記（只有規則代碼，不含內容）。"""
    return [POSSIBLE_ALLEGATION] if _policy().allegation.matches(title) else []


@router.get("")
async def list_alerts(
    session: Session,
    _: CanRead,
    state: Annotated[AlertState | None, Query(description="通知狀態")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict[str, Any]:
    """列出通知（新到舊），附事件標題。"""
    stmt = (
        select(Alert, Event.title)
        .join(Event, Event.id == Alert.event_id)
        .order_by(Alert.created_at.desc(), Alert.id.desc())
        .limit(limit + 1)
    )
    if state is not None:
        stmt = stmt.where(Alert.state == state.value)
    if cursor is not None:
        stmt = stmt.where(keyset_after(Alert.created_at, Alert.id, cursor))
    rows = (await session.execute(stmt)).all()
    page, next_cursor = split_page(rows, limit, lambda r: (r[0].created_at, r[0].id))
    data = [
        AlertOut(
            id=alert.id,
            event_id=alert.event_id,
            event_title=title,
            audience=alert.audience,
            state=alert.state,
            version=alert.version,
            body=alert.body,
            retract_reason=alert.retract_reason,
            flags=_flags(title),
            created_at=alert.created_at,
            updated_at=alert.updated_at,
        ).model_dump(mode="json")
        for alert, title in page
    ]
    return envelope(data, next_cursor=next_cursor, limit=limit)


@router.post("/{alert_id}/retract")
async def retract_alert(
    alert_id: uuid.UUID, body: RetractIn, session: Session, principal: CanRetract
) -> dict[str, Any]:
    """撤回通知；不存在回 NOT_FOUND，狀態不允許撤回回 CONFLICT，原因含個資回 VALIDATION_ERROR。"""
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到通知")
    previous = AlertState(alert.state)
    try:
        target = transition(previous, AlertState.RETRACTED)
    except StateTransitionError:
        raise ApiError(ErrorCode.CONFLICT, f"狀態為 {previous.value} 的通知不能撤回") from None
    metadata = {"from": previous.value, "reason": body.reason, "version": alert.version}
    try:
        prepare_metadata(metadata)
    except AuditMetadataError:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "撤回原因不得含個資",
            details=[{"field": "body.reason", "message": "疑似含個資"}],
        ) from None
    alert.state = target.value
    alert.retract_reason = body.reason
    await record_audit(
        session, principal, "alert.retract", target=f"alert:{alert_id}", metadata=metadata
    )
    await session.commit()
    return envelope({"id": str(alert_id), "state": target.value, "previous": previous.value})
