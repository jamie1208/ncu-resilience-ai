"""P4-T09：事件小報告（S06 之後接上 S07、S08）。

- S06 之後由 `after_assessment` 決定是否排入產生報告：風險中以上自動產生；風險低但已有報告時也重算；
  事件被查證為不實時不產生，既有報告標為失效。
- `generate_report`：S07 原因、S08 措施，加上 S06 分項成為「可能的影響」；每一項都要有存在的
  evidence_ids；推測原因（hypothesized）永遠帶推測標示，只有官方來源逐字原句支持的原因才算確認。
- S07／S08 abstain 時該區塊沒有任何項目（不產生空白或捏造內容），API 顯示「證據不足，暫不分析」。
- `GET /api/v1/admin/events/{id}` 回傳最新一份報告；
  `POST /api/v1/admin/events/{id}/report` 手動觸發。
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.api.v1.admin.events import get_report_queue
from app.domain.states import EventStatus
from app.main import create_app
from app.models import AuditLog, Event, EventEvidence
from app.models.report import EventReport
from app.models.source import Source
from app.pipeline.analyze_item import PipelineDeps, analyze_event
from app.reports.service import (
    ABSTAIN_MESSAGE,
    HYPOTHESIZED_LABEL,
    REPORT_EVENT_TASK,
    generate_report,
    make_report_hook,
)
from app.worker.celery_app import celery_app
from tests.integration.test_pipeline import (
    ADMIN,
    SkillScript,
    add_item,
    add_source,
    deps,
    risk,
    verification,
)

pytestmark = pytest.mark.anyio

OFFICIAL = "工程一館停電公告\n\n停電原因為變電箱老舊跳脫，總務處已安排更換。"
RUMOR = "工程一館又停電了\n\n聽說是旁邊施工挖到電纜，電梯也停了。"
OFFICIAL_QUOTE = "停電原因為變電箱老舊跳脫"
RUMOR_QUOTE = "聽說是旁邊施工挖到電纜"


# ---------- 假的 S07／S08 輸出 ----------


def cause(
    cause_id: str, basis: str, evidence_id: str, quote: str, description: str
) -> dict[str, Any]:
    return {
        "cause_id": cause_id,
        "description": description,
        "basis": basis,
        "evidence_ids": [evidence_id],
        "evidence_spans": [{"evidence_id": evidence_id, "quote": quote}],
        "to_verify": None if basis == "confirmed" else "向總務處確認施工是否影響供電。",
    }


def root_causes(*causes: dict[str, Any]) -> dict[str, Any]:
    cited = sorted({e for c in causes for e in c["evidence_ids"]})
    return {
        "status": "ok",
        "causes": list(causes),
        "missing_information": ["變電箱更換時程"],
        "evidence_ids": cited,
        "reason": "整理證據中提到的原因。",
        "abstain_reason": None,
    }


def root_cause_abstain(reason: str = "no_cause_evidence") -> dict[str, Any]:
    return {
        "status": "abstain",
        "causes": [],
        "missing_information": ["停電原因"],
        "evidence_ids": [],
        "reason": "證據沒有提到原因。",
        "abstain_reason": reason,
    }


def measure(
    action: str,
    evidence_id: str,
    *,
    audience: str = "school",
    horizon: str = "short",
    kind: str = "respond",
    cause_id: str | None = None,
    cause_basis: str | None = None,
    unit: str | None = "總務處",
) -> dict[str, Any]:
    return {
        "action": action,
        "audience": audience,
        "horizon": horizon,
        "priority": "high",
        "kind": kind,
        "cause_id": cause_id,
        "cause_basis": cause_basis,
        "responsible_unit": unit if audience == "school" else None,
        "evidence_ids": [evidence_id],
    }


def plan(*measures: dict[str, Any]) -> dict[str, Any]:
    cited = sorted({e for m in measures for e in m["evidence_ids"]})
    return {
        "status": "ok",
        "measures": list(measures),
        "evidence_ids": cited,
        "reason": "先應變，再查證待確認的原因。",
        "abstain_reason": None,
    }


def plan_abstain(reason: str = "insufficient_evidence") -> dict[str, Any]:
    return {
        "status": "abstain",
        "measures": [],
        "evidence_ids": [],
        "reason": "看不出發生什麼事。",
        "abstain_reason": reason,
    }


# ---------- 資料 ----------


@dataclasses.dataclass
class Scene:
    event: Event
    official: str
    rumor: str


async def source(session: AsyncSession, code: str) -> Source:
    """同一個測試可能建立多個事件，來源代碼不可重複。"""
    existing = await session.scalar(select(Source).where(Source.code == code))
    return existing or await add_source(session, code)


async def scene(session: AsyncSession, *, status: EventStatus = EventStatus.UNVERIFIED) -> Scene:
    """一個事件：官方公告（說明原因）與社群貼文（推測原因）各一份證據。"""
    official = await add_item(session, await source(session, "ncu-announcements"), OFFICIAL)
    rumor = await add_item(session, await source(session, "facebook"), RUMOR)
    event = Event(title="工程一館停電", type="facility", status=status.value)
    session.add(event)
    await session.flush()
    for item in (official, rumor):
        session.add(
            EventEvidence(event_id=event.id, source_item_id=item.id, support_type="mentions")
        )
    await session.commit()
    return Scene(event, official.id.hex, rumor.id.hex)


class Enqueued:
    """記錄被排入的報告任務（取代 Celery）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str]] = []

    def __call__(self, event_id: uuid.UUID, trace_id: str) -> None:
        self.calls.append((event_id, trace_id))


