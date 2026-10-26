"""管理端事件 API。

- `GET /api/v1/admin/events`（P3-T03）：事件列表，新到舊，cursor 分頁；可依狀態、類型篩選。
  每筆附最新一次風險評估與證據數。需要 `events:read_all`。
- `GET /api/v1/admin/events/{id}`（P3-T03）：事件詳細，含證據來源與風險評估歷史。
- `POST /api/v1/admin/events/{id}/verify`（P3-T03）：記錄查證結果（變更事件狀態）；
  轉移由 app/domain/states.py 檢查，非法回 CONFLICT。需要 `events:verify`，寫入稽核 `event.verify`。
- `POST /api/v1/admin/events/{id}/analyze`（P2-T13）：重跑事件的分析（S05 查證、S06 風險），
  排入 Celery 佇列後回 202；需要 `events:analyze`，寫入稽核 `event.analyze`。
  分析的 trace_id 沿用本請求的 request_id，可由稽核紀錄對到 ai_analysis_runs。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import sync_event_alert
from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, keyset_after, split_page
from app.api.responses import envelope
from app.core.logging import request_id_var
from app.core.security import Permission, Principal
from app.domain.states import EventStatus, StateTransitionError, transition
from app.models.ai import RiskAssessment
from app.models.event import Event, EventEvidence
from app.models.source import Source, SourceItem
from app.services.audit import AuditMetadataError, prepare_metadata, record_audit
from app.worker.celery_app import ANALYZE_EVENT_TASK, celery_app

router = APIRouter(prefix="/events", tags=["admin"])

AnalysisQueue = Callable[[uuid.UUID, str], None]

RISK_HISTORY_LIMIT = 20

# detected、analyzing 只由分析流程設定，管理者不能手動指定
VerifyStatus = Literal[
    "unverified", "verified", "disputed", "false", "monitoring", "resolved", "archived"
]


def _send_to_celery(event_id: uuid.UUID, request_id: str) -> None:
    celery_app.send_task(ANALYZE_EVENT_TASK, args=[str(event_id), request_id])


def get_analysis_queue() -> AnalysisQueue:
    """把事件排入分析佇列的函式（測試時以 dependency override 替換）。"""
    return _send_to_celery


Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]
CanVerify = Annotated[Principal, Depends(require_permission(Permission.EVENTS_VERIFY))]
CanAnalyze = Annotated[Principal, Depends(require_permission(Permission.EVENTS_ANALYZE))]
Queue = Annotated[AnalysisQueue, Depends(get_analysis_queue)]


class RiskSummary(BaseModel):
    """最新一次風險評估的分數與等級。"""

    score: float
    level: str


class EventOut(BaseModel):
    """事件列表的一筆。"""

    id: uuid.UUID
    title: str
    type: str
    status: str
    occurrence_start_date: date | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    evidence_count: int
    risk: RiskSummary | None


class EvidenceOut(BaseModel):
    """事件的一則證據：來源文章的出處與摘錄（不含全文）。"""

    source_item_id: uuid.UUID
    source_code: str
    url: str
    published_at: AwareDatetime | None
    support_type: str
    excerpt: str | None


class RiskAssessmentOut(BaseModel):
    """一次風險評估（分項、總分、等級與規則版本）。"""

    id: uuid.UUID
    score: float
    level: str
    dimensions: dict[str, Any]
    policy_version: str
    ai_run_id: uuid.UUID | None
    created_at: AwareDatetime


class VerifyIn(BaseModel):
    """查證結果；note 會寫入稽核紀錄，不得含個資。"""

    status: VerifyStatus
    note: Annotated[str | None, Field(max_length=200)] = None


async def _get_event(session: AsyncSession, event_id: uuid.UUID) -> Event:
    event = await session.get(Event, event_id)
    if event is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到事件")
    return event


@router.get("")
async def list_events(
    session: Session,
    _: CanRead,
    status: Annotated[EventStatus | None, Query(description="事件狀態")] = None,
    type: Annotated[str | None, Query(max_length=32, description="事件類型")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict[str, Any]:
    """列出事件（新到舊），附最新風險評估與證據數。"""
    stmt = select(Event).order_by(Event.created_at.desc(), Event.id.desc()).limit(limit + 1)
    if status is not None:
        stmt = stmt.where(Event.status == status.value)
    if type is not None:
        stmt = stmt.where(Event.type == type)
    if cursor is not None:
        stmt = stmt.where(keyset_after(Event.created_at, Event.id, cursor))
    rows = (await session.execute(stmt)).scalars().all()
    page, next_cursor = split_page(rows, limit, lambda e: (e.created_at, e.id))

    ids = [e.id for e in page]
    counts: dict[uuid.UUID, int] = {}
    risks: dict[uuid.UUID, RiskSummary] = {}
    if ids:
        count_rows = await session.execute(
            select(EventEvidence.event_id, func.count())
            .where(EventEvidence.event_id.in_(ids))
            .group_by(EventEvidence.event_id)
        )
        counts = {event_id: n for event_id, n in count_rows}
        risk_rows = await session.execute(
            select(RiskAssessment.event_id, RiskAssessment.score, RiskAssessment.level)
            .where(RiskAssessment.event_id.in_(ids))
            .ext(distinct_on(RiskAssessment.event_id))
            .order_by(
                RiskAssessment.event_id, RiskAssessment.created_at.desc(), RiskAssessment.id.desc()
            )
        )
        risks = {
            event_id: RiskSummary(score=float(score), level=level)
            for event_id, score, level in risk_rows
        }

    data = [
        EventOut(
            id=e.id,
            title=e.title,
            type=e.type,
            status=e.status,
            occurrence_start_date=e.occurrence_start_date,
            created_at=e.created_at,
            updated_at=e.updated_at,
            evidence_count=counts.get(e.id, 0),
            risk=risks.get(e.id),
        ).model_dump(mode="json")
        for e in page
    ]
    return envelope(data, next_cursor=next_cursor, limit=limit)


@router.get("/{event_id}")
async def get_event(event_id: uuid.UUID, session: Session, _: CanRead) -> dict[str, Any]:
    """事件詳細：基本資料、證據來源（舊到新）與最近的風險評估（新到舊）。"""
    event = await _get_event(session, event_id)
    evidence_rows = await session.execute(
        select(EventEvidence, SourceItem, Source.code)
        .join(SourceItem, SourceItem.id == EventEvidence.source_item_id)
        .join(Source, Source.id == SourceItem.source_id)
        .where(EventEvidence.event_id == event_id)
        .order_by(SourceItem.published_at.asc().nulls_last(), SourceItem.id)
    )
    evidence = [
        EvidenceOut(
            source_item_id=item.id,
            source_code=code,
            url=item.url,
            published_at=item.published_at,
            support_type=ev.support_type,
            excerpt=ev.excerpt,
        ).model_dump(mode="json")
        for ev, item, code in evidence_rows
    ]
    risk_rows = await session.execute(
        select(RiskAssessment)
        .where(RiskAssessment.event_id == event_id)
        .order_by(RiskAssessment.created_at.desc(), RiskAssessment.id.desc())
        .limit(RISK_HISTORY_LIMIT)
    )
    risks = [
        RiskAssessmentOut(
            id=r.id,
            score=float(r.score),
            level=r.level,
            dimensions=r.dimensions,
            policy_version=r.policy_version,
            ai_run_id=r.ai_run_id,
            created_at=r.created_at,
        ).model_dump(mode="json")
        for r in risk_rows.scalars()
    ]
    data = {
        "id": str(event.id),
        "title": event.title,
        "type": event.type,
        "status": event.status,
        "occurrence_start_date": (
            event.occurrence_start_date.isoformat() if event.occurrence_start_date else None
        ),
        "created_at": event.created_at.isoformat(),
        "updated_at": event.updated_at.isoformat(),
        "evidence": evidence,
        "risk_assessments": risks,
    }
    return envelope(data)


@router.post("/{event_id}/verify")
async def verify_event(
    event_id: uuid.UUID, body: VerifyIn, session: Session, principal: CanVerify
) -> dict[str, Any]:
    """記錄查證結果；事件不存在回 NOT_FOUND，狀態轉移不合法回 CONFLICT。"""
    event = await _get_event(session, event_id)
    previous = EventStatus(event.status)
    try:
        target = transition(previous, EventStatus(body.status))
    except StateTransitionError:
        raise ApiError(
            ErrorCode.CONFLICT, f"事件狀態不能從 {previous.value} 改為 {body.status}"
        ) from None
    metadata: dict[str, Any] = {"from": previous.value, "to": target.value}
    note = body.note.strip() if body.note else ""
    if note:
        metadata["note"] = note
    # 先檢查稽核 metadata，避免改了狀態才發現備註含個資
    try:
        prepare_metadata(metadata)
    except AuditMetadataError:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "備註不得含個資",
            details=[{"field": "body.note", "message": "疑似含個資"}],
        ) from None
    event.status = target.value
    await record_audit(
        session, principal, "event.verify", target=f"event:{event_id}", metadata=metadata
    )
    # 查證結果改變後同步個人通知（P3-T08）；由通知服務帳號執行，管理者本身沒有發布權限
    await sync_event_alert(session, event_id)
    await session.commit()
    return envelope({"id": str(event_id), "status": target.value, "previous": previous.value})


@router.post("/{event_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze_event(
    event_id: uuid.UUID, session: Session, principal: CanAnalyze, enqueue: Queue
) -> dict[str, Any]:
    """重跑事件分析（非同步）；事件不存在回 NOT_FOUND，已封存回 CONFLICT。"""
    event = await _get_event(session, event_id)
    if event.status == EventStatus.ARCHIVED.value:
        raise ApiError(ErrorCode.CONFLICT, "事件已封存，不能重跑分析")
    request_id = request_id_var.get() or str(uuid.uuid4())
    await record_audit(
        session, principal, "event.analyze", target=f"event:{event_id}", request_id=request_id
    )
    await session.commit()
    enqueue(event_id, request_id)
    return envelope({"event_id": str(event_id), "status": "queued"})
