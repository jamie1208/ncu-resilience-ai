"""P2-T13：分析管線端到端（fake provider，不呼叫真實 API）。

- `analyze_item`：S01 → S02 → S03 → 候選召回 → S04 → 建立／併入事件 → S05（規則）→ S06（引擎）。
- `analyze_event`：重跑事件的 S05 → S06，風險評估新增一筆（保留歷史）。
- `POST /api/v1/admin/events/{id}/analyze`：權限、稽核、排入佇列。
"""

from __future__ import annotations

import json
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import embedding_text
from app.ai.llm.base import LLMAuthError, LLMError, LLMResult, Message, Usage
from app.ai.llm.config import load_llm_config
from app.ai.llm.fake_provider import FakeEmbeddingProvider
from app.ai.runner.loader import discover_skills
from app.api.deps import get_db_session, get_principal
from app.api.v1.admin.events import get_analysis_queue
from app.core.security import ANONYMOUS, Principal, Role
from app.domain.states import EventStatus
from app.main import create_app
from app.models import AuditLog, Event, EventEvidence, Location
from app.models.ai import AIAnalysisRun, EventRelation, RiskAssessment
from app.models.event import EVENT_EMBEDDING_DIM
from app.models.source import Source, SourceItem, SourceItemVersion
from app.pipeline.analyze_item import (
    ANALYZE_EVENT_TASK,
    ANALYZE_ITEM_TASK,
    Outcome,
    PipelineDeps,
    analyze_event,
    analyze_item,
)
from app.risk.engine import load_policy
from app.worker.celery_app import celery_app

pytestmark = pytest.mark.anyio

PUBLISHED = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
NOTICE = "科學一館停電公告\n\n科學一館將於 10/22 09:00–12:00 停電檢修，請師生提早存檔。"
REPOST = "有人知道科學一館今天是不是停電嗎？\n\n剛剛電梯停了，走廊燈也全暗。"
SKILLS = discover_skills()


# ---------- 假的模型：依 Skill 回傳腳本 ----------


Script = Mapping[str, Any] | LLMError | Callable[[], Mapping[str, Any]]


class SkillScript:
    """依 schema 名稱（每個 Skill 一個）分別排隊回應；沒有腳本的 Skill 被呼叫時測試失敗。"""

    name = "fake"

    def __init__(self, scripts: Mapping[str, Sequence[Script]]) -> None:
        self._queues = {
            skill.replace("-", "_") + "_output": deque(items) for skill, items in scripts.items()
        }
        self.called: list[str] = []

    async def generate_structured(
        self,
        messages: Sequence[Message],
        json_schema: Mapping[str, Any],
        *,
        schema_name: str,
        model: str,
        timeout: float,
        max_output_tokens: int,
    ) -> LLMResult:
        skill = schema_name.removesuffix("_output").replace("_", "-")
        self.called.append(skill)
        queue = self._queues.get(schema_name)
        if not queue:
            raise AssertionError(f"沒有預期 {skill} 會被呼叫")
        item = queue.popleft()
        if isinstance(item, LLMError):
            raise item
        output = item() if callable(item) else item
        return LLMResult(
            provider=self.name,
            model=model,
            text=json.dumps(output, ensure_ascii=False),
            usage=Usage(input_tokens=100, output_tokens=50),
            response_id=None,
            latency_ms=1,
        )


def relevance(eid: str, relevant: bool = True) -> dict[str, Any]:
    return {
        "status": "ok",
        "is_campus_relevant": relevant,
        "evidence_ids": [eid],
        "evidence_spans": [],
        "reason": "內容提到校內建築。",
        "confidence": 0.9,
        "abstain_reason": None,
    }


def extraction(eid: str, *, is_event: bool = True, location: str = "科學一館") -> dict[str, Any]:
    return {
        "status": "ok",
        "is_event": is_event,
        "title": "科學一館停電檢修" if is_event else None,
        "occurrence_start_date": "2026-10-22" if is_event else None,
        "occurrence_start_time": "09:00" if is_event else None,
        "occurrence_end_date": None,
        "occurrence_end_time": None,
        "location": location if is_event else None,
        "mentions_person": False,
        "summary": (
            {"text": "科學一館 10/22 上午停電檢修。", "evidence_ids": [eid]} if is_event else None
        ),
        "evidence_ids": [eid],
        "evidence_spans": [{"field": "location", "evidence_id": eid, "quote": "科學一館"}],
        "reason": "寫明停電的時間與地點。",
        "confidence": 0.9,
        "abstain_reason": None,
    }