def with_hook(provider: SkillScript, enqueue: Enqueued) -> PipelineDeps:
    return dataclasses.replace(deps(provider), report_hook=make_report_hook(enqueue))


async def assess(
    session: AsyncSession, s: Scene, *, safety: int, suggested: str = "unverified"
) -> Enqueued:
    """重跑事件的 S05、S06（風險由 safety 決定），回傳被排入的報告任務。"""
    stances = {s.official: ("supports", "official"), s.rumor: ("supports", "first_hand")}
    if suggested == "false":
        stances = {s.official: ("refutes", "official"), s.rumor: ("supports", "hearsay")}
    provider = SkillScript(
        {
            "evidence-verifier": [verification(stances, suggested)],
            "risk-assessor": [risk(s.official, safety=safety)],
        }
    )
    enqueue = Enqueued()
    await analyze_event(session, s.event.id, with_hook(provider, enqueue))
    return enqueue


def full_report_script(s: Scene) -> SkillScript:
    return SkillScript(
        {
            "root-cause-analyst": [
                root_causes(
                    cause("c1", "confirmed", s.official, OFFICIAL_QUOTE, "變電箱老舊跳脫。"),
                    cause("c2", "hypothesized", s.rumor, RUMOR_QUOTE, "疑似附近施工挖到電纜。"),
                )
            ],
            "solution-planner": [
                plan(
                    measure("停電期間改到其他大樓自習。", s.official, audience="students"),
                    measure(
                        "盤點同年份的變電箱並排定汰換。",
                        s.official,
                        horizon="mid",
                        kind="prevent",
                        cause_id="c1",
                        cause_basis="confirmed",
                    ),
                    measure(
                        "確認附近施工是否曾影響供電線路。",
                        s.rumor,
                        kind="investigate",
                        cause_id="c2",
                        cause_basis="hypothesized",
                    ),
                )
            ],
        }
    )


def strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)


# ---------- 何時產生 ----------


async def test_medium_or_higher_risk_queues_report(session: AsyncSession) -> None:
    s = await scene(session)

    enqueue = await assess(session, s, safety=85)  # personal_safety ≥ 80 → critical

    assert [event_id for event_id, _ in enqueue.calls] == [s.event.id]


async def test_low_risk_without_report_does_not_queue(session: AsyncSession) -> None:
    s = await scene(session)

    enqueue = await assess(session, s, safety=10)  # 預設分項加權為 low

    assert enqueue.calls == []


async def test_low_risk_with_existing_report_recalculates(session: AsyncSession) -> None:
    s = await scene(session)
    await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="manual")

    enqueue = await assess(session, s, safety=10)

    assert [event_id for event_id, _ in enqueue.calls] == [s.event.id]


# ---------- 內容：每項可回溯證據、推測不呈現為事實 ----------


