"""P5-T14：一鍵下架（app/publishing/takedown.py、/api/v1/admin/publications）。

- 下架後公開 API 立即不再回傳（列表、詳細頁、分類計數），同步也不會重新公開；
- 快取失效：下架前拿到的 ETag 不再得到 304，詳細頁回 404 且 `no-store`；
- 每次下架寫入 publication_takedowns 與稽核 `publication.take_down`（同一個 request_id）；
- 重新上架只把狀態交回公開規則判斷（不直接公開），寫入稽核 `publication.restore`；
- 公開內容列表與權限（下架需要 `alerts:retract`，重新上架需要 `events:verify`）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.domain.states import EventStatus
from app.main import create_app
from app.models import AuditLog
from app.models.publication import PublicationTakedown
from app.publishing.sync import SyncAction
from app.publishing.takedown import (
    TakedownConflictError,
    TakedownNotFoundError,
    restore,
    take_down,
)
from tests.integration.test_public_api import (
    ADMIN,
    PUBLIC,
    add_event,
    publication_of,
    publish,
    seed_public,
)

pytestmark = pytest.mark.anyio

ADMIN_API = "/api/v1/admin/publications"
NOTIFIER = Principal(kind="service", subject="notifier", roles=frozenset({Role.SERVICE_NOTIFIER}))


def _client(session: AsyncSession, principal: Principal | None) -> httpx2.AsyncClient:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://test")


@pytest.fixture()
async def admin(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(session, ADMIN) as c:
        yield c


@pytest.fixture()
async def public(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(session, None) as c:
        yield c


async def _audits(session: AsyncSession, action: str) -> list[AuditLog]:
    return list(
        await session.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
        )
    )


# ---------- 服務層 ----------


async def test_take_down_marks_publication_and_records(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    result = await take_down(session, pub.id, ADMIN, reason="內容待確認")
    await session.commit()
    assert (result.previous, result.state) == ("published", "taken_down")
    pub = await publication_of(session, event)
    assert pub.state == "taken_down"
    assert pub.taken_down_at is not None
    record = await session.scalar(
        select(PublicationTakedown).where(PublicationTakedown.publication_id == pub.id)
    )
    assert record is not None
    assert (record.version, record.reason, record.actor) == (1, "內容待確認", ADMIN.actor)
    [log] = await _audits(session, "publication.take_down")
    assert log.target == f"publication:{pub.id}"
    assert log.actor == ADMIN.actor
    assert log.request_id == record.request_id
    assert log.meta["version"] == 1
    assert log.meta["from"] == "published"
    assert log.meta["reason"] == "內容待確認"


async def test_withdrawn_publication_can_be_taken_down(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    event.status = EventStatus.FALSE.value
    await session.commit()
    await publish(session, event)
    pub = await publication_of(session, event)
    result = await take_down(session, pub.id, ADMIN)
    await session.commit()
    assert result.previous == "withdrawn"
    event.status = EventStatus.VERIFIED.value
    await session.commit()
    assert await publish(session, event) is SyncAction.KEPT_DOWN


async def test_take_down_twice_conflicts(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    await take_down(session, pub.id, ADMIN)
    await session.commit()
    with pytest.raises(TakedownConflictError):
        await take_down(session, pub.id, ADMIN)


async def test_take_down_unknown_publication(session: AsyncSession) -> None:
    with pytest.raises(TakedownNotFoundError):
        await take_down(session, uuid.uuid4(), ADMIN)


async def test_sync_does_not_republish_after_take_down(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    await take_down(session, pub.id, ADMIN)
    event.summary = "新的摘要"
    await session.commit()
    assert await publish(session, event) is SyncAction.KEPT_DOWN
    assert (await publication_of(session, event)).state == "taken_down"


async def test_restore_hands_back_to_policy(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    await take_down(session, pub.id, ADMIN)
    await session.commit()
    result = await restore(session, pub.id, ADMIN)
    await session.commit()
    assert (result.previous, result.state) == ("taken_down", "published")
    pub = await publication_of(session, event)
    assert pub.state == "published"
    assert pub.taken_down_at is None
    [log] = await _audits(session, "publication.restore")
    assert log.meta["sync"] == "published"


async def test_restore_does_not_publish_what_policy_rejects(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    await take_down(session, pub.id, ADMIN)
    event.status = EventStatus.FALSE.value
    await session.commit()
    result = await restore(session, pub.id, ADMIN)
    await session.commit()
    assert result.state == "withdrawn"


async def test_restore_requires_taken_down(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    with pytest.raises(TakedownConflictError):
        await restore(session, pub.id, ADMIN)


# ---------- 公開端立即消失與快取失效 ----------


async def test_public_api_stops_returning_immediately(
    admin: httpx2.AsyncClient, public: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["content"])
    assert (await public.get(f"{PUBLIC}/events/{pub.id}")).status_code == 200

    res = await admin.post(f"{ADMIN_API}/{pub.id}/takedown", json={"reason": "緊急下架"})
    assert res.status_code == 200
    assert res.json()["data"] == {"id": str(pub.id), "state": "taken_down", "previous": "published"}

    detail = await public.get(f"{PUBLIC}/events/{pub.id}")
    assert detail.status_code == 404
    assert detail.headers["cache-control"] == "no-store"
    for path in ("feed", "events", "progress"):
        items = (await public.get(f"{PUBLIC}/{path}")).json()["data"]
        assert str(pub.id) not in {i["id"] for i in items}, path
    categories = (await public.get(f"{PUBLIC}/categories")).json()["data"]
    facility = next(c for c in categories if c["event_type"] == "facility")
    assert facility["content"] == 0


async def test_old_etag_no_longer_matches_after_take_down(
    admin: httpx2.AsyncClient, public: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["notice"])
    etags = {}
    for path in ("feed", "notices", "categories"):
        etags[path] = (await public.get(f"{PUBLIC}/{path}")).headers["etag"]

    assert (await admin.post(f"{ADMIN_API}/{pub.id}/takedown", json={})).status_code == 200

    for path, etag in etags.items():
        res = await public.get(f"{PUBLIC}/{path}", headers={"If-None-Match": etag})
        assert res.status_code == 200, path
        assert res.headers["etag"] != etag, path
    notices = (await public.get(f"{PUBLIC}/notices")).json()["data"]
    assert notices == []


# ---------- 管理端 API ----------


async def test_takedown_writes_audit_via_api(
    admin: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["content"])
    res = await admin.post(f"{ADMIN_API}/{pub.id}/takedown", json={"reason": "  誤判  "})
    assert res.status_code == 200
    [log] = await _audits(session, "publication.take_down")
    assert log.meta["reason"] == "誤判"
    assert log.meta["event_id"] == str(events["content"].id)


async def test_takedown_without_reason(admin: httpx2.AsyncClient, session: AsyncSession) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["content"])
    assert (await admin.post(f"{ADMIN_API}/{pub.id}/takedown")).status_code == 200
    [log] = await _audits(session, "publication.take_down")
    assert "reason" not in log.meta


async def test_takedown_reason_with_pii_is_rejected(
    admin: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["content"])
    res = await admin.post(f"{ADMIN_API}/{pub.id}/takedown", json={"reason": "聯絡 0912-345-678"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await publication_of(session, events["content"])).state == "published"


async def test_takedown_errors(admin: httpx2.AsyncClient, session: AsyncSession) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["content"])
    missing = await admin.post(f"{ADMIN_API}/{uuid.uuid4()}/takedown", json={})
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    await admin.post(f"{ADMIN_API}/{pub.id}/takedown", json={})
    again = await admin.post(f"{ADMIN_API}/{pub.id}/takedown", json={})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONFLICT"


async def test_restore_via_api(
    admin: httpx2.AsyncClient, public: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["content"])
    await admin.post(f"{ADMIN_API}/{pub.id}/takedown", json={})
    res = await admin.post(f"{ADMIN_API}/{pub.id}/restore")
    assert res.status_code == 200
    assert res.json()["data"] == {"id": str(pub.id), "state": "published", "previous": "taken_down"}
    assert (await public.get(f"{PUBLIC}/events/{pub.id}")).status_code == 200


async def test_list_publications(admin: httpx2.AsyncClient, session: AsyncSession) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["content"])
    await admin.post(f"{ADMIN_API}/{pub.id}/takedown", json={"reason": "誤判"})

    data = (await admin.get(ADMIN_API)).json()["data"]
    assert len(data) == 3
    by_event = {d["event_id"]: d for d in data}
    down = by_event[str(events["content"].id)]
    assert down["state"] == "taken_down"
    assert down["takedown_reason"] == "誤判"
    assert down["taken_down_at"] is not None
    assert down["event_title"] == "總圖書館三樓漏水"
    assert down["preview"] == "總圖書館三樓漏水"
    notice = by_event[str(events["notice"].id)]
    assert (notice["level"], notice["state"]) == ("safety_notice", "published")
    assert notice["preview"]
    assert notice["takedown_reason"] is None
    gone = by_event[str(events["gone"].id)]
    assert (gone["state"], gone["preview"]) == ("withdrawn", None)

    published = (await admin.get(ADMIN_API, params={"state": "published"})).json()["data"]
    assert [d["event_id"] for d in published] == [str(events["notice"].id)]
    one = (await admin.get(ADMIN_API, params={"event_id": str(events["gone"].id)})).json()
    assert [d["event_id"] for d in one["data"]] == [str(events["gone"].id)]


async def test_requires_login(public: httpx2.AsyncClient, session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    assert (await public.get(ADMIN_API)).status_code == 401
    assert (await public.post(f"{ADMIN_API}/{pub.id}/takedown", json={})).status_code == 401
    assert (await publication_of(session, event)).state == "published"


async def test_requires_permission(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    async with _client(session, NOTIFIER) as c:
        assert (await c.post(f"{ADMIN_API}/{pub.id}/takedown", json={})).status_code == 403
        assert (await c.post(f"{ADMIN_API}/{pub.id}/restore")).status_code == 403
    assert (await publication_of(session, event)).state == "published"
