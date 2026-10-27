"""P4-T10：通知中心的再發生提醒（GET /api/v1/admin/recurrences）。

- 讀取時依目前的事件與改善措施重算，不另存資料表；
- 同地點、同類別在時間窗內再發生 → 提醒「可能不是單純個案，要不要整批檢查？」，附整組事件；
- 改善措施達到「改進」之後再發生 → 標示 after_improvement；
- 不同地點、查證為不實的事件不列入；需要 alerts:read_all。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.main import create_app
from app.models.event import Event, Location
from app.models.response_action import ResponseAction, ResponseActionTransition
from tests.integration.test_pipeline import ADMIN

pytestmark = pytest.mark.anyio

TAIPEI = ZoneInfo("Asia/Taipei")
URL = "/api/v1/admin/recurrences"


def today() -> date:
    return datetime.now(TAIPEI).date()


@pytest.fixture()
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    app.dependency_overrides[get_principal] = lambda: ADMIN
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def location(session: AsyncSession, name: str) -> Location:
    loc = Location(name=name)
    session.add(loc)
    await session.flush()
    return loc


async def event(
    session: AsyncSession,
    loc: Location | None,
    days_ago: int,
    *,
    type: str = "facility",
    status: str = "unverified",
    title: str = "總圖停電",
) -> Event:
    e = Event(
        title=title,
        type=type,
        status=status,
        location_id=loc.id if loc else None,
        occurrence_start_date=today() - timedelta(days=days_ago),
    )
    session.add(e)
    await session.flush()
    return e


async def improve(session: AsyncSession, e: Event, days_ago: int) -> None:
    """手動建立一項已達「改進」的改善措施（含狀態歷史）。"""
    action = ResponseAction(
        event_id=e.id, origin="manual", title="加裝不斷電設備", state="improved"
    )
    session.add(action)
    await session.flush()
    at = datetime.now(TAIPEI) - timedelta(days=days_ago)
    path = [None, "discovered", "handling", "recovered", "improved"]
    for prev, nxt in pairwise(path):
        session.add(
            ResponseActionTransition(
                action_id=action.id,
                from_state=prev,
                to_state=nxt,
                actor="admin:u-1",
                request_id=uuid.uuid4().hex,
                created_at=at,
            )
        )
    await session.flush()


async def test_recurrence_within_window_is_listed(
    session: AsyncSession, client: httpx2.AsyncClient
) -> None:
    lib = await location(session, "總圖書館")
    first = await event(session, lib, 10)
    second = await event(session, lib, 1)
    await session.commit()

    resp = await client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    [group] = body["data"]
    assert group["location_id"] == str(lib.id)
    assert group["location_name"] == "總圖書館"
    assert group["event_type"] == "facility"
    assert group["event_type_label"] == "設施"
    assert group["reasons"] == ["within_window"]
    assert group["message"] == "可能不是單純個案，要不要整批檢查？"
    assert group["latest_on"] == (today() - timedelta(days=1)).isoformat()
    assert [e["id"] for e in group["events"]] == [str(second.id), str(first.id)]
    assert group["events"][0]["title"] == "總圖停電"
    assert group["events"][0]["improved_on"] is None
    assert body["meta"]["policy_version"]


async def test_other_location_type_and_false_are_not_listed(
    session: AsyncSession, client: httpx2.AsyncClient
) -> None:
    lib = await location(session, "總圖書館")
    dorm = await location(session, "女十四舍")
    await event(session, lib, 5)
    await event(session, dorm, 1)
    await event(session, lib, 2, type="security", title="總圖腳踏車失竊")
    await event(session, lib, 1, status="false")
    await event(session, None, 1)
    await session.commit()

    resp = await client.get(URL)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_recurrence_after_improvement_is_marked(
    session: AsyncSession, client: httpx2.AsyncClient
) -> None:
    lib = await location(session, "總圖書館")
    old = await event(session, lib, 200, status="resolved")
    await improve(session, old, 100)
    again = await event(session, lib, 0)
    await session.commit()

    resp = await client.get(URL)
    [group] = resp.json()["data"]
    assert group["reasons"] == ["after_improvement"]
    assert [e["id"] for e in group["events"]] == [str(again.id), str(old.id)]
    assert group["events"][1]["improved_on"] == (today() - timedelta(days=100)).isoformat()
    assert group["events"][1]["status"] == "resolved"


async def test_requires_alerts_read_permission(session: AsyncSession) -> None:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    ingestion = Principal(
        kind="service", subject="ingestion", roles=frozenset({Role.SERVICE_INGESTION})
    )
    app.dependency_overrides[get_db_session] = test_session
    app.dependency_overrides[get_principal] = lambda: ingestion
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(URL)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
