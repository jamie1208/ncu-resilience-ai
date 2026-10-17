"""擷取執行紀錄與來源健康狀態（P1-T08）。

- `GET /api/v1/admin/ingestion-runs`：執行紀錄，新到舊，cursor 分頁；可依來源代碼、狀態篩選。
- `GET /api/v1/admin/ingestion-runs/health`：各來源健康狀態（7 天窗口）。

兩者都需要 `ingestion_runs:read`（管理者）。登入於 P3-T01 實作，在那之前一律回 401。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, encode_cursor
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.ingestion.health import WINDOW, compute_health
from app.ingestion.types import RunStatus
from app.models.source import IngestionRun, Source

router = APIRouter(prefix="/ingestion-runs", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.INGESTION_RUNS_READ))]


class IngestionRunOut(BaseModel):
    """一筆擷取執行紀錄；counts 只有筆數與錯誤類型，不含內文。"""

    id: uuid.UUID
    source_code: str
    status: str
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    counts: dict[str, Any]


@router.get("")
async def list_ingestion_runs(
    session: Session,
    _: CanRead,
    source: Annotated[str | None, Query(max_length=64, description="來源代碼")] = None,
    status: Annotated[RunStatus | None, Query(description="執行狀態")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict[str, Any]:
    """列出擷取執行紀錄（新到舊）。"""
    stmt = (
        select(IngestionRun, Source.code)
        .join(Source, Source.id == IngestionRun.source_id)
        .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
        .limit(limit + 1)
    )
    if source is not None:
        stmt = stmt.where(Source.code == source)
    if status is not None:
        stmt = stmt.where(IngestionRun.status == status.value)
    if cursor is not None:
        after_time, after_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                IngestionRun.started_at < after_time,
                and_(IngestionRun.started_at == after_time, IngestionRun.id < after_id),
            )
        )
    rows = (await session.execute(stmt)).all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1][0].started_at, page[-1][0].id) if len(rows) > limit else None
    )
    data = [
        IngestionRunOut(
            id=run.id,
            source_code=code,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            counts=run.counts,
        ).model_dump(mode="json")
        for run, code in page
    ]
    return envelope(data, next_cursor=next_cursor, limit=limit)


@router.get("/health")
async def ingestion_health(session: Session, _: CanRead) -> dict[str, Any]:
    """各來源健康狀態（7 天成功率、連續失敗、未處理死信等）。"""
    report = await compute_health(session, now=datetime.now(UTC))
    return envelope([h.model_dump(mode="json") for h in report], window_days=WINDOW.days)
