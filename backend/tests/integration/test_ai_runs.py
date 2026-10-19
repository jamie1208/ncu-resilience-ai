"""P2-T04：ai_analysis_runs、risk_assessments、event_relations（migration 0005）與 SqlRunStore。"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import Usage
from app.ai.llm.config import load_llm_config
from app.ai.llm.fake_provider import FakeProvider
from app.ai.runner.loader import load_skill
from app.ai.runner.run import RunRecord, RunStatus, SqlRunStore, run_skill
from app.models import AIAnalysisRun, Event, EventRelation, RiskAssessment

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "skills" / "_template"
# 台北 2026-10-20 00:30 = UTC 10-19 16:30
NOW = datetime(2026, 10, 19, 16, 30, tzinfo=UTC)


BASE = RunRecord(
    skill_name="template",
    skill_version="0.1.0",
    schema_version="1",
    provider="fake",
    model_id="m",
    status=RunStatus.SUCCEEDED,
    result={"status": "ok"},
    abstain_reason=None,
    error_type=None,
    validation_issues=(),
    attempts=1,
    evidence_ids=("e1",),
    input_tokens=10,
    output_tokens=5,
    cached_input_tokens=0,
    cost_usd=0.0,
    latency_ms=12,
    trace_id="trace-1",
    event_id=None,
    created_at=NOW,
)


def record(created_at: datetime, cost: float, **kw: Any) -> RunRecord:
    return replace(BASE, created_at=created_at, cost_usd=cost, **kw)


async def add_event(session: AsyncSession, title: str = "停電") -> Event:
    ev = Event(title=title, type="facility", status="detected")
    session.add(ev)
    await session.flush()
    return ev


async def test_save_round_trips(session: AsyncSession) -> None:
    store = SqlRunStore(session)
    run_id = await store.save(
        record(
            NOW,
            0.00123,
            validation_issues=("json",),
            status=RunStatus.ABSTAINED,
            result=None,
            abstain_reason="invalid_output",
            attempts=3,
        )
    )
    row = (await session.execute(select(AIAnalysisRun))).scalar_one()
    assert row.id == run_id
    assert row.status == "abstained"
    assert row.result is None
    assert row.abstain_reason == "invalid_output"
    assert row.validation_issues == ["json"]
    assert row.evidence_ids == ["e1"]
    assert row.cost_usd == Decimal("0.001230")
    assert row.created_at == NOW


async def test_spent_today_uses_taipei_day(session: AsyncSession) -> None:
    store = SqlRunStore(session)
    await store.save(record(NOW - timedelta(minutes=31), 0.5))  # 台北 10-19 23:59
    await store.save(record(NOW - timedelta(minutes=29), 0.25))  # 台北 10-20 00:01
    await store.save(record(NOW, 0.125))
    assert await store.spent_today_usd(NOW) == pytest.approx(0.375)


async def test_run_skill_writes_row(session: AsyncSession) -> None:
    skill = load_skill(TEMPLATE)
    case = json.loads(
        (TEMPLATE / "tests" / "cases.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    output = json.loads(
        (TEMPLATE / "tests" / "recorded" / f"{case['id']}.json").read_text(encoding="utf-8")
    )["output"]
    event = await add_event(session)
    result = await run_skill(
        skill,
        case["input"],
        provider=FakeProvider([output], usage=Usage(input_tokens=100, output_tokens=50)),
        config=load_llm_config(REPO / "config" / "llm.yaml"),
        store=SqlRunStore(session),
        event_id=event.id,
        clock=lambda: NOW,
    )
    await session.commit()
    row = (await session.execute(select(AIAnalysisRun))).scalar_one()
    assert row.id == result.run_id
    assert row.event_id == event.id
    assert row.status == "succeeded"
    assert row.result == output


async def test_status_check_constraint(session: AsyncSession) -> None:
    session.add(
        AIAnalysisRun(
            skill_name="x",
            skill_version="0.1.0",
            schema_version="1",
            provider="fake",
            model_id="m",
            status="weird",
            attempts=1,
            trace_id="t",
            created_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_deleting_event_keeps_run(session: AsyncSession) -> None:
    event = await add_event(session)
    await SqlRunStore(session).save(record(NOW, 0.1, event_id=event.id))
    await session.delete(event)
    await session.flush()
    row = (await session.execute(select(AIAnalysisRun))).scalar_one()
    assert row.event_id is None


async def test_risk_assessments_keep_history(session: AsyncSession) -> None:
    event = await add_event(session)
    for score in (Decimal("40.5"), Decimal("72.0")):
        session.add(
            RiskAssessment(
                event_id=event.id,
                dimensions={"impact": 3},
                score=score,
                level="medium",
                policy_version="1",
            )
        )
    await session.flush()
    rows = (await session.execute(select(RiskAssessment))).scalars().all()
    assert len(rows) == 2


async def test_event_relation_rejects_self_loop(session: AsyncSession) -> None:
    event = await add_event(session)
    session.add(EventRelation(from_event_id=event.id, to_event_id=event.id, type="same_event"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_event_relation_type_and_uniqueness(session: AsyncSession) -> None:
    a = await add_event(session, "a")
    b = await add_event(session, "b")
    session.add(
        EventRelation(
            from_event_id=a.id, to_event_id=b.id, type="related_event", score=0.7, evidence=["e1"]
        )
    )
    await session.flush()
    session.add(EventRelation(from_event_id=a.id, to_event_id=b.id, type="related_event"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_event_relation_unknown_type(session: AsyncSession) -> None:
    a = await add_event(session, "a")
    b = await add_event(session, "b")
    session.add(EventRelation(from_event_id=a.id, to_event_id=b.id, type="cousin"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_run_id_is_uuid(session: AsyncSession) -> None:
    run_id = await SqlRunStore(session).save(record(NOW, 0.0))
    assert isinstance(run_id, uuid.UUID)
