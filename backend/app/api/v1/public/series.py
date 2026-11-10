"""公開系列分析頁 API（P8-T06，ADR-0018 決策 1-9、2-7，ADR-0020 決策 2；使用者同意新增）。

- `GET /api/v1/public/series/{id}`：系列分析頁——總覽、累計件數與最近一起、每週件數與突增週、
  發生地點分組、規律與可能原因（一律帶「推測，尚未查證」或「已確認」標籤）、改善建議（不寫依據的
  原因）、改善進度、改善前後比較、這個系列的每一起。`id` 是系列的 publication ID。

只讀取 publications 中 state 為 published 的目前版本（`app/publishing/sync.py` 依公開規則寫入、
已遮蔽 PII 的快照），回傳欄位以白名單挑出。子事件只列出目前仍以事件內容公開中的（讀取時再比對一次，
事件下架後立即不再出現在系列頁），引用的事件 ID（`related_ids`）同樣過濾；子事件的 `id` 是
事件的 publication ID，可直接連到 `/api/v1/public/events/{id}`。

`series_refs()` 供快報列表與快報全文（content.py）標示「XX 系列 · 第 N 起」。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.api.v1.public.route import PublicRoute
from app.models.publication import Publication, PublicationVersion
from app.publishing.policy import PublicLevel

router = APIRouter(tags=["public"], route_class=PublicRoute)

Session = Annotated[AsyncSession, Depends(get_db_session)]

_DETAIL_FIELDS = (
    "event_type",
    "label",
    "name",
    "summary",
    "first_date",
    "latest_date",
    "timeline",
    "bursts",
    "pattern_status",
    "pattern_note",
    "measures",
    "progress",
    "comparisons",
)
_EVENT_FIELDS = ("id", "ordinal", "title", "occurrence_date", "location", "verification",
                 "verification_label")  # fmt: skip
_GROUP_FIELDS = ("label", "related_ids", "event_count", "total")
_PATTERN_FIELDS = ("description", "label", "related_ids", "event_count", "total")
_CAUSE_FIELDS = ("description", "label", "decision", "related_ids", "event_count", "total")


def published_series() -> Select[Any]:
    """目前公開中的系列分析頁版本。"""
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
            Publication.series_id.is_not(None),
            Publication.state == "published",
            Publication.level == PublicLevel.CONTENT.value,
            PublicationVersion.content.is_not(None),
        )
    )


async def visible_events(session: AsyncSession, ids: Iterable[str]) -> set[str]:
    """`ids` 中目前仍以事件內容公開中的事件 publication ID。"""
    wanted = {uuid.UUID(i) for i in ids}
    if not wanted:
        return set()
    rows = await session.scalars(
        select(Publication.id).where(
            Publication.id.in_(wanted),
            Publication.event_id.is_not(None),
            Publication.state == "published",
            Publication.level == PublicLevel.CONTENT.value,
        )
    )
    return {str(r) for r in rows}


def _visible_members(content: dict[str, Any], visible: set[str]) -> list[dict[str, Any]]:
    members = [e for e in content.get("events") or [] if str(e.get("id")) in visible]
    return [
        {**{k: e.get(k) for k in _EVENT_FIELDS}, "ordinal": i}
        for i, e in enumerate(members, start=1)
    ]


def _pick(items: Any, fields: tuple[str, ...], visible: set[str]) -> list[dict[str, Any]]:
    picked = []
    for item in items or []:
        entry = {k: item.get(k) for k in fields}
        entry["related_ids"] = [i for i in item.get("related_ids") or [] if i in visible]
        picked.append(entry)
    return picked


async def series_refs(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """事件 publication ID → 所屬公開系列頁（系列 ID、名稱、第幾起、目前公開的件數）。"""
    rows = (await session.execute(published_series())).all()
    member_ids = {str(e.get("id")) for _, c, _, _ in rows for e in c.get("events") or []}
    visible = await visible_events(session, member_ids)
    refs: dict[str, dict[str, Any]] = {}
    for pub_id, content, _, _ in rows:
        members = _visible_members(content, visible)
        for m in members:
            refs[str(m["id"])] = {
                "id": str(pub_id),
                "name": content.get("name"),
                "ordinal": m["ordinal"],
                "event_count": len(members),
            }
    return refs


@router.get("/series/{publication_id}")
async def series_detail(publication_id: uuid.UUID, session: Session) -> dict[str, Any]:
    """系列分析頁；不公開、已撤下或已下架的系列回 NOT_FOUND。"""
    row = (
        await session.execute(published_series().where(Publication.id == publication_id))
    ).first()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到公開的系列")
    pub_id, content, version, at = row
    visible = await visible_events(session, (str(e.get("id")) for e in content.get("events") or []))
    events = _visible_members(content, visible)
    if not events:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到公開的系列")
    return envelope(
        {
            "id": str(pub_id),
            "kind": "series",
            "version": version,
            "published_at": at.isoformat(),
            **{k: content.get(k) for k in _DETAIL_FIELDS},
            "event_count": len(events),
            "events": events,
            "location_groups": _pick(content.get("location_groups"), _GROUP_FIELDS, visible),
            "patterns": _pick(content.get("patterns"), _PATTERN_FIELDS, visible),
            "causes": _pick(content.get("causes"), _CAUSE_FIELDS, visible),
        }
    )


__all__ = ["published_series", "router", "series_refs", "visible_events"]
