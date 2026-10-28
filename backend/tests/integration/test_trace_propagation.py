"""P5-T04：trace_id 貫穿 API → worker → AI run。

同一個 ID 出現在回應標頭、佇列、worker 日誌、稽核與 ai_analysis_runs。
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import JsonFormatter, request_id_var
from app.models import AuditLog
from app.models.ai import AIAnalysisRun
from app.pipeline.analyze_item import analyze_event
from app.worker import celery_app as app_module
from tests.integration.test_pipeline import (  # noqa: F401 — 共用 api fixture
    Api,
    SkillScript,
    _existing_event,
    api,
    deps,
    risk,
    verification,
)

pytestmark = pytest.mark.anyio

TRACE = "trace-p5t04-0001"


def worker_task(trace: str | None) -> Any:
    """模擬 worker 收到的任務：自訂標頭會出現在 task.request。"""
    request = SimpleNamespace(trace_id=trace)
    request.get = lambda key, default=None: getattr(request, key, default)
    return SimpleNamespace(name="pipeline.analyze_event", request=request)


async def scripted(session: AsyncSession) -> tuple[Any, SkillScript]:
    event = await _existing_event(session)
    from app.models import EventEvidence

    official = (await session.scalars(select(EventEvidence.source_item_id))).one().hex
    provider = SkillScript(
        {
            "evidence-verifier": [verification({official: ("supports", "official")}, "verified")],
            "risk-assessor": [risk(official)],
        }
    )
    return event, provider


async def test_api_request_id_reaches_worker_logs_audit_and_ai_runs(
    api: Api,  # noqa: F811 — fixture
    session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    event, provider = await scripted(session)

    response = await api.client.post(
        f"/api/v1/admin/events/{event.id}/analyze", headers={"X-Request-ID": TRACE}
    )
    assert response.status_code == 202
    assert response.headers["X-Request-ID"] == TRACE
    [(queued_event, queued_trace)] = api.queued
    assert (queued_event, queued_trace) == (event.id, TRACE)

    # worker：Celery 標頭與任務參數都帶著同一個 trace
    task = worker_task(TRACE)
    app_module.bind_trace(task_id="w-1", task=task)
    try:
        with caplog.at_level(logging.INFO):
            logging.getLogger("app.worker.test").info("analyze start")
            await analyze_event(session, queued_event, deps(provider), trace_id=queued_trace)
        log = json.loads(JsonFormatter().format(caplog.records[-1]))
    finally:
        app_module.release_trace(task_id="w-1", task=task, state="SUCCESS")

    assert log["request_id"] == TRACE
    ai_runs = list(await session.scalars(select(AIAnalysisRun)))
    assert ai_runs and {r.trace_id for r in ai_runs} == {TRACE}
    audits = list(await session.scalars(select(AuditLog)))
    assert {(a.action, a.request_id) for a in audits} == {
        ("event.analyze", TRACE),
        ("pipeline.analyze_event", TRACE),
    }


async def test_worker_context_trace_is_used_when_task_has_no_trace_argument(
    session: AsyncSession,
) -> None:
    event, provider = await scripted(session)

    task = worker_task("trace-from-header-01")
    app_module.bind_trace(task_id="w-2", task=task)
    try:
        result = await analyze_event(session, event.id, deps(provider))
    finally:
        app_module.release_trace(task_id="w-2", task=task, state="SUCCESS")

    assert result.trace_id == "trace-from-header-01"
    ai_runs = list(await session.scalars(select(AIAnalysisRun)))
    assert {r.trace_id for r in ai_runs} == {"trace-from-header-01"}
    assert request_id_var.get() is None
