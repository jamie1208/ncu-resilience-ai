"""P6-T05：校園韌性成績單 API。

- `GET /api/v1/admin/resilience`：後台報告（全部事件），附時間窗、樣本量、覆蓋率、來源限制；
  需要登入與 reports:read_internal；可調整時間窗；
- `GET /api/v1/public/resilience`：公開版，只統計以「事件內容」公開中的事件；只有彙總數字與
  限制說明，不含事件、地點、措施或任何 ID；免登入、只有 GET、帶快取標頭；
- 資料不足時分數為 null（N/A），不產生假精確分數。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.main import create_app
from app.models.event import Event, Location
from app.models.publication import Publication
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.resilience.cri import REPORTS_NOT_RISK
from tests.integration.test_pipeline import ADMIN

pytestmark = pytest.mark.anyio

TAIPEI = ZoneInfo("Asia/Taipei")
ADMIN_URL = "/api/v1/admin/resilience"
PUBLIC_URL = "/api/v1/public/resilience"
SECRET_TITLE = "總圖三樓漏水"
SECRET_LOCATION = "總圖書館"


def today() -> date:
    return datetime.now(TAIPEI).date()


def _app(session: AsyncSession, *, logged_in: bool) -> httpx2.AsyncClient:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    if logged_in:
        app.dependency_overrides[get_principal] = lambda: ADMIN
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://test")


@pytest.fixture()
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    async with _app(session, logged_in=True) as c:
        yield c


@pytest.fixture()
async def anonymous(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    async with _app(session, logged_in=False) as c:
        yield c


async def seed_event(
    session: AsyncSession, loc: Location, *, days_ago: int, publish: str | None
) -> Event:
    """建立一個已處理、已恢復、已改進的事件；`publish` 為公開程度（None 為不公開）。"""
    occurred = today() - timedelta(days=days_ago)
    created = datetime.combine(occurred, datetime.min.time(), TAIPEI) + timedelta(hours=10)
    e = Event(
        title=SECRET_TITLE,
        type="facility",
        status="verified",
        location_id=loc.id,
        occurrence_start_date=occurred,
        created_at=created,
    )
    session.add(e)
    await session.flush()
    action = ResponseAction(event_id=e.id, origin="manual", title="更換水管", state="improved")
    session.add(action)
    await session.flush()
    steps = [(None, "discovered", 0), ("discovered", "handling", 2), ("handling", "recovered", 50)]
    steps.append(("recovered", "improved", 60))
    for prev, nxt, hours in steps:
        session.add(
            ResponseActionTransition(
                action_id=action.id,
                from_state=prev,
                to_state=nxt,
                actor="admin:u-1",
                request_id=uuid.uuid4().hex,
                created_at=created + timedelta(hours=hours),
            )
        )
    if publish is not None:
        session.add(
            Publication(
                event_id=e.id,
                level=publish,
                state="published",
                version=1,
                policy_version="test",
                reasons=[],
            )
        )
    await session.flush()
    return e


async def seed(session: AsyncSession, *, published: int, hidden: int) -> None:
    for i in range(published):
        loc = Location(name=f"{SECRET_LOCATION}{i}")
        session.add(loc)
        await session.flush()
        await seed_event(session, loc, days_ago=10 + i, publish="content")
    for i in range(hidden):
        loc = Location(name=f"女十四舍{i}")
        session.add(loc)
        await session.flush()
        await seed_event(session, loc, days_ago=20 + i, publish="safety_notice" if i % 2 else None)
    await session.commit()


def _dims(body: dict[str, object]) -> dict[str, dict[str, object]]:
    data = body["data"]
    assert isinstance(data, dict)
    return {d["key"]: d for d in data["dimensions"]}


# --- 後台 -------------------------------------------------------------------------------


async def test_admin_without_data_is_na(client: httpx2.AsyncClient) -> None:
    res = await client.get(ADMIN_URL)
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    assert data["score"] is None
    assert data["scope"] == "all"
    assert data["event_count"] == 0
    assert data["window"]["days"] == 180
    assert data["window"]["end"] == today().isoformat()
    assert REPORTS_NOT_RISK in data["limitations"]
    for d in data["dimensions"]:
        assert d["score"] is None
        assert d["reason"] == "insufficient_samples"
        assert d["coverage"] is None


async def test_admin_counts_all_events(session: AsyncSession, client: httpx2.AsyncClient) -> None:
    await seed(session, published=3, hidden=3)
    res = await client.get(ADMIN_URL)
    data = res.json()["data"]
    assert data["event_count"] == 6
    assert data["action_count"] == 6
    assert data["min_samples"] == 5
    assert data["weights"] == [0.25, 0.25, 0.25, 0.25]
    dims = _dims(res.json())
    assert list(dims) == ["detection", "response", "recovery", "adaptation"]
    assert dims["detection"]["score"] == 100
    assert dims["response"]["metrics"][0]["value"] == round(2 / 24, 2)
    assert dims["recovery"]["metrics"][0]["value"] == 2
    assert dims["adaptation"]["metrics"][0]["value"] == 1.0
    assert all(isinstance(d["score"], int) for d in dims.values())
    assert isinstance(data["score"], int)
    assert data["dimensions"][0]["label"] == "發現"


async def test_admin_window_is_adjustable(
    session: AsyncSession, client: httpx2.AsyncClient
) -> None:
    await seed(session, published=3, hidden=3)
    res = await client.get(ADMIN_URL, params={"window_days": 15})
    data = res.json()["data"]
    assert data["window"]["days"] == 15
    assert data["window"]["start"] == (today() - timedelta(days=14)).isoformat()
    assert data["event_count"] == 3
    assert data["score"] is None


async def test_admin_rejects_bad_window(client: httpx2.AsyncClient) -> None:
    for bad in (0, 6, 800):
        res = await client.get(ADMIN_URL, params={"window_days": bad})
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_admin_requires_login(anonymous: httpx2.AsyncClient) -> None:
    res = await anonymous.get(ADMIN_URL)
    assert res.status_code == 401


# --- 公開版 -----------------------------------------------------------------------------


async def test_public_without_data_is_na(anonymous: httpx2.AsyncClient) -> None:
    res = await anonymous.get(PUBLIC_URL)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["score"] is None
    assert data["scope"] == "published"
    assert REPORTS_NOT_RISK in data["limitations"]
    assert all(d["score"] is None for d in data["dimensions"])
    assert "public" in res.headers["cache-control"]
    assert res.headers["etag"]


async def test_public_only_counts_published_content(
    session: AsyncSession, anonymous: httpx2.AsyncClient
) -> None:
    await seed(session, published=4, hidden=6)
    data = (await anonymous.get(PUBLIC_URL)).json()["data"]
    assert data["event_count"] == 4
    assert data["score"] is None  # 公開事件不足 5 件 → N/A


async def test_public_scores_with_enough_published(
    session: AsyncSession, anonymous: httpx2.AsyncClient
) -> None:
    await seed(session, published=5, hidden=2)
    res = await anonymous.get(PUBLIC_URL)
    data = res.json()["data"]
    assert data["event_count"] == 5
    assert isinstance(data["score"], int)


async def test_public_has_only_aggregates(
    session: AsyncSession, anonymous: httpx2.AsyncClient
) -> None:
    await seed(session, published=5, hidden=2)
    res = await anonymous.get(PUBLIC_URL)
    text = res.text
    assert SECRET_TITLE not in text
    assert SECRET_LOCATION not in text
    assert "更換水管" not in text
    raw = json.dumps(res.json())
    for e_id in (await session.execute(select(Event.id))).scalars():
        assert str(e_id) not in raw
    allowed = {
        "score",
        "scope",
        "window",
        "event_count",
        "action_count",
        "min_samples",
        "weights",
        "dimensions",
        "limitations",
    }
    assert set(res.json()["data"]) == allowed


async def test_public_window_is_fixed(anonymous: httpx2.AsyncClient) -> None:
    res = await anonymous.get(PUBLIC_URL, params={"window_days": 15})
    assert res.json()["data"]["window"]["days"] == 180
