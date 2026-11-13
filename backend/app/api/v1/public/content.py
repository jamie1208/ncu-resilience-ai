"""公開唯讀內容 API（P5-T11，ADR-0015）。

只讀取 publications 中 state 為 published 的目前版本（P5-T10 規則判定、已遮蔽 PII 的快照），
不讀事件、證據、小報告或改善措施等原始資料表。回傳欄位以白名單從快照挑出；`id` 是
publication 的 ID，不是事件 ID。

- `GET /api/v1/public/feed`：最新快報（事件內容，新到舊），可依 `type` 篩選、依 `q` 關鍵字搜尋；
  P8-T06 起未查證的事件也公開 AI 摘要並帶 `verification_label`（「尚未查證」），
  每筆附所屬系列（`series`，見 series.py）
- `GET /api/v1/public/notices`：安全提醒（P8-T06 起不再產生，只剩舊版本已撤下前的資料，通常為空）
- `GET /api/v1/public/categories`：事件類別與各自公開的事件數、提醒數
- `GET /api/v1/public/events`：事件分類查詢（只有事件內容），可依 `type` 篩選、依 `q` 關鍵字搜尋
- `GET /api/v1/public/events/{id}`：快報全文（官方來源、所屬系列）；單一事件小報告已停用（P8-T13）
- `GET /api/v1/public/progress`：改善進度（P8-T06 起以系列為單位：有改善措施的公開系列頁）

關鍵字只比對快照中已公開的標題、摘要與地點（已遮蔽 PII），`%`、`_` 照字面比對。
列表皆為 cursor 分頁（依版本時間新到舊）。系列分析頁的快照不會出現在事件的列表與詳細頁。
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from functools import cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.policy import NotificationPolicy
from app.alerts.policy import load_policy as load_notification_policy
from app.api.deps import get_db_session
from app.api.errors import ApiError, ErrorCode
from app.api.pagination import keyset_after, split_page
from app.api.responses import envelope
from app.api.v1.public.route import PublicRoute
from app.api.v1.public.series import published_series, series_refs
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
        "verification",
        "verification_label",
    ),
    PublicLevel.SAFETY_NOTICE.value: ("event_type", "label", "notice"),
}
_DETAIL_FIELDS = ("report", "sources")  # report 一律為 null（P8-T13 停用小報告）
_PUBLIC_LEVELS = (PublicLevel.CONTENT.value, PublicLevel.SAFETY_NOTICE.value)

Session = Annotated[AsyncSession, Depends(get_db_session)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Cursor = Annotated[str | None, Query(max_length=512)]
TypeFilter = Annotated[str | None, Query(max_length=32, description="事件類別")]
Keyword = Annotated[str | None, Query(max_length=50, description="關鍵字（標題、摘要、地點）")]

_KEYWORD_FIELDS = ("title", "summary", "location")

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
    """目前公開中、指定公開程度的事件版本；只有這裡決定什麼能被讀到（系列見 series.py）。"""
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
            Publication.event_id.is_not(None),
            Publication.state == "published",
            Publication.level.in_(levels),
            PublicationVersion.level.in_(levels),
            PublicationVersion.content.is_not(None),
        )
    )


def _item(row: Row, refs: dict[str, dict[str, Any]], *, detail: bool = False) -> dict[str, Any]:
    pub_id, content, version, at = row
    kind = str(content.get("kind"))
    fields = _SUMMARY_FIELDS.get(kind, ())
    if detail and kind == PublicLevel.CONTENT.value:
        fields = (*fields, *_DETAIL_FIELDS)
    item = {
        "id": str(pub_id),
        "kind": kind,
        "version": version,
        "published_at": at.isoformat(),
        **{k: content.get(k) for k in fields},
    }
    if kind == PublicLevel.CONTENT.value:
        item["series"] = refs.get(str(pub_id))
    return item


def _match_keyword(stmt: Select[Row], keyword: str | None) -> Select[Row]:
    """關鍵字篩選：不分大小寫比對公開快照的標題、摘要、地點；空白關鍵字不篩選。"""
    text = (keyword or "").strip()
    if not text:
        return stmt
    content = PublicationVersion.content
    return stmt.where(
        or_(*(content[f].astext.icontains(text, autoescape=True) for f in _KEYWORD_FIELDS))
    )


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
    keyword: str | None = None,
) -> dict[str, Any]:
    rows, next_cursor = await _page(
        session,
        _match_keyword(_published(*levels), keyword),
        event_type=_check_type(event_type),
        limit=limit,
        cursor=cursor,
    )
    refs = await series_refs(session)
    return envelope([_item(r, refs) for r in rows], next_cursor=next_cursor, limit=limit)


@router.get("/feed")
async def feed(
    session: Session,
    type: TypeFilter = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    q: Keyword = None,
) -> dict[str, Any]:
    """最新快報：公開的事件內容（含尚未查證的）與舊安全提醒，新到舊。"""
    return await _list(session, _PUBLIC_LEVELS, type, limit, cursor, q)


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
    q: Keyword = None,
) -> dict[str, Any]:
    """事件分類查詢：公開內容的事件（含尚未查證的），新到舊。"""
    return await _list(session, (PublicLevel.CONTENT.value,), type, limit, cursor, q)


@router.get("/events/{publication_id}")
async def event_detail(publication_id: uuid.UUID, session: Session) -> dict[str, Any]:
    """快報全文；只有提醒、已撤下或已下架的事件回 NOT_FOUND。"""
    row = (
        await session.execute(
            _published(PublicLevel.CONTENT.value).where(Publication.id == publication_id)
        )
    ).first()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到公開事件")
    refs = await series_refs(session)
    return envelope(_item(tuple(row), refs, detail=True))  # type: ignore[arg-type]


@router.get("/progress")
async def progress(
    session: Session,
    type: TypeFilter = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """改善進度：有改善措施的公開系列頁，附各狀態的措施數；`id` 是系列的 publication ID。"""
    stmt = published_series().where(
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
                "kind": "series",
                "version": version,
                "published_at": at.isoformat(),
                "event_type": content.get("event_type"),
                "label": content.get("label"),
                "title": content.get("name"),
                "occurrence_date": content.get("latest_date"),
                "counts": dict(Counter(str(a.get("state")) for a in actions)),
                "progress": actions,
            }
        )
    return envelope(data, next_cursor=next_cursor, limit=limit)