def classification(eid: str, category: str = "facility") -> dict[str, Any]:
    return {
        "status": "ok",
        "category": category,
        "tags": ["停電"],
        "classification_confidence": 0.9,
        "evidence_ids": [eid],
        "evidence_spans": [],
        "reason": "停電屬於設施類。",
        "abstain_reason": None,
    }


def linking(article: str, candidate: str, relation: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "relation": relation,
        "confidence": 0.9,
        "evidence_ids": [article, candidate],
        "evidence_spans": [],
        "reason": "時間地點一致。",
        "abstain_reason": None,
    }


def verification(stances: Mapping[str, tuple[str, str]], suggested: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "assessments": [
            {"evidence_id": eid, "stance": stance, "claim_type": claim}
            for eid, (stance, claim) in stances.items()
        ],
        "suggested_status": suggested,
        "confidence": 0.8,
        "evidence_ids": list(stances),
        "evidence_spans": [],
        "reason": "依各份證據的立場判斷。",
        "abstain_reason": None,
    }


def risk(eid: str, safety: int = 10, operations: int = 60) -> dict[str, Any]:
    def dim(score: int) -> dict[str, Any]:
        return {"score": score, "uncertainty": 0.3, "reason": "依公告內容評分。"}

    return {
        "status": "ok",
        "dimensions": {
            "personal_safety": dim(safety),
            "operations": dim(operations),
            "property": dim(10),
            "reputation": dim(5),
            "scope": dim(40),
        },
        "evidence_ids": [eid],
        "evidence_spans": [],
        "reason": "停電影響上課與研究。",
        "abstain_reason": None,
    }


def abstain(skill: str) -> dict[str, Any]:
    """依各 Skill 的 schema 組出 abstain 輸出。"""
    base: dict[str, Any] = {
        "status": "abstain",
        "evidence_ids": [],
        "evidence_spans": [],
        "reason": "內容不足。",
        "abstain_reason": "insufficient_evidence",
    }
    if skill == "evidence-verifier":
        return {**base, "assessments": [], "suggested_status": None, "confidence": 0}
    if skill == "risk-assessor":
        return {**base, "dimensions": None}
    raise ValueError(skill)


# ---------- 資料與相依 ----------


async def add_source(session: AsyncSession, code: str) -> Source:
    source = Source(code=code, name=code, type="rpage", access_policy="approved")
    session.add(source)
    await session.flush()
    return source


async def add_item(
    session: AsyncSession, source: Source, text: str, *, item_id: uuid.UUID | None = None
) -> SourceItem:
    item = SourceItem(
        id=item_id or uuid.uuid4(),
        source_id=source.id,
        external_id=uuid.uuid4().hex,
        url=f"https://example.test/{uuid.uuid4().hex}",
        published_at=PUBLISHED,
    )
    session.add(item)
    await session.flush()
    session.add(
        SourceItemVersion(item_id=item.id, content_hash=uuid.uuid4().hex, normalized_text=text)
    )
    await session.flush()
    return item


def eid(item: SourceItem) -> str:
    return item.id.hex


def deps(provider: SkillScript, embedder: FakeEmbeddingProvider | None = None) -> PipelineDeps:
    return PipelineDeps(
        provider=provider,
        embedder=embedder or FakeEmbeddingProvider(),
        config=load_llm_config(),
        skills=SKILLS,
        policy=load_policy(),
    )


