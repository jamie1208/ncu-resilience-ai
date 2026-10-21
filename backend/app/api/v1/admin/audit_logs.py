"""管理端稽核查詢（P3-T03）：`GET /api/v1/admin/audit-logs`，需要 `audit_logs:read`。

新到舊，cursor 分頁；可依 action、actor、target、request_id 與時間區間（since 含、until 不含）篩選。
`audit_logs:read` 是敏感權限：每次查詢經 `authorize()` 寫入 `audit_logs.read` 稽核（記錄篩選條件）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, keyset_after, split_page
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.audit import AuditLog
from app.services.audit import AuditMetadataError, authorize

router = APIRouter(prefix="/audit-logs", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.AUDIT_LOGS_READ))]


class AuditLogOut(BaseModel):
    """一筆稽核紀錄；metadata 寫入時已排除個資與原文。"""

    id: uuid.UUID
    actor: str
    action: str
    target: str | None
    request_id: str | None
    metadata: dict[str, Any]
    created_at: AwareDatetime


@router.get("")
async def list_audit_logs(
    session: Session,
    principal: CanRead,
    action: Annotated[str | None, Query(max_length=100, pattern=r"^[a-z_.]+$")] = None,
    actor: Annotated[str | None, Query(max_length=200)] = None,
    target: Annotated[str | None, Query(max_length=300)] = None,
    request_id: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[AwareDatetime | None, Query(description="起始時間（含）")] = None,
    until: Annotated[AwareDatetime | None, Query(description="結束時間（不含）")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict[str, Any]:
    """查詢稽核紀錄（新到舊），並記錄這次查詢。"""
    filters: dict[str, Any] = {
        "action": action,
        "actor": actor,
        "target": target,
        "request_id": request_id,
        "since": since,
        "until": until,
    }
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    for column, value in (
        (AuditLog.action, action),
        (AuditLog.actor, actor),
        (AuditLog.target, target),
        (AuditLog.request_id, request_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at < until)
    if cursor is not None:
        stmt = stmt.where(keyset_after(AuditLog.created_at, AuditLog.id, cursor))
    rows = (await session.execute(stmt.limit(limit + 1))).scalars().all()
    page, next_cursor = split_page(rows, limit, lambda log: (log.created_at, log.id))
    data = [
        AuditLogOut(
            id=log.id,
            actor=log.actor,
            action=log.action,
            target=log.target,
            request_id=log.request_id,
            metadata=log.meta,
            created_at=log.created_at,
        ).model_dump(mode="json")
        for log in page
    ]

    used = {key: value for key, value in filters.items() if value is not None}
    try:
        await authorize(
            session, principal, Permission.AUDIT_LOGS_READ, metadata={**used, "limit": limit}
        )
    except AuditMetadataError:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "篩選條件不得含個資") from None
    await session.commit()
    return envelope(data, next_cursor=next_cursor, limit=limit)
