"""管理端公開內容 API（P5-T14，ADR-0015）。

- `GET /api/v1/admin/publications`：公開內容列表（最近更新的在前），可依狀態、事件篩選；每筆附
  事件或系列（P8-T06，`subject`）的標題、目前版本的公開預覽（事件內容為標題、系列為名稱、安全
  提醒為提醒文字）與最近一次下架原因。需要 `alerts:read_all`。
- `GET /api/v1/admin/publications/reminders`：通知中心的公開提醒（P8-T06，ADR-0018 2-2、
  ADR-0020 2-3）：疑似指控、推測的規律與原因第一次公開或改變時的版本（reasons 含 `remind`，由
  `app/publishing/sync.py` 寫入），新到舊最多 100 筆；每筆附標記（possible_allegation、
  mentions_party、speculation）、目前狀態與能否一鍵下架。需要 `alerts:read_all`。
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
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.event import Event
from app.models.publication import Publication, PublicationTakedown, PublicationVersion
from app.models.series import EventSeries
from app.publishing.policy import MENTIONS_PARTY, POSSIBLE_ALLEGATION, SPECULATION
from app.publishing.sync import REMIND
from app.publishing.takedown import (
    TAKEABLE_STATES,
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
REMINDER_LIMIT = 100
REMINDER_FLAGS = (POSSIBLE_ALLEGATION, MENTIONS_PARTY, SPECULATION)

PublicationState = Literal["published", "withdrawn", "taken_down"]
Subject = Literal["event", "series"]


class PublicationOut(BaseModel):
    """公開內容列表的一筆。"""

    id: uuid.UUID
    subject: Subject
    event_id: uuid.UUID | None
    series_id: uuid.UUID | None
    event_title: str
    """事件標題，系列為系列名稱。"""
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


class ReminderOut(BaseModel):
    """一則公開提醒：需要使用者注意的內容在這個版本第一次公開或改變了。"""

    publication_id: uuid.UUID
    subject: Subject
    event_id: uuid.UUID | None
    series_id: uuid.UUID | None
    title: str
    version: int
    flags: list[str]
    state: str
    current: bool
    """是否仍是目前公開的版本。"""
    can_take_down: bool
    created_at: AwareDatetime


def _preview(content: dict[str, Any] | None) -> str | None:
    if not content:
        return None
    value = content.get("title") or content.get("name") or content.get("notice")
    return str(value) if value else None


def _subject(pub: Publication) -> Subject:
    return "series" if pub.series_id is not None else "event"


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
        select(
            Publication,
            func.coalesce(Event.title, EventSeries.title),
            PublicationVersion.content,
            latest_reason,
        )
        .outerjoin(Event, Event.id == Publication.event_id)
        .outerjoin(EventSeries, EventSeries.id == Publication.series_id)
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
            subject=_subject(pub),
            event_id=pub.event_id,
            series_id=pub.series_id,
            event_title=title or "",
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


@router.get("/reminders")
async def list_reminders(session: Session, _: CanRead) -> dict[str, Any]:
    """通知中心的公開提醒（新到舊），每筆附一鍵下架所需的 publication ID。"""
    stmt = (
        select(Publication, PublicationVersion)
        .join(PublicationVersion, PublicationVersion.publication_id == Publication.id)
        .where(PublicationVersion.reasons.contains([REMIND]))
        .order_by(PublicationVersion.created_at.desc(), PublicationVersion.id.desc())
        .limit(REMINDER_LIMIT)
    )
    rows = (await session.execute(stmt)).all()
    data = []
    for pub, version in rows:
        current = version.version == pub.version
        data.append(
            ReminderOut(
                publication_id=pub.id,
                subject=_subject(pub),
                event_id=pub.event_id,
                series_id=pub.series_id,
                title=_preview(version.content) or "",
                version=version.version,
                flags=[f for f in REMINDER_FLAGS if f in (version.reasons or [])],
                state=pub.state,
                current=current,
                can_take_down=current and pub.state in TAKEABLE_STATES,
                created_at=version.created_at,
            ).model_dump(mode="json")
        )
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