async def test_every_item_cites_existing_evidence(session: AsyncSession) -> None:
    s = await scene(session)
    await assess(session, s, safety=85)

    report = await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="auto")

    assert report is not None
    known = {s.official, s.rumor}
    items = [*report.impacts, *report.causes, *report.measures]
    assert len(report.impacts) == 5
    assert len(report.causes) == 2
    assert len(report.measures) == 3
    for item in items:
        assert item["evidence_ids"], item
        assert set(item["evidence_ids"]) <= known
    assert report.root_cause_run_id is not None
    assert report.solution_run_id is not None
    assert report.risk_assessment_id is not None
    assert report.invalidated_at is None


async def test_hypothesized_cause_is_always_labelled(session: AsyncSession) -> None:
    s = await scene(session)
    report = await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="auto")
    assert report is not None

    confirmed, hypothesized = report.causes
    assert confirmed["basis"] == "confirmed"
    assert confirmed["quotes"] == [{"evidence_id": s.official, "quote": OFFICIAL_QUOTE}]
    assert hypothesized["basis"] == "hypothesized"
    assert hypothesized["label"] == HYPOTHESIZED_LABEL
    assert hypothesized["to_verify"]
    planned = {m["cause_id"]: m["cause_basis"] for m in report.measures if m["cause_id"]}
    assert planned == {"c1": "confirmed", "c2": "hypothesized"}


async def test_confirmed_cause_without_official_quote_is_downgraded(
    session: AsyncSession,
) -> None:
    """模型把社群說法標為 confirmed：規則改判為推測，措施沿用改判後的性質。"""
    s = await scene(session)
    provider = SkillScript(
        {
            "root-cause-analyst": [
                root_causes(cause("c1", "confirmed", s.rumor, RUMOR_QUOTE, "附近施工挖到電纜。"))
            ],
            "solution-planner": [
                plan(
                    measure(
                        "要求施工廠商賠償。",
                        s.rumor,
                        kind="prevent",
                        cause_id="c1",
                        cause_basis="confirmed",
                    )
                )
            ],
        }
    )

    report = await generate_report(session, s.event.id, deps(provider), trigger="manual")

    assert report is not None
    (only,) = report.causes
    assert only["basis"] == "hypothesized"
    assert only["label"] == HYPOTHESIZED_LABEL
    assert only["to_verify"]
    (planned,) = report.measures
    assert planned["cause_basis"] == "hypothesized"


async def test_abstain_produces_no_items(session: AsyncSession) -> None:
    s = await scene(session)
    provider = SkillScript(
        {
            "root-cause-analyst": [root_cause_abstain()],
            "solution-planner": [plan_abstain()],
        }
    )

    report = await generate_report(session, s.event.id, deps(provider), trigger="manual")

    assert report is not None
    assert report.causes == []
    assert report.measures == []
    assert report.causes_status == "abstained"
    assert report.measures_status == "abstained"
    assert report.impacts == []  # 沒有風險評估時不呈現影響
    assert all(text.strip() for text in strings(report.missing_information))


async def test_root_cause_abstain_still_plans_general_measures(session: AsyncSession) -> None:
    s = await scene(session)
    provider = SkillScript(
        {
            "root-cause-analyst": [root_cause_abstain()],
            "solution-planner": [
                plan(measure("公告停電期間的替代自習空間。", s.official, cause_id=None))
            ],
        }
    )

    report = await generate_report(session, s.event.id, deps(provider), trigger="manual")

    assert report is not None
    assert report.causes_status == "abstained"
    assert report.measures_status == "ok"
    assert [m["action"] for m in report.measures] == ["公告停電期間的替代自習空間。"]


async def test_regenerating_keeps_history(session: AsyncSession) -> None:
    s = await scene(session)
    await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="auto")
    await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="manual")

    rows = list(
        await session.scalars(select(EventReport).where(EventReport.event_id == s.event.id))
    )

    assert len(rows) == 2


# ---------- 查證為不實 ----------


async def test_refuted_event_does_not_run_s07_or_s08(session: AsyncSession) -> None:
    s = await scene(session, status=EventStatus.FALSE)
    provider = SkillScript({})

    report = await generate_report(session, s.event.id, deps(provider), trigger="manual")

    assert report is None
    assert provider.called == []


async def test_pipeline_refutation_invalidates_report(session: AsyncSession) -> None:
    s = await scene(session)
    report = await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="auto")
    assert report is not None

    enqueue = await assess(session, s, safety=85, suggested="false")

    await session.refresh(report)
    assert report.invalidated_at is not None
    assert report.invalidated_reason == "event_false"
    assert enqueue.calls == []


