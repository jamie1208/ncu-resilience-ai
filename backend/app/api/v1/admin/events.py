"""管理端事件 API。

- `POST /api/v1/admin/events/{id}/analyze`（P2-T13）：重跑事件的分析（S05 查證、S06 風險），
  排入 Celery 佇列後回 202；需要 `events:analyze`，寫入稽核 `event.analyze`。
  分析的 trace_id 沿用本請求的 request_id，可由稽核紀錄對到 ai_analysis_runs。

事件列表等其他管理功能於 P3-T03 加入。登入於 P3-T01 實作，在那之前一律回 401。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.logging import request_id_var
from app.core.security import Permission, Principal
from app.domain.states import EventStatus
from app.models.event import Event
from app.services.audit import record_audit
from app.worker.celery_app import ANALYZE_EVENT_TASK, celery_app

router = APIRouter(prefix="/events", tags=["admin"])

AnalysisQueue = Callable[[uuid.UUID, str], None]


def _send_to_celery(event_id: uuid.UUID, request_id: str) -> None:
    celery_app.send_task(ANALYZE_EVENT_TASK, args=[str(event_id), request_id])


def get_analysis_queue() -> AnalysisQueue:
    """把事件排入分析佇列的函式（測試時以 dependency override 替換）。"""
    return _send_to_celery


Session = Annotated[AsyncSession, Depends(get_db_session)]
CanAnalyze = Annotated[Principal, Depends(require_permission(Permission.EVENTS_ANALYZE))]
Queue = Annotated[AnalysisQueue, Depends(get_analysis_queue)]


@router.post("/{event_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze_event(
    event_id: uuid.UUID, session: Session, principal: CanAnalyze, enqueue: Queue
) -> dict[str, Any]:
    """重跑事件分析（非同步）；事件不存在回 NOT_FOUND，已封存回 CONFLICT。"""
    event = await session.get(Event, event_id)
    if event is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到事件")
    if event.status == EventStatus.ARCHIVED.value:
        raise ApiError(ErrorCode.CONFLICT, "事件已封存，不能重跑分析")
    request_id = request_id_var.get() or str(uuid.uuid4())
    await record_audit(
        session, principal, "event.analyze", target=f"event:{event_id}", request_id=request_id
    )
    await session.commit()
    enqueue(event_id, request_id)
    return envelope({"event_id": str(event_id), "status": "queued"})
