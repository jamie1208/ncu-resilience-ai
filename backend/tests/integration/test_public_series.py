"""P8-T06：公開系列分析頁（`app/publishing/sync.py` 的系列同步、`/api/v1/public/series/{id}`）。

- 同步：只依公開中的子事件組出系列頁；沒有公開的子事件時不公開或撤下；內容沒變不升版；
  已下架的不覆蓋；系列刪除時公開狀態一併刪除；
- 公開提醒（`remind`）：疑似指控與推測內容第一次公開、改變時產生；只有件數改變時不重複；
- 讀取時再比對子事件的公開狀態：事件下架後立即不再出現在系列頁與快報的「第 N 起」；
- 快報列表與全文附所屬系列；前後比較只並列平均與固定說明；管理端列表含系列。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.domain.states import EventStatus
from app.main import create_app
from app.models.event import Event
from app.models.publication import Publication, PublicationVersion
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.models.series import EventSeries, EventSeriesMember, SeriesAnalysis
from app.publishing.sync import REMIND, SyncAction, sync_all, sync_series_publication
from tests.integration.test_public_api import add_event, add_series, publish

pytestmark = pytest.mark.anyio

PUBLIC = "/api/v1/public"
ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))


def _app(session: AsyncSession, principal: Principal | None = None) -> httpx2.AsyncClient:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://test")


@pytest.fixture()
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    async with _app(session) as c:
        yield c


@pytest.fixture()
async def admin(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    async with _app(session, ADMIN) as c:
        yield c


def analysis(series: EventSeries, events: list[Event], **overrides: Any) -> SeriesAnalysis:
    ids = [str(e.id) for e in events]
    values: dict[str, Any] = {
        "series_id": series.id,
        "status": "ok",
        "summary": "總圖書館接連傳出漏水。",
        "events": [],
        "location_groups": [{"label": "總圖書館", "reason": "x", "event_ids": ids}],
        "pattern_status": "ok",
        "patterns": [
            {"pattern_id": "p1", "description": "多在大雨後發生", "event_ids": ids, "quotes": []}
        ],
        "causes": [
            {"cause_id": "c1", "description": "排水管線老舊", "event_ids": ids[:1], "quotes": []}
        ],
        "measures": [],
        "trace_id": "t",
    }
    values.update(overrides)
    return SeriesAnalysis(**values)


async def seed(session: AsyncSession) -> tuple[EventSeries, list[Event]]:
    first = await add_event(session, title="總圖書館三樓漏水")
    second = await add_event(
        session, title="總圖書館二樓滴水", status=EventStatus.UNVERIFIED, confirm=None
    )
    second.occurrence_start_date = date(2026, 10, 28)
    series = await add_series(session, [first, second])
    session.add(analysis(series, [first, second]))
    await session.commit()
    return series, [first, second]


async def series_pub(session: AsyncSession, series: EventSeries) -> Publication:
    pub = await session.scalar(select(Publication).where(Publication.series_id == series.id))
    assert pub is not None
    await session.refresh(pub)
    return pub


async def versions(session: AsyncSession, pub: Publication) -> list[PublicationVersion]:
    return list(
        await session.scalars(
            select(PublicationVersion)
            .where(PublicationVersion.publication_id == pub.id)
            .order_by(PublicationVersion.version)
        )
    )


async def event_pub_id(session: AsyncSession, event: Event) -> str:
    return str(await session.scalar(select(Publication.id).where(Publication.event_id == event.id)))


# ---------- 同步 ----------


async def test_series_page_is_published_from_public_members(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    series, (first, _second) = await seed(session)
    await sync_all(session)
    pub = await series_pub(session, series)
    assert (pub.level, pub.state, pub.version) == ("content", "published", 1)
    assert pub.needs_review is False
    data = (await client.get(f"{PUBLIC}/series/{pub.id}")).json()["data"]
    assert data["name"] == "總圖書館漏水"
    assert data["event_count"] == 2
    assert [(e["ordinal"], e["title"]) for e in data["events"]] == [
        (1, "總圖書館三樓漏水"),
        (2, "總圖書館二樓滴水"),
    ]
    assert [e["verification_label"] for e in data["events"]] == ["已查證", "尚未查證"]
    assert data["events"][0]["id"] == await event_pub_id(session, first)
    assert data["patterns"][0]["label"] == "推測，尚未查證"
    assert data["patterns"][0]["related_ids"] == [e["id"] for e in data["events"]]
    assert data["causes"][0]["label"] == "推測，尚未查證"
    assert sum(p["count"] for p in data["timeline"]["points"]) == 2


async def test_series_without_public_members_is_not_published_or_is_withdrawn(
    session: AsyncSession,
) -> None:
    event = await add_event(session, status=EventStatus.FALSE)
    series = await add_series(session, [event])
    await publish(session, event)
    assert await sync_series_publication(session, series.id) is SyncAction.NONE

    ok = await add_event(session, title="總圖書館三樓漏水")
    other = await add_series(session, [ok], title="另一個系列")
    await sync_all(session)
    pub = await series_pub(session, other)
    ok.status = EventStatus.FALSE.value
    await session.commit()
    await sync_all(session)
    await session.refresh(pub)
    assert (pub.state, pub.level) == ("withdrawn", "none")


async def test_unchanged_series_keeps_version_and_new_event_updates_without_reminder(
    session: AsyncSession,
) -> None:
    series, _events = await seed(session)
    await sync_all(session)
    pub = await series_pub(session, series)
    [v1] = await versions(session, pub)
    assert REMIND in v1.reasons  # 推測內容第一次公開
    assert await sync_series_publication(session, series.id) is SyncAction.NONE

    third = await add_event(session, title="總圖書館一樓積水")
    session.add(
        EventSeriesMember(
            event_id=third.id, series_id=series.id, method="ai", joined_at=datetime.now(UTC)
        )
    )
    await session.commit()
    await sync_all(session)
    pub = await series_pub(session, series)
    v1, v2 = await versions(session, pub)
    assert v2.content is not None and v2.content["event_count"] == 3
    assert REMIND not in v2.reasons  # 推測內容沒變，不重複提醒


async def test_changed_speculation_reminds_again(session: AsyncSession) -> None:
    series, events = await seed(session)
    await sync_all(session)
    session.add(
        analysis(
            series,
            events,
            causes=[
                {
                    "cause_id": "c1",
                    "description": "屋頂排水孔堵塞",
                    "event_ids": [str(events[0].id)],
                }
            ],
            created_at=datetime.now(UTC) + timedelta(seconds=1),
        )
    )
    await session.commit()
    await sync_all(session)
    pub = await series_pub(session, series)
    _v1, v2 = await versions(session, pub)
    assert REMIND in v2.reasons
    assert v2.content is not None and v2.content["causes"][0]["description"] == "屋頂排水孔堵塞"


async def test_allegation_event_reminds_once(session: AsyncSession) -> None:
    event = await add_event(session, title="警衛在停車場偷走機車", event_type="security")
    await sync_all(session)
    pub = await session.scalar(select(Publication).where(Publication.event_id == event.id))
    assert pub is not None and pub.needs_review
    [v1] = await versions(session, pub)
    assert {REMIND, "possible_allegation"} <= set(v1.reasons)
    await sync_all(session)
    assert len(await versions(session, pub)) == 1


async def test_taken_down_series_is_kept_down_and_restore_resyncs(
    admin: httpx2.AsyncClient, client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    series, _ = await seed(session)
    await sync_all(session)
    pub = await series_pub(session, series)
    assert (await admin.post(f"/api/v1/admin/publications/{pub.id}/takedown")).status_code == 200
    assert (await client.get(f"{PUBLIC}/series/{pub.id}")).status_code == 404
    assert await sync_series_publication(session, series.id) is SyncAction.KEPT_DOWN
    res = await admin.post(f"/api/v1/admin/publications/{pub.id}/restore")
    assert res.status_code == 200
    assert (await client.get(f"{PUBLIC}/series/{pub.id}")).status_code == 200


async def test_deleted_series_removes_its_publication(session: AsyncSession) -> None:
    series, _ = await seed(session)
    await sync_all(session)
    await session.delete(series)
    await session.commit()
    count = await session.scalar(
        select(func.count()).select_from(Publication).where(Publication.series_id.is_not(None))
    )
    assert count == 0


# ---------- 公開 API ----------


async def test_taken_down_event_disappears_from_series_page_immediately(
    admin: httpx2.AsyncClient, client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    series, (first, _second) = await seed(session)
    await sync_all(session)
    pub = await series_pub(session, series)
    first_pub = await event_pub_id(session, first)
    assert (await admin.post(f"/api/v1/admin/publications/{first_pub}/takedown")).status_code == 200
    data = (await client.get(f"{PUBLIC}/series/{pub.id}")).json()["data"]
    assert [e["title"] for e in data["events"]] == ["總圖書館二樓滴水"]
    assert [e["ordinal"] for e in data["events"]] == [1]
    assert first_pub not in str(data)
    feed = (await client.get(f"{PUBLIC}/feed")).json()["data"]
    [item] = [i for i in feed if i["title"] == "總圖書館二樓滴水"]
    assert item["series"]["ordinal"] == 1 and item["series"]["event_count"] == 1


async def test_feed_and_event_detail_link_to_series(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    series, (first, _second) = await seed(session)
    alone = await add_event(session, title="工程五館停電")
    await sync_all(session)
    pub = await series_pub(session, series)
    feed = {i["title"]: i for i in (await client.get(f"{PUBLIC}/feed")).json()["data"]}
    assert feed["總圖書館二樓滴水"]["series"] == {
        "id": str(pub.id),
        "name": "總圖書館漏水",
        "ordinal": 2,
        "event_count": 2,
    }
    assert feed["工程五館停電"]["series"] is None  # 不在任何系列
    detail = (await client.get(f"{PUBLIC}/events/{await event_pub_id(session, first)}")).json()
    assert detail["data"]["series"]["ordinal"] == 1
    assert alone.id  # 只是確認事件已建立


async def test_series_detail_rejects_event_publication_and_internal_ids(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    series, (first, _) = await seed(session)
    await sync_all(session)
    for target in (await event_pub_id(session, first), str(series.id), str(uuid.uuid4())):
        res = await client.get(f"{PUBLIC}/series/{target}")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"
    # 系列頁不會出現在事件的詳細頁與列表
    pub = await series_pub(session, series)
    assert (await client.get(f"{PUBLIC}/events/{pub.id}")).status_code == 404
    feed = (await client.get(f"{PUBLIC}/feed")).json()["data"]
    assert str(pub.id) not in {i["id"] for i in feed}


async def test_series_progress_and_before_after_comparison(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    series, (_first, _) = await seed(session)
    action = ResponseAction(
        series_id=series.id,
        origin="manual",
        title="雨季前清理排水孔",
        owner="林組長",
        state="handling",
    )
    session.add(action)
    await session.flush()
    session.add(
        ResponseActionTransition(
            action_id=action.id,
            from_state="discovered",
            to_state="handling",
            actor="admin:u-1",
            request_id="r",
        )
    )
    await session.commit()
    await sync_all(session)
    pub = await series_pub(session, series)
    data = (await client.get(f"{PUBLIC}/series/{pub.id}")).json()["data"]
    assert [a["title"] for a in data["progress"]] == ["雨季前清理排水孔"]
    assert "林組長" not in str(data)
    [comparison] = data["comparisons"]
    assert comparison["status"] == "insufficient"
    assert comparison["message"] == "資料還不夠"
    assert comparison["caveat"].startswith("變少不一定是措施的功勞")
    assert "change_ratio" not in comparison
    assert [m["kind"] for m in data["timeline"]["markers"]] == ["started"]


async def test_admin_lists_series_publications_and_reminders(
    admin: httpx2.AsyncClient, session: AsyncSession
) -> None:
    series, _ = await seed(session)
    await sync_all(session)
    data = (await admin.get("/api/v1/admin/publications")).json()["data"]
    [row] = [d for d in data if d["subject"] == "series"]
    assert (row["series_id"], row["event_id"]) == (str(series.id), None)
    assert row["event_title"] == row["preview"] == "總圖書館漏水"
    reminders = (await admin.get("/api/v1/admin/publications/reminders")).json()["data"]
    [reminder] = [r for r in reminders if r["subject"] == "series"]
    assert reminder["flags"] == ["speculation"]
    assert reminder["current"] and reminder["can_take_down"]


async def test_reminders_require_login(client: httpx2.AsyncClient) -> None:
    assert (await client.get("/api/v1/admin/publications/reminders")).status_code == 401


async def test_public_resilience_ignores_unverified_content(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    """P8-T06：未查證事件公開為事件內容，但公開版韌性成績單仍只統計已查證的事件。"""
    await add_event(session, title="總圖書館三樓漏水")
    await add_event(session, title="總圖書館二樓滴水", status=EventStatus.UNVERIFIED, confirm=None)
    await sync_all(session)
    for event in await session.scalars(select(Event)):
        event.occurrence_start_date = datetime.now(UTC).date()
    await session.commit()
    data = (await client.get(f"{PUBLIC}/resilience")).json()["data"]
    assert data["event_count"] == 1
