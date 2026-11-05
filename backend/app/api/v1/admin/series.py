"""管理端系列 API（P8-T04，ADR-0018 決策 1-1～1-4、ADR-0020 決策 1）。

- `GET /api/v1/admin/series`：系列清單（最近有事件加入的在前，最多 `limit` 筆），可依 `category`
  或 `event_id`（該事件所屬的系列）篩選。需要 `events:read_all`。
- `GET /api/v1/admin/series/{id}`：系列與它的事件（加入時間排序，附加入方式）及移出紀錄。
  需要 `events:read_all`；系列不存在回 NOT_FOUND。
- `POST /api/v1/admin/series/{id}/events/{event_id}/split`（回 201）：把事件拆出系列，
  自成 1 件的系列，之後不再自動歸回。需要 `events:verify`，寫入稽核 `series.split`。
- `POST /api/v1/admin/series/{id}/dissolve`：解散系列，每個事件各自成為 1 件的系列。
  需要 `events:verify`，寫入稽核 `series.dissolve`。
系列或事件不存在（含事件不在這個系列）回 NOT_FOUND，系列只有一件回 CONFLICT。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.event import Event
from app.models.series import EventSeries, EventSeriesMember, EventSeriesRemoval
from app.series.grouping import (
    SeriesConflictError,
    SeriesNotFoundError,
    dissolve_series,
    split_from_series,
)

router = APIRouter(prefix="/series", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]
CanVerify = Annotated[Principal, Depends(require_permission(Permission.EVENTS_VERIFY))]


def _series_data(series: EventSeries, count: int, latest: datetime | None) -> dict[str, Any]:
    return {
        "id": str(series.id),
        "category": series.category,
        "title": series.title,
        "event_count": count,
        "latest_joined_at": latest.isoformat() if latest else None,
        "created_at": series.created_at.isoformat(),
    }


@router.get("")
async def list_series(
    session: Session,
    _: CanRead,
    category: Annotated[str | None, Query(max_length=32)] = None,
    event_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """系列清單與件數。"""
    latest = func.max(EventSeriesMember.joined_at)
    query = (
        select(EventSeries, func.count(EventSeriesMember.event_id), latest)
        .join(EventSeriesMember, EventSeriesMember.series_id == EventSeries.id)
        .group_by(EventSeries.id)
        .order_by(latest.desc(), EventSeries.id)
        .limit(limit)
    )
    if category is not None:
        query = query.where(EventSeries.category == category)
    if event_id is not None:
        query = query.where(
            EventSeries.id
            == select(EventSeriesMember.series_id)
            .where(EventSeriesMember.event_id == event_id)
            .scalar_subquery()
        )
    rows = (await session.execute(query)).all()
    return envelope([_series_data(s, int(count), last) for s, count, last in rows])


@router.get("/{series_id}")
async def get_series(series_id: uuid.UUID, session: Session, _: CanRead) -> dict[str, Any]:
    """系列與它的事件。"""
    series = await session.get(EventSeries, series_id)
    if series is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到系列")
    rows = list(
        (
            await session.execute(
                select(Event, EventSeriesMember)
                .join(EventSeriesMember, EventSeriesMember.event_id == Event.id)
                .where(EventSeriesMember.series_id == series_id)
                .order_by(EventSeriesMember.joined_at, Event.id)
            )
        ).all()
    )
    joined = [m.joined_at for _, m in rows]
    events = [
        {
            "id": str(e.id),
            "title": e.title,
            "status": e.status,
            "occurrence_start_date": (
                e.occurrence_start_date.isoformat() if e.occurrence_start_date else None
            ),
            "created_at": e.created_at.isoformat(),
            "joined_at": m.joined_at.isoformat(),
            "method": m.method,
        }
        for e, m in rows
    ]
    removals = [
        {"event_id": str(r.event_id), "reason": r.reason, "created_at": r.created_at.isoformat()}
        for r in await session.scalars(
            select(EventSeriesRemoval)
            .where(EventSeriesRemoval.series_id == series_id)
            .order_by(EventSeriesRemoval.created_at)
        )
    ]
    data = _series_data(series, len(events), max(joined, default=None))
    return envelope({**data, "events": events, "removals": removals})


@router.post("/{series_id}/events/{event_id}/split", status_code=status.HTTP_201_CREATED)
async def split(
    series_id: uuid.UUID, event_id: uuid.UUID, session: Session, principal: CanVerify
) -> dict[str, Any]:
    """把事件拆出系列。"""
    try:
        new = await split_from_series(session, principal, series_id, event_id)
    except SeriesNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    except SeriesConflictError as e:
        raise ApiError(ErrorCode.CONFLICT, str(e)) from None
    await session.commit()
    return envelope(
        {"event_id": str(event_id), "series_id": str(new), "from_series_id": str(series_id)}
    )


@router.post("/{series_id}/dissolve")
async def dissolve(series_id: uuid.UUID, session: Session, principal: CanVerify) -> dict[str, Any]:
    """解散系列。"""
    try:
        moved = await dissolve_series(session, principal, series_id)
    except SeriesNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    except SeriesConflictError as e:
        raise ApiError(ErrorCode.CONFLICT, str(e)) from None
    await session.commit()
    return envelope(
        {
            "series_id": str(series_id),
            "events": [{"event_id": str(e), "series_id": str(s)} for e, s in moved.items()],
        }
    )