async def count(session: AsyncSession, model: type[Any]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def runs(session: AsyncSession) -> list[AIAnalysisRun]:
    rows = await session.scalars(select(AIAnalysisRun).order_by(AIAnalysisRun.created_at))
    return list(rows)


# ---------- analyze_item ----------


async def test_new_official_item_creates_verified_event_with_risk(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-announcements")
    item = await add_item(session, source, NOTICE)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extraction(e)],
            "event-classifier": [classification(e)],
            "evidence-verifier": [verification({e: ("supports", "official")}, "verified")],
            "risk-assessor": [risk(e)],
        }
    )

    result = await analyze_item(session, item.id, deps(provider), trace_id="trace-1")

    assert result.outcome is Outcome.CREATED
    assert provider.called == [
        "relevance-filter",
        "event-extractor",
        "event-classifier",
        "evidence-verifier",
        "risk-assessor",
    ]
    event = await session.get(Event, result.event_id)
    assert event is not None
    assert event.title == "科學一館停電檢修"
    assert event.type == "facility"
    assert event.status == EventStatus.VERIFIED.value
    assert event.occurrence_start_date == date(2026, 10, 22)
    assert event.embedding is not None
    location = await session.get(Location, event.location_id)
    assert location is not None and location.name == "科學一館"

    evidence = (await session.scalars(select(EventEvidence))).all()
    assert [(x.source_item_id, x.support_type) for x in evidence] == [(item.id, "supports")]

    recorded = await runs(session)
    assert [r.skill_name for r in recorded] == provider.called
    assert all(r.event_id == event.id for r in recorded)  # S01–S03 事後補上 event_id
    assert {r.trace_id for r in recorded} == {"trace-1"}

    assessment = (await session.scalars(select(RiskAssessment))).one()
    assert assessment.event_id == event.id
    assert assessment.ai_run_id == recorded[-1].id
    assert assessment.policy_version == load_policy().policy_version

    audit = (await session.scalars(select(AuditLog))).one()
    assert audit.action == "pipeline.analyze_item"
    assert audit.actor == "service:ai-pipeline"
    assert audit.request_id == "trace-1"
    assert audit.meta["outcome"] == "created"


async def test_irrelevant_item_stops_after_s01(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    provider = SkillScript({"relevance-filter": [relevance(eid(item), relevant=False)]})

    result = await analyze_item(session, item.id, deps(provider))

    assert result.outcome is Outcome.IRRELEVANT
    assert result.event_id is None
    assert provider.called == ["relevance-filter"]
    assert await count(session, Event) == 0
    assert [r.skill_name for r in await runs(session)] == ["relevance-filter"]


async def test_non_event_stops_after_s02(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    provider = SkillScript(
        {"relevance-filter": [relevance(e)], "event-extractor": [extraction(e, is_event=False)]}
    )

    result = await analyze_item(session, item.id, deps(provider))

    assert result.outcome is Outcome.NOT_EVENT
    assert await count(session, Event) == 0


async def test_item_id_with_digit_runs_is_not_mistaken_for_pii(session: AsyncSession) -> None:
    """evidence_id 是文章 id 的十六進位，可能出現像學號、手機號碼的連續數字。"""
    item_id = uuid.UUID("0912345678ab4cdef0123456789abcde")
    item = await add_item(session, await add_source(session, "facebook"), REPOST, item_id=item_id)
    e = eid(item)
    provider = SkillScript(
        {"relevance-filter": [relevance(e)], "event-extractor": [extraction(e, is_event=False)]}
    )

    result = await analyze_item(session, item.id, deps(provider))

    assert result.outcome is Outcome.NOT_EVENT
    assert provider.called == ["relevance-filter", "event-extractor"]  # 沒有因個資誤判而重試


async def test_category_none_is_not_an_event(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extraction(e)],
            "event-classifier": [classification(e, "none")],
        }
    )

    result = await analyze_item(session, item.id, deps(provider))

    assert result.outcome is Outcome.NOT_EVENT
    assert await count(session, Event) == 0


async def test_model_failure_stops_and_keeps_run_record(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [LLMAuthError("金鑰錯誤", status_code=401)],
        }
    )

    result = await analyze_item(session, item.id, deps(provider))

    assert result.outcome is Outcome.STOPPED
    assert result.stopped_at == "event-extractor"
    assert await count(session, Event) == 0
    await session.rollback()  # 已提交的執行紀錄不會因為回滾而消失
    statuses = [(r.skill_name, r.status) for r in await runs(session)]
    assert statuses == [("relevance-filter", "succeeded"), ("event-extractor", "failed")]


