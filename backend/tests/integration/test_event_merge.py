"""P8-T01：手動合併／拆分事件（v1.0 F03，`app/events/merge.py`）。

- 合併：來源文章（證據）、改善措施與文章的 S01–S03 執行紀錄移到目標事件；被併入的事件改為
  archived、寫入 event_merges（導向目標）、小報告失效；公開快照只剩目標事件；
  S04 的候選召回不再找到被併入的事件，同一篇文章重跑時歸到目標事件；
- 拆分：把指定的來源文章移到新事件（同類型、地點、開始日期，狀態 analyzing），兩邊的事件說明
  依剩下的文章重新挑選，之後各自重跑 S05、S06；
- 稽核 `event.merge`、`event.split`；管理端 API 掛在 `/api/v1/admin/events/{id}/merge|split`。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import embedding_text, find_candidate_events
from app.ai.llm.fake_provider import FakeEmbeddingProvider
from app.api.deps import get_db_session, get_principal
from app.api.v1.admin.events import get_analysis_queue
from app.core.security import Principal, Role
from app.domain.states import EventStatus
from app.events.merge import (
    MergeConflictError,
    MergeInvalidError,
    MergeNotFoundError,
    merge_events,
    split_event,
)
from app.main import create_app
from app.models import AuditLog
from app.models.ai import AIAnalysisRun, EventRelation, RiskAssessment
from app.models.event import EVENT_EMBEDDING_DIM, Event, EventEvidence, EventMerge
from app.models.publication import Publication
from app.models.response_action import ResponseAction
from app.pipeline.analyze_item import Outcome, PipelineDeps, analyze_event, analyze_item
from app.reports.service import latest_report
from tests.integration.test_pipeline import (
    SkillScript,
    deps,
    risk,
    verification,
)
from tests.integration.test_public_api import (
    ADMIN,
    OFFICIAL,
    Evidence,
    add_event,
    measure,
    publication_of,
    publish,
)

pytestmark = pytest.mark.anyio

VECTOR = [1.0] + [0.0] * (EVENT_EMBEDDING_DIM - 1)
PUBLISHED = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)
REPOST = Evidence("facebook", "總圖三樓又在漏水，座位區都是水。")
OTHER = Evidence("facebook", "工程一館地下室積水，機車進不去。")


async def items_of(session: AsyncSession, event: Event) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(EventEvidence.source_item_id).where(EventEvidence.event_id == event.id)
    )
    return set(rows)


async def add_extractor_run(
    session: AsyncSession, event: Event, item_id: uuid.UUID, text: str
) -> AIAnalysisRun:
    """文章的 S02 執行紀錄（事件說明的出處）。"""
    run = AIAnalysisRun(
        event_id=event.id,
        skill_name="event-extractor",
        skill_version="1.0.0",
        schema_version="1",
        provider="fake",
        model_id="fake",
        status="succeeded",
        result={"summary": {"text": text, "evidence_ids": [item_id.hex]}},
        attempts=1,
        evidence_ids=[item_id.hex],
        trace_id="seed",
    )
    session.add(run)
    await session.flush()
    return run


async def two_events(session: AsyncSession) -> tuple[Event, Event]:
    """目標事件（官方公告、已公開）與重複的事件（社群轉貼、已公開、有改善措施與小報告）。"""
    target = await add_event(session, title="總圖書館三樓漏水")
    duplicate = await add_event(
        session,
        title="總圖三樓又漏水",
        summary="總圖三樓座位區漏水。",
        evidence=(REPOST,),
        measures=[measure("三樓座位區暫停開放")],
        actions=[{"title": "三樓座位區暫停開放", "measure": measure("三樓座位區暫停開放")}],
    )
    target.embedding = VECTOR
    duplicate.embedding = VECTOR
    await session.commit()
    return target, duplicate


async def merge(session: AsyncSession, source: Event, target: Event) -> EventMerge:
    record = await merge_events(session, ADMIN, source.id, target.id, request_id="req-merge")
    await session.commit()
    return record


# ---------- 合併 ----------


async def test_merge_moves_evidence_actions_and_marks_source(session: AsyncSession) -> None:
    target, duplicate = await two_events(session)
    target_items = await items_of(session, target)
    duplicate_items = await items_of(session, duplicate)
    (moved_item,) = duplicate_items
    s02 = await add_extractor_run(session, duplicate, moved_item, "總圖三樓座位區漏水。")
    await session.commit()

    record = await merge(session, duplicate, target)

    assert (record.merged_event_id, record.target_event_id) == (duplicate.id, target.id)
    assert record.decided_by == "admin:u-1"
    assert await items_of(session, target) == target_items | duplicate_items
    assert await items_of(session, duplicate) == set()
    actions = (await session.scalars(select(ResponseAction))).all()
    assert [a.event_id for a in actions] == [target.id]
    await session.refresh(s02)
    assert s02.event_id == target.id  # 文章的 S01–S03 紀錄跟著文章走
    await session.refresh(duplicate)
    assert duplicate.status == EventStatus.ARCHIVED.value
    report = await latest_report(session, duplicate.id)
    assert report is not None
    assert report.invalidated_reason == "event_merged"
    # 被併入事件的風險評估保留為歷史
    risks = await session.scalars(
        select(RiskAssessment.event_id).where(RiskAssessment.event_id == duplicate.id)
    )
    assert list(risks) == [duplicate.id]

    audit = (await session.scalars(select(AuditLog).where(AuditLog.action == "event.merge"))).one()
    assert audit.target == f"event:{duplicate.id}"
    assert audit.request_id == "req-merge"
    assert audit.meta["into"] == str(target.id)
    assert audit.meta["moved_evidence"] == 1
    assert audit.meta["moved_actions"] == 1


async def test_merge_keeps_shared_evidence_once(session: AsyncSession) -> None:
    target, duplicate = await two_events(session)
    (shared,) = await items_of(session, target)
    session.add(
        EventEvidence(event_id=duplicate.id, source_item_id=shared, support_type="mentions")
    )
    await session.commit()

    await merge(session, duplicate, target)

    assert len(await items_of(session, target)) == 2
    assert await items_of(session, duplicate) == set()


async def test_merge_picks_the_stronger_summary(session: AsyncSession) -> None:
    target = await add_event(session, summary="社群：總圖三樓漏水。", evidence=(REPOST,))
    official = await add_event(session, summary="總圖書館公告三樓漏水。", evidence=(OFFICIAL,))
    (repost_item,) = await items_of(session, target)
    (official_item,) = await items_of(session, official)
    await add_extractor_run(session, target, repost_item, "社群：總圖三樓漏水。")
    run = await add_extractor_run(session, official, official_item, "總圖書館公告三樓漏水。")
    target.summary_source_item_id = repost_item
    await session.commit()

    await merge(session, official, target)

    await session.refresh(target)
    assert target.summary == "總圖書館公告三樓漏水。"  # 官方來源優先
    assert target.summary_source_item_id == official_item
    assert target.summary_ai_run_id == run.id


async def test_merge_leaves_only_target_in_public_snapshot(session: AsyncSession) -> None:
    target, duplicate = await two_events(session)
    await publish(session, target)
    await publish(session, duplicate)
    assert (await publication_of(session, duplicate)).state == "published"

    await merge(session, duplicate, target)

    assert (await publication_of(session, duplicate)).state == "withdrawn"
    assert (await publication_of(session, target)).state == "published"
    published = await session.scalars(
        select(Publication.event_id).where(Publication.state == "published")
    )
    assert list(published) == [target.id]
    # 之後的同步也不會讓被併入的事件重新公開
    await publish(session, duplicate)
    assert (await publication_of(session, duplicate)).state == "withdrawn"


async def test_s04_no_longer_links_articles_to_merged_event(session: AsyncSession) -> None:
    target, duplicate = await two_events(session)
    on_date = target.occurrence_start_date
    assert on_date is not None
    before = await find_candidate_events(session, VECTOR, on_date=on_date, category="facility")
    assert {c.event_id for c in before} == {target.id, duplicate.id}
    (moved_item,) = await items_of(session, duplicate)

    await merge(session, duplicate, target)

    after = await find_candidate_events(session, VECTOR, on_date=on_date, category="facility")
    assert [c.event_id for c in after] == [target.id]
    # 被移走的文章重跑管線時歸到目標事件，不會建立或回到被併入的事件
    official_id = next(iter(await items_of(session, target) - {moved_item})).hex
    provider = SkillScript(
        {
            "evidence-verifier": [
                verification(
                    {
                        official_id: ("supports", "official"),
                        moved_item.hex: ("supports", "first_hand"),
                    },
                    "verified",
                )
            ],
            "risk-assessor": [risk(official_id)],
        }
    )
    result = await analyze_item(session, moved_item, deps(provider))
    assert result.outcome is Outcome.ANALYZED
    assert result.event_id == target.id
    assert await items_of(session, duplicate) == set()


def relation(
    from_id: uuid.UUID, to_id: uuid.UUID, *, decision: str = "pending", type: str = "related_event"
) -> EventRelation:
    decided = decision != "pending"
    return EventRelation(
        from_event_id=from_id,
        to_event_id=to_id,
        type=type,
        decision=decision,
        decided_by="admin:u-1" if decided else None,
        decided_at=PUBLISHED if decided else None,
    )


async def relation_pairs(session: AsyncSession) -> set[tuple[uuid.UUID, uuid.UUID, str, str]]:
    rows = await session.scalars(select(EventRelation))
    return {(r.from_event_id, r.to_event_id, r.type, r.decision) for r in rows}


async def test_merge_moves_relations_to_target(session: AsyncSession) -> None:
    target, duplicate = await two_events(session)
    a = await add_event(session, title="總圖二樓停電")
    b = await add_event(session, title="總圖空調故障")
    c = await add_event(session, title="總圖電梯停用")
    session.add_all(
        [
            relation(duplicate.id, a.id),  # 改指向目標
            relation(c.id, duplicate.id, type="hypothesized_cause"),  # 另一端也改
            relation(duplicate.id, target.id),  # 兩端合併後會連到自己：刪除
            relation(target.id, b.id),  # 目標已有同類型關聯（待審）
            relation(b.id, duplicate.id, decision="confirmed"),  # 已確認的優先保留
        ]
    )
    await session.commit()

    await merge(session, duplicate, target)

    assert await relation_pairs(session) == {
        (target.id, a.id, "related_event", "pending"),
        (c.id, target.id, "hypothesized_cause", "pending"),
        (b.id, target.id, "related_event", "confirmed"),
    }
    audit = (await session.scalars(select(AuditLog).where(AuditLog.action == "event.merge"))).one()
    assert audit.meta["moved_relations"] == 3


async def test_merge_keeps_existing_target_relation_when_both_pending(
    session: AsyncSession,
) -> None:
    target, duplicate = await two_events(session)
    a = await add_event(session, title="總圖二樓停電")
    session.add_all([relation(a.id, target.id), relation(duplicate.id, a.id)])
    await session.commit()

    await merge(session, duplicate, target)

    assert await relation_pairs(session) == {(a.id, target.id, "related_event", "pending")}


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (EventStatus.ANALYZING, MergeConflictError),  # 分析中
        (EventStatus.ARCHIVED, MergeConflictError),  # 已封存（含已被併入）
    ],
)
async def test_merge_rejects_source_in_wrong_state(
    session: AsyncSession, status: EventStatus, error: type[Exception]
) -> None:
    target, duplicate = await two_events(session)
    duplicate.status = status.value
    await session.commit()

    with pytest.raises(error):
        await merge_events(session, ADMIN, duplicate.id, target.id, request_id="r")


async def test_merge_rejects_archived_or_merged_target(session: AsyncSession) -> None:
    target, duplicate = await two_events(session)
    third = await add_event(session, title="總圖三樓第三篇")
    await merge(session, target, third)  # target 已被併入 third

    with pytest.raises(MergeConflictError):
        await merge_events(session, ADMIN, duplicate.id, target.id, request_id="r")


async def test_merge_rejects_same_or_missing_event(session: AsyncSession) -> None:
    target, _ = await two_events(session)

    with pytest.raises(MergeInvalidError):
        await merge_events(session, ADMIN, target.id, target.id, request_id="r")
    with pytest.raises(MergeNotFoundError):
        await merge_events(session, ADMIN, uuid.uuid4(), target.id, request_id="r")
    with pytest.raises(MergeNotFoundError):
        await merge_events(session, ADMIN, target.id, uuid.uuid4(), request_id="r")


# ---------- 拆分 ----------


async def split_seed(session: AsyncSession) -> tuple[Event, uuid.UUID, uuid.UUID]:
    """一個事件誤把兩件事併在一起：官方公告（總圖漏水）與社群貼文（工程一館積水）。"""
    event = await add_event(session, evidence=(OFFICIAL, OTHER))
    event.embedding = VECTOR
    rows = await session.execute(
        select(EventEvidence.source_item_id, EventEvidence.excerpt).where(
            EventEvidence.event_id == event.id
        )
    )
    by_text = {excerpt: item_id for item_id, excerpt in rows}
    official, other = by_text[OFFICIAL.text], by_text[OTHER.text]
    await add_extractor_run(session, event, official, "總圖書館三樓天花板漏水。")
    await add_extractor_run(session, event, other, "工程一館地下室積水。")
    event.summary = "工程一館地下室積水。"
    event.summary_source_item_id = other
    await session.commit()
    return event, official, other


async def test_split_moves_items_to_new_event(session: AsyncSession) -> None:
    event, official, other = await split_seed(session)

    new = await split_event(
        session, ADMIN, event.id, [other], title="工程一館地下室積水", request_id="req-split"
    )
    await session.commit()

    assert new.id != event.id
    assert await items_of(session, event) == {official}
    assert await items_of(session, new) == {other}
    assert (new.title, new.type, new.location_id, new.occurrence_start_date) == (
        "工程一館地下室積水",
        event.type,
        event.location_id,
        event.occurrence_start_date,
    )
    assert new.status == EventStatus.ANALYZING.value
    # 兩邊的事件說明依各自的文章重新挑選
    await session.refresh(event)
    assert (event.summary, event.summary_source_item_id) == ("總圖書館三樓天花板漏水。", official)
    assert (new.summary, new.summary_source_item_id) == ("工程一館地下室積水。", other)
    s02 = await session.scalars(
        select(AIAnalysisRun.event_id).where(AIAnalysisRun.evidence_ids.contains([other.hex]))
    )
    assert list(s02) == [new.id]
    # 措施與小報告留在原事件
    assert await session.scalar(select(EventMerge)) is None

    audit = (await session.scalars(select(AuditLog).where(AuditLog.action == "event.split"))).one()
    assert audit.target == f"event:{event.id}"
    assert audit.request_id == "req-split"
    assert audit.meta["new_event_id"] == str(new.id)
    assert audit.meta["moved_evidence"] == 1
    assert "工程一館" not in str(audit.meta)  # 不記標題文字


async def test_split_then_both_events_are_reanalyzed_separately(session: AsyncSession) -> None:
    event, official, other = await split_seed(session)
    new = await split_event(
        session, ADMIN, event.id, [other], title="工程一館地下室積水", request_id="r"
    )
    await session.commit()
    provider = SkillScript(
        {
            "evidence-verifier": [
                verification({official.hex: ("supports", "official")}, "verified"),
                verification({other.hex: ("supports", "first_hand")}, "unverified"),
            ],
            "risk-assessor": [risk(official.hex), risk(other.hex)],
        }
    )

    first = await analyze_event(session, event.id, deps(provider))
    second = await analyze_event(session, new.id, deps(provider))

    assert (first.outcome, second.outcome) == (Outcome.ANALYZED, Outcome.ANALYZED)
    verifier_runs = (
        await session.scalars(
            select(AIAnalysisRun)
            .where(AIAnalysisRun.skill_name == "evidence-verifier")
            .order_by(AIAnalysisRun.created_at)
        )
    ).all()
    assert [(r.event_id, r.evidence_ids) for r in verifier_runs] == [
        (event.id, [official.hex]),
        (new.id, [other.hex]),
    ]
    await session.refresh(new)
    assert new.status == EventStatus.UNVERIFIED.value


async def test_split_recomputes_both_embeddings_on_reanalysis(session: AsyncSession) -> None:
    event, official, other = await split_seed(session)
    new = await split_event(
        session, ADMIN, event.id, [other], title="工程一館地下室積水", request_id="r"
    )
    await session.commit()
    # 拆分後兩邊的向量都清空（原本的向量可能來自被拆走的文章），候選召回暫時找不到它們
    await session.refresh(event)
    assert (event.embedding, new.embedding) == (None, None)

    official_vector = [1.0] + [0.0] * (EVENT_EMBEDDING_DIM - 1)
    other_vector = [0.0, 1.0] + [0.0] * (EVENT_EMBEDDING_DIM - 2)
    embedder = FakeEmbeddingProvider(
        {
            embedding_text(None, OFFICIAL.text): official_vector,
            embedding_text(None, OTHER.text): other_vector,
        }
    )
    provider = SkillScript(
        {
            "evidence-verifier": [
                verification({official.hex: ("supports", "official")}, "verified"),
                verification({other.hex: ("supports", "first_hand")}, "unverified"),
            ],
            "risk-assessor": [risk(official.hex), risk(other.hex)],
        }
    )
    pipeline_deps: PipelineDeps = deps(provider, embedder)

    first = await analyze_event(session, event.id, pipeline_deps)
    second = await analyze_event(session, new.id, pipeline_deps)

    assert ("embedding", "succeeded") in [(s.name, s.status) for s in first.steps]
    assert ("embedding", "succeeded") in [(s.name, s.status) for s in second.steps]
    await session.refresh(event)
    await session.refresh(new)
    assert list(event.embedding) == official_vector
    assert list(new.embedding) == other_vector
    on_date = event.occurrence_start_date
    assert on_date is not None
    nearest = await find_candidate_events(session, other_vector, on_date=on_date, limit=1)
    assert [c.event_id for c in nearest] == [new.id]
    # 已經有向量的事件重跑分析時不重算
    again = await analyze_event(
        session,
        new.id,
        deps(
            SkillScript(
                {
                    "evidence-verifier": [
                        verification({other.hex: ("supports", "first_hand")}, "unverified")
                    ],
                    "risk-assessor": [risk(other.hex)],
                }
            ),
            embedder,
        ),
    )
    assert "embedding" not in [s.name for s in again.steps]


@pytest.mark.parametrize("which", ["all", "none", "unknown"])
async def test_split_rejects_invalid_items(session: AsyncSession, which: str) -> None:
    event, official, other = await split_seed(session)
    items = {"all": [official, other], "none": [], "unknown": [uuid.uuid4()]}[which]

    with pytest.raises(MergeInvalidError):
        await split_event(session, ADMIN, event.id, items, title="另一件事", request_id="r")


async def test_split_rejects_title_with_pii(session: AsyncSession) -> None:
    event, _, other = await split_seed(session)

    with pytest.raises(MergeInvalidError):
        await split_event(
            session, ADMIN, event.id, [other], title="聯絡 0912-345-678", request_id="r"
        )


async def test_split_rejects_archived_event(session: AsyncSession) -> None:
    event, _, other = await split_seed(session)
    event.status = EventStatus.ARCHIVED.value
    await session.commit()

    with pytest.raises(MergeConflictError):
        await split_event(session, ADMIN, event.id, [other], title="另一件事", request_id="r")


# ---------- API ----------


class Api:
    def __init__(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        self.app = app
        self.client = client
        self.queued: list[tuple[uuid.UUID, str]] = []
        app.dependency_overrides[get_analysis_queue] = lambda: self._enqueue

    def _enqueue(self, event_id: uuid.UUID, request_id: str) -> None:
        self.queued.append((event_id, request_id))

    def as_principal(self, principal: Principal) -> None:
        self.app.dependency_overrides[get_principal] = lambda: principal

    async def post(self, path: str, body: dict[str, Any]) -> httpx2.Response:
        return await self.client.post(f"/api/v1/admin/events/{path}", json=body)


@pytest.fixture()
async def api(session: AsyncSession) -> AsyncIterator[Api]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        api = Api(app, client)
        api.as_principal(ADMIN)
        yield api


async def test_merge_endpoint_merges_and_queues_target(api: Api, session: AsyncSession) -> None:
    target, duplicate = await two_events(session)

    response = await api.post(f"{duplicate.id}/merge", {"target_event_id": str(target.id)})

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {"event_id": str(duplicate.id), "merged_into": str(target.id)}
    assert api.queued == [(target.id, body["meta"]["request_id"])]
    detail = (await api.client.get(f"/api/v1/admin/events/{duplicate.id}")).json()["data"]
    assert detail["status"] == "archived"
    assert detail["merged_into"] == str(target.id)
    assert detail["evidence"] == []
    target_detail = (await api.client.get(f"/api/v1/admin/events/{target.id}")).json()["data"]
    assert target_detail["merged_into"] is None
    assert len(target_detail["evidence"]) == 2


async def test_merge_endpoint_errors(api: Api, session: AsyncSession) -> None:
    target, duplicate = await two_events(session)

    same = await api.post(f"{target.id}/merge", {"target_event_id": str(target.id)})
    assert (same.status_code, same.json()["error"]["code"]) == (422, "VALIDATION_ERROR")
    missing = await api.post(f"{uuid.uuid4()}/merge", {"target_event_id": str(target.id)})
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    await api.post(f"{duplicate.id}/merge", {"target_event_id": str(target.id)})
    again = await api.post(f"{duplicate.id}/merge", {"target_event_id": str(target.id)})
    assert again.json()["error"]["code"] == "CONFLICT"
    assert len(api.queued) == 1


async def test_split_endpoint_creates_event_and_queues_both(
    api: Api, session: AsyncSession
) -> None:
    event, _, other = await split_seed(session)

    response = await api.post(
        f"{event.id}/split", {"source_item_ids": [str(other)], "title": "工程一館地下室積水"}
    )

    assert response.status_code == 201
    body = response.json()
    new_id = uuid.UUID(body["data"]["new_event_id"])
    assert body["data"]["event_id"] == str(event.id)
    request_id = body["meta"]["request_id"]
    assert api.queued == [(event.id, request_id), (new_id, request_id)]
    new = await session.get(Event, new_id)
    assert new is not None
    assert await items_of(session, new) == {other}


async def test_split_endpoint_validation(api: Api, session: AsyncSession) -> None:
    event, official, other = await split_seed(session)

    everything = await api.post(
        f"{event.id}/split",
        {"source_item_ids": [str(official), str(other)], "title": "另一件事"},
    )
    assert everything.json()["error"]["code"] == "VALIDATION_ERROR"
    blank = await api.post(f"{event.id}/split", {"source_item_ids": [str(other)], "title": " "})
    assert blank.json()["error"]["code"] == "VALIDATION_ERROR"
    assert api.queued == []


@pytest.mark.parametrize("path", ["merge", "split"])
async def test_merge_split_require_verify_permission(
    api: Api, session: AsyncSession, path: str
) -> None:
    event, _, other = await split_seed(session)
    api.as_principal(Principal(kind="service", subject="ai", roles=frozenset({Role.SERVICE_AI})))
    body = (
        {"target_event_id": str(uuid.uuid4())}
        if path == "merge"
        else {"source_item_ids": [str(other)], "title": "x"}
    )

    response = await api.post(f"{event.id}/{path}", body)

    assert response.status_code == 403
