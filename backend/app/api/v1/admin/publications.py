"""管理端公開內容 API（P5-T14，ADR-0015）。

- `GET /api/v1/admin/publications`：公開內容列表（最近更新的在前），可依狀態、事件篩選；每筆附
  事件標題、目前版本的公開預覽（事件內容為標題、安全提醒為提醒文字）與最近一次下架原因。
  需要 `alerts:read_all`。
- `POST /api/v1/admin/publications/{id}/takedown`：一鍵下架（原因可省略）；公開 API 立即不再回傳。
  已下架回 CONFLICT，原因含個資回 VALIDATION_ERROR。需要 `alerts:retract`，寫入稽核
  `publication.take_down`。
- `POST /api/v1/admin/publications/{id}/restore`：重新上架，交回公開規則判斷（不直接公開）。
  未下架回 CONFLICT。需要 `events:verify`，寫入稽核 `publication.restore`。

公開與否只能經公開規則（P5-T10）判斷；管理者只能撤下，不能直接公開。規則見
app/publishing/takedown.py。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.event import Event
from app.models.publication import Publication, PublicationTakedown, PublicationVersion
from app.publishing.takedown import (
    TakedownConflictError,
    TakedownNotFoundError,
    TakedownResult,
    restore,
    take_down,
)
from app.services.audit import AuditMetadataError

router = APIRouter(prefix="/publications", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.ALERTS_READ_ALL))]
CanTakeDown = Annotated[Principal, Depends(require_permission(Permission.ALERTS_RETRACT))]
CanRestore = Annotated[Principal, Depends(require_permission(Permission.EVENTS_VERIFY))]

LIST_LIMIT = 200

PublicationState = Literal["published", "withdrawn", "taken_down"]


class PublicationOut(BaseModel):
    """公開內容列表的一筆。"""

    id: uuid.UUID
    event_id: uuid.UUID
    event_title: str
    level: str
    state: str
    version: int
    needs_review: bool
    reasons: list[str]
    preview: str | None
    taken_down_at: AwareDatetime | None
    takedown_reason: str | None
    updated_at: AwareDatetime


class TakedownIn(BaseModel):
    """下架原因（可省略）；會寫入稽核紀錄，不得含個資。"""

    reason: Annotated[str | None, Field(max_length=200)] = None


def _preview(content: dict[str, Any] | None) -> str | None:
    if not content:
        return None
    value = content.get("title") or content.get("notice")
    return str(value) if value else None


def _result(result: TakedownResult) -> dict[str, Any]:
    return envelope(
        {"id": str(result.publication_id), "state": result.state, "previous": result.previous}
    )


@router.get("")
async def list_publications(
    session: Session,
    _: CanRead,
    state: Annotated[PublicationState | None, Query(description="公開狀態")] = None,
    event_id: Annotated[uuid.UUID | None, Query(description="事件 ID")] = None,
) -> dict[str, Any]:
    """列出公開內容（最近更新的在前），附事件標題、公開預覽與最近一次下架原因。"""
    latest_reason = (
        select(PublicationTakedown.reason)
        .where(PublicationTakedown.publication_id == Publication.id)
        .order_by(PublicationTakedown.created_at.desc(), PublicationTakedown.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    stmt = (
        select(Publication, Event.title, PublicationVersion.content, latest_reason)
        .join(Event, Event.id == Publication.event_id)
        .outerjoin(
            PublicationVersion,
            and_(
                PublicationVersion.publication_id == Publication.id,
                PublicationVersion.version == Publication.version,
            ),
        )
        .order_by(Publication.updated_at.desc(), Publication.id.desc())
        .limit(LIST_LIMIT)
    )
    if state is not None:
        stmt = stmt.where(Publication.state == state)
    if event_id is not None:
        stmt = stmt.where(Publication.event_id == event_id)
    rows = (await session.execute(stmt)).all()
    data = [
        PublicationOut(
            id=pub.id,
            event_id=pub.event_id,
            event_title=title,
            level=pub.level,
            state=pub.state,
            version=pub.version,
            needs_review=pub.needs_review,
            reasons=list(pub.reasons),
            # 下架時保留目前版本，預覽即被下架的內容；撤下的版本沒有內容
            preview=_preview(content),
            taken_down_at=pub.taken_down_at,
            takedown_reason=reason if pub.state == "taken_down" else None,
            updated_at=pub.updated_at,
        ).model_dump(mode="json")
        for pub, title, content, reason in rows
    ]
    return envelope(data)


@router.post("/{publication_id}/takedown")
async def takedown_publication(
    publication_id: uuid.UUID,
    session: Session,
    principal: CanTakeDown,
    body: Annotated[TakedownIn | None, Body()] = None,
) -> dict[str, Any]:
    """一鍵下架；不存在回 NOT_FOUND，已下架回 CONFLICT，原因含個資回 VALIDATION_ERROR。"""
    try:
        result = await take_down(
            session, publication_id, principal, reason=body.reason if body else None
        )
    except TakedownNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    except TakedownConflictError as e:
        raise ApiError(ErrorCode.CONFLICT, str(e)) from None
    except AuditMetadataError:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "下架原因不得含個資",
            details=[{"field": "body.reason", "message": "疑似含個資"}],
        ) from None
    await session.commit()
    return _result(result)


@router.post("/{publication_id}/restore")
async def restore_publication(
    publication_id: uuid.UUID, session: Session, principal: CanRestore
) -> dict[str, Any]:
    """重新上架並依公開規則重新判斷；不存在回 NOT_FOUND，未下架回 CONFLICT。"""
    try:
        result = await restore(session, publication_id, principal)
    except TakedownNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    except TakedownConflictError as e:
        raise ApiError(ErrorCode.CONFLICT, str(e)) from None
    await session.commit()
    return _result(result)