VECTOR = [1.0] + [0.0] * (EVENT_EMBEDDING_DIM - 1)


def same_vector_embedder() -> FakeEmbeddingProvider:
    """轉貼文章的向量固定為 VECTOR，與 `_existing_event` 相同（確保被召回）。"""
    return FakeEmbeddingProvider({embedding_text(None, REPOST): VECTOR})


async def _existing_event(session: AsyncSession) -> Event:
    """已有一個官方公告建立的事件，向量為 VECTOR。"""
    official = await add_item(session, await add_source(session, "ncu-announcements"), NOTICE)
    vector = VECTOR
    event = Event(
        title="科學一館停電檢修",
        type="facility",
        status=EventStatus.UNVERIFIED.value,
        occurrence_start_date=date(2026, 10, 22),
        embedding=vector,
    )
    session.add(event)
    await session.flush()
    session.add(
        EventEvidence(event_id=event.id, source_item_id=official.id, support_type="mentions")
    )
    await session.flush()
    return event


async def test_same_event_is_merged_into_existing_event(session: AsyncSession) -> None:
    embedder = same_vector_embedder()
    existing = await _existing_event(session)
    official_id = (await session.scalars(select(EventEvidence.source_item_id))).one().hex
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extraction(e)],
            "event-classifier": [classification(e)],
            "event-linker": [linking(e, official_id, "same_event")],
            "evidence-verifier": [
                verification(
                    {official_id: ("supports", "official"), e: ("supports", "first_hand")},
                    "verified",
                )
            ],
            "risk-assessor": [risk(e)],
        }
    )

    result = await analyze_item(session, item.id, deps(provider, embedder))

    assert result.outcome is Outcome.LINKED
    assert result.event_id == existing.id
    assert await count(session, Event) == 1
    await session.refresh(existing)
    assert existing.status == EventStatus.VERIFIED.value
    support = dict(
        (
            await session.execute(select(EventEvidence.source_item_id, EventEvidence.support_type))
        ).all()
    )
    assert support == {uuid.UUID(official_id): "supports", item.id: "supports"}
    linker_run = next(r for r in await runs(session) if r.skill_name == "event-linker")
    assert linker_run.event_id == existing.id


async def test_related_event_creates_new_event_and_relation(session: AsyncSession) -> None:
    embedder = same_vector_embedder()
    existing = await _existing_event(session)
    official_id = (await session.scalars(select(EventEvidence.source_item_id))).one().hex
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extraction(e)],
            "event-classifier": [classification(e)],
            "event-linker": [linking(e, official_id, "related_event")],
            "evidence-verifier": [verification({e: ("supports", "hearsay")}, "unverified")],
            "risk-assessor": [risk(e)],
        }
    )

    result = await analyze_item(session, item.id, deps(provider, embedder))

    assert result.outcome is Outcome.CREATED
    assert result.event_id != existing.id
    new_event = await session.get(Event, result.event_id)
    assert new_event is not None and new_event.status == EventStatus.UNVERIFIED.value
    relation = (await session.scalars(select(EventRelation))).one()
    assert (relation.from_event_id, relation.to_event_id) == (new_event.id, existing.id)
    assert relation.type == "related_event"
    assert relation.score == pytest.approx(0.9)
    assert relation.ai_run_id is not None


async def test_reanalyzing_same_item_does_not_duplicate_event(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "ncu-announcements"), NOTICE)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extraction(e)],
            "event-classifier": [classification(e)],
            "evidence-verifier": [verification({e: ("supports", "official")}, "verified")] * 2,
            "risk-assessor": [risk(e), risk(e, operations=80)],
        }
    )
    first = await analyze_item(session, item.id, deps(provider))

    second = await analyze_item(session, item.id, deps(provider))

    assert second.outcome is Outcome.ANALYZED
    assert second.event_id == first.event_id
    assert await count(session, Event) == 1
    assert await count(session, EventEvidence) == 1
    assert await count(session, RiskAssessment) == 2  # 每次評分新增一筆，保留歷史


