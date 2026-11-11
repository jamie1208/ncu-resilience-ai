"""後台匿名回報收件匣（P8-T16）。

- `GET /api/v1/admin/anonymous-reports`：收件匣中的全部投稿，依收據分組（原投稿在前、補件依收到
  順序在後；沒有收據的舊投稿自成一組）。每則投稿列出收到時間、原投稿或補件、擷取與分析狀態、
  所屬事件；沒有成立事件、甚至還沒被擷取的投稿也看得到。

投稿原文只在管理端出現（ADR-0015）；回應不含收據碼或它的雜湊，分組只用原投稿的收件匣 id 表示。
分頁以組為單位，依原投稿的收件匣 id 新到舊（即原投稿的收到順序）；後來的補件不會讓整組往前移。
需要 `sources:read`（管理者）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.anonymous_report import AnonymousReport, ReportReceipt
from app.models.event import Event, EventEvidence
from app.services.report_receipts import report_items

router = APIRouter(prefix="/anonymous-reports", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.SOURCES_READ))]

IngestionStatus = Literal["not_ingested", "ingested"]


class ReportEventOut(BaseModel):
    """投稿被歸入的事件。"""

    id: uuid.UUID
    title: str
    status: str


class InboxReportOut(BaseModel):
    """收件匣中的一則投稿（原文只在管理端出現）。"""

    id: int
    kind: Literal["original", "supplement"]
    content: str
    location: str | None
    submitted_at: AwareDatetime
    ingestion_status: IngestionStatus
    source_item_id: uuid.UUID | None
    analysis_status: str | None
    events: list[ReportEventOut]


class InboxGroupOut(BaseModel):
    """同一張收據的投稿；`id` 是原投稿的收件匣 id。"""

    id: int
    latest_submitted_at: AwareDatetime
    reports: list[InboxReportOut]
    events: list[ReportEventOut]


def _decode(cursor: str) -> int:
    if not cursor.isdigit() or len(cursor) > 19:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor 不正確")
    return int(cursor)


@router.get("")
async def list_anonymous_reports(
    session: Session,
    _: CanRead,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=32)] = None,
) -> dict[str, Any]:
    """列出匿名回報收件匣，補件依收據分組到原投稿之下（新到舊）。"""
    receipt = aliased(ReportReceipt)
    original = aliased(ReportReceipt)
    root = func.coalesce(original.report_id, AnonymousReport.id)
    grouped = (
        select(AnonymousReport.id)
        .outerjoin(receipt, receipt.report_id == AnonymousReport.id)
        .outerjoin(
            original,
            and_(original.code_hash == receipt.code_hash, original.is_supplement.is_(False)),
        )
    )

    roots_stmt = (
        grouped.with_only_columns(root.label("root"))
        .group_by(root)
        .order_by(root.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        roots_stmt = roots_stmt.where(root < _decode(cursor))
    roots = list((await session.execute(roots_stmt)).scalars())
    page = roots[:limit]
    next_cursor = str(page[-1]) if len(roots) > limit else None
    if not page:
        return envelope([], next_cursor=None, limit=limit)

    rows = (
        await session.execute(
            grouped.with_only_columns(AnonymousReport, receipt.is_supplement, root)
            .where(root.in_(page))
            .order_by(AnonymousReport.id)
        )
    ).all()
    items = await report_items(session, [report.id for report, _, _ in rows])
    events = await _events_of(session, [item_id for item_id, _ in items.values()])

    reports_of: dict[int, list[InboxReportOut]] = {r: [] for r in page}
    latest: dict[int, datetime] = {}
    for report, is_supplement, group in rows:
        found = items.get(report.id)
        reports_of[group].append(
            InboxReportOut(
                id=report.id,
                kind="supplement" if is_supplement else "original",
                content=report.content,
                location=report.location,
                submitted_at=report.submitted_at,
                ingestion_status="ingested" if found else "not_ingested",
                source_item_id=found[0] if found else None,
                analysis_status=found[1] if found else None,
                events=events.get(found[0], []) if found else [],
            )
        )
        if group not in latest or report.submitted_at > latest[group]:
            latest[group] = report.submitted_at

    data = []
    for group in page:
        merged: dict[uuid.UUID, ReportEventOut] = {}
        for r in reports_of[group]:
            for e in r.events:
                merged.setdefault(e.id, e)
        data.append(
            InboxGroupOut(
                id=group,
                latest_submitted_at=latest[group],
                reports=reports_of[group],
                events=list(merged.values()),
            ).model_dump(mode="json")
        )
    return envelope(data, next_cursor=next_cursor, limit=limit)


async def _events_of(
    session: AsyncSession, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ReportEventOut]]:
    """來源文章 id → 它被歸入的事件（依事件建立時間）。"""
    if not item_ids:
        return {}
    rows = await session.execute(
        select(EventEvidence.source_item_id, Event.id, Event.title, Event.status)
        .join(Event, Event.id == EventEvidence.event_id)
        .where(EventEvidence.source_item_id.in_(item_ids))
        .order_by(Event.created_at, Event.id)
    )
    result: dict[uuid.UUID, list[ReportEventOut]] = {}
    for item_id, event_id, title, status in rows.all():
        result.setdefault(item_id, []).append(
            ReportEventOut(id=event_id, title=title, status=status)
        )
    return result
