"""管理端地點 API（P8-T02，ADR-0018 決策 1-5）。

- `GET /api/v1/admin/locations`：最上層的代表地點清單（名稱排序），每個附別名、下層地點與事件數
  （事件數含別名的事件；下層地點不併入，另外列在 children）。需要 `events:read_all`。
- `POST /api/v1/admin/locations/{id}/split`：把判斷錯的別名從代表地點拆開，或把地點從上層地點拆開；
  拆開後不再自動合併。需要 `events:verify`，寫入稽核 `location.split`。
  地點不存在回 NOT_FOUND，不是別名也沒有上層地點回 CONFLICT。
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.locations.resolver import LocationNotFoundError, LocationSplitError, split_location
from app.models.event import Event, Location

router = APIRouter(prefix="/locations", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]
CanVerify = Annotated[Principal, Depends(require_permission(Permission.EVENTS_VERIFY))]


class LocationRef(BaseModel):
    """別名或下層地點。"""

    id: uuid.UUID
    name: str
    event_count: int


class LocationOut(BaseModel):
    """一個代表地點。"""

    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    event_count: int
    aliases: list[LocationRef]
    children: list[LocationRef]


@router.get("")
async def list_locations(session: Session, _: CanRead) -> dict[str, Any]:
    """代表地點與它們的別名、下層地點。"""
    locations = list(await session.scalars(select(Location).order_by(Location.name, Location.id)))
    counts: Counter[uuid.UUID] = Counter(
        {
            loc_id: n
            for loc_id, n in await session.execute(
                select(Event.location_id, func.count())
                .where(Event.location_id.is_not(None))
                .group_by(Event.location_id)
            )
            if loc_id is not None
        }
    )

    def own(loc: Location) -> int:
        """地點本身與別名的事件數。"""
        alias_ids = [a.id for a in locations if a.canonical_id == loc.id]
        return counts.get(loc.id, 0) + sum(counts.get(a, 0) for a in alias_ids)

    def ref(loc: Location) -> LocationRef:
        return LocationRef(id=loc.id, name=loc.name, event_count=own(loc))

    data = []
    for loc in locations:
        if loc.canonical_id is not None or loc.parent_id is not None:
            continue
        aliases = [
            LocationRef(id=a.id, name=a.name, event_count=counts.get(a.id, 0))
            for a in locations
            if a.canonical_id == loc.id
        ]
        children = [ref(c) for c in locations if c.parent_id == loc.id and c.canonical_id is None]
        data.append(
            LocationOut(
                id=loc.id,
                name=loc.name,
                parent_id=loc.parent_id,
                event_count=own(loc),
                aliases=aliases,
                children=children,
            ).model_dump(mode="json")
        )
    return envelope(data)


@router.post("/{location_id}/split")
async def split(location_id: uuid.UUID, session: Session, principal: CanVerify) -> dict[str, Any]:
    """把地點從代表地點或上層地點拆開。"""
    try:
        loc = await split_location(session, principal, location_id)
    except LocationNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    except LocationSplitError as e:
        raise ApiError(ErrorCode.CONFLICT, str(e)) from None
    await session.commit()
    return envelope(
        {
            "id": str(loc.id),
            "canonical_id": str(loc.canonical_id) if loc.canonical_id else None,
            "parent_id": str(loc.parent_id) if loc.parent_id else None,
        }
    )