async def test_verifier_abstain_leaves_event_unverified(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extraction(e)],
            "event-classifier": [classification(e)],
            "evidence-verifier": [abstain("evidence-verifier")],
            "risk-assessor": [abstain("risk-assessor")],
        }
    )

    result = await analyze_item(session, item.id, deps(provider))

    assert result.outcome is Outcome.CREATED
    event = await session.get(Event, result.event_id)
    assert event is not None and event.status == EventStatus.UNVERIFIED.value
    assert await count(session, RiskAssessment) == 0
    assert result.risk_level is None


async def test_missing_item_raises(session: AsyncSession) -> None:
    with pytest.raises(LookupError):
        await analyze_item(session, uuid.uuid4(), deps(SkillScript({})))


# ---------- analyze_event ----------


async def test_analyze_event_reruns_verification_and_risk(session: AsyncSession) -> None:
    event = await _existing_event(session)
    official_id = (await session.scalars(select(EventEvidence.source_item_id))).one().hex
    provider = SkillScript(
        {
            "evidence-verifier": [verification({official_id: ("refutes", "official")}, "false")],
            "risk-assessor": [risk(official_id, safety=85)],
        }
    )

    result = await analyze_event(session, event.id, deps(provider), trace_id="trace-2")

    assert result.outcome is Outcome.ANALYZED
    assert provider.called == ["evidence-verifier", "risk-assessor"]
    await session.refresh(event)
    assert event.status == EventStatus.FALSE.value
    assessment = (await session.scalars(select(RiskAssessment))).one()
    assert assessment.level == result.risk_level
    assert "critical_override" in assessment.dimensions["_rules"]
    audit = (await session.scalars(select(AuditLog))).one()
    assert (audit.action, audit.request_id) == ("pipeline.analyze_event", "trace-2")


async def test_analyze_event_skips_archived(session: AsyncSession) -> None:
    event = await _existing_event(session)
    event.status = EventStatus.ARCHIVED.value
    await session.flush()

    result = await analyze_event(session, event.id, deps(SkillScript({})))

    assert result.outcome is Outcome.SKIPPED


# ---------- API ----------

ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
NOTIFIER = Principal(kind="service", subject="notifier", roles=frozenset({Role.SERVICE_NOTIFIER}))


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

    async def analyze(self, event_id: uuid.UUID | str) -> httpx2.Response:
        return await self.client.post(f"/api/v1/admin/events/{event_id}/analyze")


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


async def test_analyze_endpoint_queues_and_audits(api: Api, session: AsyncSession) -> None:
    event = await _existing_event(session)

    response = await api.analyze(event.id)

    assert response.status_code == 202
    body = response.json()
    assert body["error"] is None
    assert body["data"] == {"event_id": str(event.id), "status": "queued"}
    request_id = body["meta"]["request_id"]
    assert api.queued == [(event.id, request_id)]
    audit = (await session.scalars(select(AuditLog))).one()
    assert (audit.action, audit.actor, audit.target, audit.request_id) == (
        "event.analyze",
        "admin:u-1",
        f"event:{event.id}",
        request_id,
    )


async def test_analyze_endpoint_unknown_event_is_not_found(api: Api) -> None:
    response = await api.analyze(uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert api.queued == []


async def test_analyze_endpoint_archived_event_conflicts(api: Api, session: AsyncSession) -> None:
    event = await _existing_event(session)
    event.status = EventStatus.ARCHIVED.value
    await session.flush()

    response = await api.analyze(event.id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert api.queued == []


@pytest.mark.parametrize(
    ("principal", "status", "code"),
    [(ANONYMOUS, 401, "UNAUTHORIZED"), (NOTIFIER, 403, "FORBIDDEN")],
)
async def test_analyze_endpoint_requires_permission(
    api: Api, principal: Principal, status: int, code: str
) -> None:
    api.as_principal(principal)

    response = await api.analyze(uuid.uuid4())

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert api.queued == []


def test_pipeline_tasks_are_registered() -> None:
    assert ANALYZE_ITEM_TASK in celery_app.tasks
    assert ANALYZE_EVENT_TASK in celery_app.tasks
