"""公開唯讀內容 API（P5-T11，ADR-0015）。

只讀取 publications 中 state 為 published 的目前版本（P5-T10 規則判定、已遮蔽 PII 的快照），
不讀事件、證據、小報告或改善措施等原始資料表。回傳欄位以白名單從快照挑出；`id` 是
publication 的 ID，不是事件 ID。

- `GET /api/v1/public/feed`：最新動態（事件內容與安全提醒，新到舊），可依 `type` 篩選
- `GET /api/v1/public/notices`：安全提醒（未經證實事件的中性提醒）
- `GET /api/v1/public/categories`：事件類別與各自公開的事件數、提醒數
- `GET /api/v1/public/events`：事件分類查詢（只有事件內容），可依 `type` 篩選
- `GET /api/v1/public/events/{id}`：公開版小報告（影響、建議、官方來源、改善進度）
- `GET /api/v1/public/progress`：改善進度（有改善措施的公開事件）

列表皆為 cursor 分頁（依版本時間新到舊）。
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from functools import cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.policy import NotificationPolicy
from app.alerts.policy import load_policy as load_notification_policy
from app.api.deps import get_db_session
from app.api.errors import ApiError, ErrorCode
from app.api.pagination import keyset_after, split_page
from app.api.responses import envelope
from app.api.v1.public.route import PublicRoute
from app.models.publication import Publication, PublicationVersion
from app.publishing.policy import PublicLevel, load_policy

# route_class 要設在這裡：include_router 會沿用子 router 的路由類別
router = APIRouter(tags=["public"], route_class=PublicRoute)

DEFAULT_LIMIT = 20
MAX_LIMIT = 50

_SUMMARY_FIELDS = {
    PublicLevel.CONTENT.value: (
        "event_type",
        "label",
        "title",
        "summary",
        "location",
        "occurrence_date",
        "risk_level",
    ),
    PublicLevel.SAFETY_NOTICE.value: ("event_type", "label", "notice"),
}
_DETAIL_FIELDS = ("report", "sources", "progress")
_PUBLIC_LEVELS = (PublicLevel.CONTENT.value, PublicLevel.SAFETY_NOTICE.value)

Session = Annotated[AsyncSession, Depends(get_db_session)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Cursor = Annotated[str | None, Query(max_length=512)]
TypeFilter = Annotated[str | None, Query(max_length=32, description="事件類別")]

Row = tuple[uuid.UUID, dict[str, Any], int, datetime]


@cache
def _texts() -> NotificationPolicy:
    return load_notification_policy()


@cache
def _public_types() -> tuple[str, ...]:
    excluded = set(load_policy().excluded_event_types)
    return tuple(t for t in _texts().event_types if t not in excluded)


def _check_type(value: str | None) -> str | None:
    if value is not None and value not in _public_types():
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "請求參數不正確",
            details=[{"field": "query.type", "message": "未知的事件類別"}],
        )
    return value


def _published(*levels: str) -> Select[Row]:
    """目前公開中、指定公開程度的版本；只有這裡決定什麼能被讀到。"""
    return (
        select(
            Publication.id,
            PublicationVersion.content,
            PublicationVersion.version,
            PublicationVersion.created_at,
        )
        .join(
            PublicationVersion,
            and_(
                PublicationVersion.publication_id == Publication.id,
                PublicationVersion.version == Publication.version,
            ),
        )
        .where(
            Publication.state == "published",
            Publication.level.in_(levels),
            PublicationVersion.level.in_(levels),
            PublicationVersion.content.is_not(None),
        )
    )


def _item(row: Row, *, detail: bool = False) -> dict[str, Any]:
    pub_id, content, version, at = row
    kind = str(content.get("kind"))
    fields = _SUMMARY_FIELDS.get(kind, ())
    if detail and kind == PublicLevel.CONTENT.value:
        fields = (*fields, *_DETAIL_FIELDS)
    return {
        "id": str(pub_id),
        "kind": kind,
        "version": version,
        "published_at": at.isoformat(),
        **{k: content.get(k) for k in fields},
    }


async def _page(
    session: AsyncSession,
    stmt: Select[Row],
    *,
    event_type: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[Sequence[Row], str | None]:
    if event_type is not None:
        stmt = stmt.where(PublicationVersion.content["event_type"].astext == event_type)
    if cursor is not None:
        stmt = stmt.where(keyset_after(PublicationVersion.created_at, Publication.id, cursor))
    stmt = stmt.order_by(PublicationVersion.created_at.desc(), Publication.id.desc()).limit(
        limit + 1
    )
    rows = [tuple(r) for r in (await session.execute(stmt)).all()]
    return split_page(rows, limit, lambda r: (r[3], r[0]))  # type: ignore[arg-type]


async def _list(
    session: AsyncSession,
    levels: tuple[str, ...],
    event_type: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    rows, next_cursor = await _page(
        session,
        _published(*levels),
        event_type=_check_type(event_type),
        limit=limit,
        cursor=cursor,
    )
    return envelope([_item(r) for r in rows], next_cursor=next_cursor, limit=limit)


@router.get("/feed")
async def feed(
    session: Session,
    type: TypeFilter = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """最新動態：公開的事件內容與安全提醒，新到舊。"""
    return await _list(session, _PUBLIC_LEVELS, type, limit, cursor)


@router.get("/notices")
async def notices(
    session: Session,
    type: TypeFilter = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """安全提醒：未經證實事件的中性提醒，新到舊。"""
    return await _list(session, (PublicLevel.SAFETY_NOTICE.value,), type, limit, cursor)


@router.get("/categories")
async def categories(session: Session) -> dict[str, Any]:
    """事件類別，以及每個類別目前公開的事件數與提醒數。"""
    published = _published(*_PUBLIC_LEVELS).subquery()
    event_type = published.c.content["event_type"].astext
    kind = published.c.content["kind"].astext
    rows = await session.execute(select(event_type, kind, func.count()).group_by(event_type, kind))
    counts: Counter[tuple[str, str]] = Counter({(t, k): n for t, k, n in rows})
    data = [
        {
            "event_type": t,
            "label": _texts().event_types[t].label,
            "content": counts[(t, PublicLevel.CONTENT.value)],
            "safety_notice": counts[(t, PublicLevel.SAFETY_NOTICE.value)],
        }
        for t in _public_types()
    ]
    return envelope(data)


@router.get("/events")
async def events(
    session: Session,
    type: TypeFilter = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """事件分類查詢：已查證、公開內容的事件，新到舊。"""
    return await _list(session, (PublicLevel.CONTENT.value,), type, limit, cursor)


@router.get("/events/{publication_id}")
async def event_detail(publication_id: uuid.UUID, session: Session) -> dict[str, Any]:
    """公開版小報告；只有提醒、已撤下或已下架的事件回 NOT_FOUND。"""
    row = (
        await session.execute(
            _published(PublicLevel.CONTENT.value).where(Publication.id == publication_id)
        )
    ).first()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到公開事件")
    return envelope(_item(tuple(row), detail=True))  # type: ignore[arg-type]


@router.get("/progress")
async def progress(
    session: Session,
    type: TypeFilter = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """改善進度：有改善措施的公開事件，附各狀態的措施數。"""
    stmt = _published(PublicLevel.CONTENT.value).where(
        func.jsonb_array_length(PublicationVersion.content["progress"]) > 0
    )
    rows, next_cursor = await _page(
        session, stmt, event_type=_check_type(type), limit=limit, cursor=cursor
    )
    data = []
    for row in rows:
        pub_id, content, version, at = row
        actions = list(content.get("progress") or [])
        data.append(
            {
                "id": str(pub_id),
                "version": version,
                "published_at": at.isoformat(),
                **{k: content.get(k) for k in ("event_type", "label", "title", "occurrence_date")},
                "counts": dict(Counter(str(a.get("state")) for a in actions)),
                "progress": actions,
            }
        )
    return envelope(data, next_cursor=next_cursor, limit=limit)
