"""P6-T01：關聯圖資料與 API。

- 關聯類型：same_event（其實是同一件事）、related_event（相關）、hypothesized_cause（from 可能
  是 to 的原因，推測，尚未查證）。邊存 evidence_ids、方法、分數與人工決策。
- 禁止 self-loop（service 與資料庫兩層）；同一對事件同一類型不論方向只能有一條。
- evidence_ids 必須是兩端事件之一的證據文章。
- 人工決策：pending → confirmed／rejected；已決定的關聯可回到 pending（駁回可回滾）。
  每次決策寫入稽核 `relation.decide`；被駁回的關聯預設不出現在關聯圖。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.main import create_app
from app.models import AuditLog, Event, EventEvidence, EventRelation
from app.relations.service import (
    RelationConflictError,
    RelationInvalidError,
    add_relation,
)
from tests.integration.test_event_report import source
from tests.integration.test_pipeline import ADMIN, add_item

pytestmark = pytest.mark.anyio


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


async def event(session: AsyncSession, title: str) -> tuple[Event, str]:
    """一個事件與一份證據；回傳事件與證據的 evidence_id。"""
    item = await add_item(session, await source(session, "ncu-announcements"), f"{title}\n\n內容")
    e = Event(title=title, type="facility", status="unverified")
    session.add(e)
    await session.flush()
    session.add(EventEvidence(event_id=e.id, source_item_id=item.id, support_type="mentions"))
    await session.commit()
    return e, item.id.hex


async def pair(session: AsyncSession) -> tuple[Event, str, Event, str]:
    a, ea = await event(session, "工程一館停電")
    b, eb = await event(session, "工程一館電梯受困")
    return a, ea, b, eb


async def relation(session: AsyncSession, **kw: Any) -> EventRelation:
    rel = await add_relation(session, **kw)
    await session.commit()
    return rel


async def decide(
    client: httpx2.AsyncClient, relation_id: uuid.UUID | str, decision: str, **extra: Any
) -> httpx2.Response:
    return await client.post(
        f"/api/v1/admin/relations/{relation_id}/decision",
        json={"decision": decision, **extra},
    )


# ---------- 禁止 self-loop ----------


async def test_service_rejects_self_loop(session: AsyncSession) -> None:
    a, ea = await event(session, "工程一館停電")
    with pytest.raises(RelationInvalidError):
        await add_relation(
            session,
            from_event_id=a.id,
            to_event_id=a.id,
            type="related_event",
            method="manual",
            evidence_ids=[ea],
        )
    assert (await session.scalars(select(EventRelation))).all() == []


async def test_database_rejects_self_loop_for_new_type(session: AsyncSession) -> None:
    a, _ = await event(session, "工程一館停電")
    session.add(EventRelation(from_event_id=a.id, to_event_id=a.id, type="hypothesized_cause"))
    with pytest.raises(IntegrityError):
        await session.flush()


# ---------- 新增關聯的規則 ----------


async def test_relation_stores_evidence_method_score_and_pending_decision(
    session: AsyncSession,
) -> None:
    a, ea, b, eb = await pair(session)
    rel = await relation(
        session,
        from_event_id=a.id,
        to_event_id=b.id,
        type="hypothesized_cause",
        method="manual",
        score=0.6,
        evidence_ids=[ea, eb],
    )
    assert rel.evidence == [ea, eb]
    assert rel.method == "manual"
    assert rel.score == 0.6
    assert rel.decision == "pending"
    assert rel.decided_at is None and rel.decided_by is None


async def test_reverse_duplicate_is_conflict(session: AsyncSession) -> None:
    a, ea, b, eb = await pair(session)
    await relation(
        session,
        from_event_id=a.id,
        to_event_id=b.id,
        type="same_event",
        method="ai",
        evidence_ids=[ea],
    )
    with pytest.raises(RelationConflictError):
        await add_relation(
            session,
            from_event_id=b.id,
            to_event_id=a.id,
            type="same_event",
            method="ai",
            evidence_ids=[eb],
        )


async def test_evidence_must_belong_to_endpoints(session: AsyncSession) -> None:
    a, ea, b, _ = await pair(session)
    _, other = await event(session, "中大湖步道積水")
    with pytest.raises(RelationInvalidError):
        await add_relation(
            session,
            from_event_id=a.id,
            to_event_id=b.id,
            type="related_event",
            method="ai",
            evidence_ids=[ea, other],
        )


async def test_hypothesized_cause_requires_evidence(session: AsyncSession) -> None:
    a, _, b, _ = await pair(session)
    with pytest.raises(RelationInvalidError):
        await add_relation(
            session,
            from_event_id=a.id,
            to_event_id=b.id,
            type="hypothesized_cause",
            method="manual",
            evidence_ids=[],
        )


async def test_pipeline_rows_default_to_ai_and_pending(session: AsyncSession) -> None:
    """P2-T09 管線寫入的 related_event 不帶方法與決策，使用預設值。"""
    a, _, b, _ = await pair(session)
    session.add(EventRelation(from_event_id=a.id, to_event_id=b.id, type="related_event"))
    await session.commit()
    rel = await session.scalar(select(EventRelation))
    assert rel is not None
    await session.refresh(rel)
    assert rel.method == "ai"
    assert rel.decision == "pending"


# ---------- 查詢 ----------


async def test_graph_returns_nodes_and_edges(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    a, ea, b, eb = await pair(session)
    c, ec = await event(session, "中大湖步道積水")
    ab = await relation(
        session,
        from_event_id=a.id,
        to_event_id=b.id,
        type="hypothesized_cause",
        method="manual",
        score=0.6,
        evidence_ids=[ea, eb],
    )
    await relation(
        session,
        from_event_id=b.id,
        to_event_id=c.id,
        type="related_event",
        method="ai",
        score=0.4,
        evidence_ids=[ec],
    )

    res = await client.get("/api/v1/admin/relations", params={"event_id": str(a.id)})
    assert res.status_code == 200
    data = res.json()["data"]
    assert {n["id"] for n in data["nodes"]} == {str(a.id), str(b.id)}
    node = next(n for n in data["nodes"] if n["id"] == str(a.id))
    assert node["title"] == "工程一館停電"
    assert node["status"] == "unverified"
    [edge] = data["edges"]
    assert edge["id"] == str(ab.id)
    assert edge["source"] == str(a.id) and edge["target"] == str(b.id)
    assert edge["type"] == "hypothesized_cause"
    assert edge["method"] == "manual"
    assert edge["score"] == 0.6
    assert edge["evidence_ids"] == [ea, eb]
    assert edge["decision"] == "pending"

    everything = (await client.get("/api/v1/admin/relations")).json()["data"]
    assert len(everything["edges"]) == 2
    assert len(everything["nodes"]) == 3

    only_related = (
        await client.get("/api/v1/admin/relations", params={"type": "related_event"})
    ).json()["data"]
    assert [e["type"] for e in only_related["edges"]] == ["related_event"]


async def test_get_one_relation_and_not_found(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    a, ea, b, _ = await pair(session)
    rel = await relation(
        session,
        from_event_id=a.id,
        to_event_id=b.id,
        type="related_event",
        method="ai",
        evidence_ids=[ea],
    )
    res = await client.get(f"/api/v1/admin/relations/{rel.id}")
    assert res.status_code == 200
    assert res.json()["data"]["edge"]["id"] == str(rel.id)
    assert {n["id"] for n in res.json()["data"]["nodes"]} == {str(a.id), str(b.id)}

    missing = await client.get(f"/api/v1/admin/relations/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


# ---------- 人工確認、駁回與回滾 ----------


async def test_confirm_records_decision_and_audit(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    a, ea, b, _ = await pair(session)
    rel = await relation(
        session,
        from_event_id=a.id,
        to_event_id=b.id,
        type="related_event",
        method="ai",
        evidence_ids=[ea],
    )
    res = await decide(client, rel.id, "confirmed", note="同一棟大樓同一天")
    assert res.status_code == 200
    edge = res.json()["data"]["edge"]
    assert edge["decision"] == "confirmed"
    assert edge["decided_by"] == ADMIN.actor
    assert edge["decided_at"]
    assert edge["decision_note"] == "同一棟大樓同一天"

    log = await session.scalar(select(AuditLog).where(AuditLog.action == "relation.decide"))
    assert log is not None
    assert log.target == f"event_relation:{rel.id}"
    assert log.meta["from"] == "pending"
    assert log.meta["to"] == "confirmed"
    assert log.meta["type"] == "related_event"


async def test_reject_hides_edge_and_can_be_rolled_back(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    a, ea, b, eb = await pair(session)
    rel = await relation(
        session,
        from_event_id=a.id,
        to_event_id=b.id,
        type="hypothesized_cause",
        method="manual",
        score=0.6,
        evidence_ids=[ea, eb],
    )

    rejected = await decide(client, rel.id, "rejected", note="時間對不上")
    assert rejected.status_code == 200
    assert rejected.json()["data"]["edge"]["decision"] == "rejected"

    graph = (await client.get("/api/v1/admin/relations")).json()["data"]
    assert graph["edges"] == []
    shown = (
        await client.get("/api/v1/admin/relations", params={"include_rejected": "true"})
    ).json()["data"]
    assert [e["decision"] for e in shown["edges"]] == ["rejected"]

    # 回滾：回到待確認，邊與證據都還在
    rolled = await decide(client, rel.id, "pending")
    assert rolled.status_code == 200
    edge = rolled.json()["data"]["edge"]
    assert edge["decision"] == "pending"
    assert edge["decided_at"] is None and edge["decided_by"] is None
    assert edge["decision_note"] is None
    assert edge["evidence_ids"] == [ea, eb]
    assert edge["score"] == 0.6

    graph = (await client.get("/api/v1/admin/relations")).json()["data"]
    assert [e["id"] for e in graph["edges"]] == [str(rel.id)]

    # 回滾後可以重新決定
    assert (await decide(client, rel.id, "confirmed")).status_code == 200

    trail = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "relation.decide")
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    ).all()
    assert [(t.meta["from"], t.meta["to"]) for t in trail] == [
        ("pending", "rejected"),
        ("rejected", "pending"),
        ("pending", "confirmed"),
    ]


async def test_illegal_decision_change_is_conflict(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    a, ea, b, _ = await pair(session)
    rel = await relation(
        session,
        from_event_id=a.id,
        to_event_id=b.id,
        type="related_event",
        method="ai",
        evidence_ids=[ea],
    )
    same = await decide(client, rel.id, "pending")
    assert same.status_code == 409
    assert same.json()["error"]["code"] == "CONFLICT"

    assert (await decide(client, rel.id, "confirmed")).status_code == 200
    # 已確認不能直接改成駁回，要先回滾
    flip = await decide(client, rel.id, "rejected")
    assert flip.status_code == 409
    assert (await session.scalar(select(EventRelation.decision))) == "confirmed"


async def test_decision_validation(client: httpx2.AsyncClient, session: AsyncSession) -> None:
    a, ea, b, _ = await pair(session)
    rel = await relation(
        session,
        from_event_id=a.id,
        to_event_id=b.id,
        type="related_event",
        method="ai",
        evidence_ids=[ea],
    )
    bad = await decide(client, rel.id, "maybe")
    assert bad.status_code == 422

    pii = await decide(client, rel.id, "rejected", note="請聯絡 0912345678")
    assert pii.status_code == 422
    assert pii.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await session.scalar(select(EventRelation.decision))) == "pending"

    missing = await decide(client, uuid.uuid4(), "confirmed")
    assert missing.status_code == 404