# ---------- API ----------


class Api:
    def __init__(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        self.client = client
        self.queued = Enqueued()
        app.dependency_overrides[get_report_queue] = lambda: self.queued


@pytest.fixture()
async def api(session: AsyncSession) -> AsyncIterator[Api]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    app.dependency_overrides[get_principal] = lambda: ADMIN
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield Api(app, c)


async def test_event_detail_returns_latest_report(api: Api, session: AsyncSession) -> None:
    s = await scene(session)
    await assess(session, s, safety=85)
    await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="auto")

    response = await api.client.get(f"/api/v1/admin/events/{s.event.id}")

    assert response.status_code == 200
    report = response.json()["data"]["report"]
    assert report["valid"] is True
    assert report["trigger"] == "auto"
    assert report["impacts"]["status"] == "ok"
    assert {i["dimension"] for i in report["impacts"]["items"]} == {
        "personal_safety",
        "operations",
        "property",
        "reputation",
        "scope",
    }
    causes = report["causes"]["items"]
    assert [c["label"] for c in causes] == ["已確認", HYPOTHESIZED_LABEL]
    assert report["measures"]["status"] == "ok"
    assert report["referrals"] == [{"unit": "總務處", "evidence_ids": [s.official, s.rumor]}]
    for section in ("impacts", "causes", "measures"):
        for item in report[section]["items"]:
            assert item["evidence_ids"]


async def test_event_detail_shows_abstain_message(api: Api, session: AsyncSession) -> None:
    s = await scene(session)
    provider = SkillScript(
        {"root-cause-analyst": [root_cause_abstain()], "solution-planner": [plan_abstain()]}
    )
    await generate_report(session, s.event.id, deps(provider), trigger="manual")

    report = (await api.client.get(f"/api/v1/admin/events/{s.event.id}")).json()["data"]["report"]

    assert report["causes"] == {
        "status": "abstained",
        "message": ABSTAIN_MESSAGE,
        "items": [],
    }
    assert report["measures"] == {
        "status": "abstained",
        "message": ABSTAIN_MESSAGE,
        "items": [],
    }
    assert report["referrals"] == []


async def test_event_detail_without_report_is_null(api: Api, session: AsyncSession) -> None:
    s = await scene(session)

    response = await api.client.get(f"/api/v1/admin/events/{s.event.id}")

    assert response.json()["data"]["report"] is None


async def test_verify_false_invalidates_report(api: Api, session: AsyncSession) -> None:
    s = await scene(session)
    report = await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="auto")
    assert report is not None

    response = await api.client.post(
        f"/api/v1/admin/events/{s.event.id}/verify", json={"status": "false"}
    )

    assert response.status_code == 200
    data = (await api.client.get(f"/api/v1/admin/events/{s.event.id}")).json()["data"]
    assert data["report"]["valid"] is False
    assert data["report"]["invalidated_reason"] == "event_false"


async def test_manual_trigger_queues_and_audits(api: Api, session: AsyncSession) -> None:
    s = await scene(session)

    response = await api.client.post(f"/api/v1/admin/events/{s.event.id}/report")

    assert response.status_code == 202
    assert response.json()["data"] == {"event_id": str(s.event.id), "status": "queued"}
    ((event_id, request_id),) = api.queued.calls
    assert event_id == s.event.id
    audit = await session.scalar(select(AuditLog).where(AuditLog.action == "event.report"))
    assert audit is not None
    assert audit.request_id == request_id


async def test_manual_trigger_rejects_refuted_or_archived(api: Api, session: AsyncSession) -> None:
    refuted = await scene(session, status=EventStatus.FALSE)
    archived = await scene(session, status=EventStatus.ARCHIVED)

    for s in (refuted, archived):
        response = await api.client.post(f"/api/v1/admin/events/{s.event.id}/report")
        assert response.status_code == 409
    assert api.queued.calls == []


async def test_manual_trigger_unknown_event(api: Api) -> None:
    response = await api.client.post(f"/api/v1/admin/events/{uuid.uuid4()}/report")

    assert response.status_code == 404


def test_report_task_is_registered() -> None:
    assert REPORT_EVENT_TASK in celery_app.tasks
