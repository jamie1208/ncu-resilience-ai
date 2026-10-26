"""P3-T08：警示狀態機與個人遞送（G3-2：同版本通知無重複遞送）。

- `sync_event_alert`：依 P3-T07 的規則 candidate → policy_checked → eligible → published；
  內容改變 → updated（版本遞增）；查證為 false → retracted；事件 resolved／archived 或
  不再符合規則 → expired。
- idempotency_key ＝ hash(event_id, alert_version, audience)；重送不重複、撤回後停止。
- 遞送先做管理介面通知中心（inbox），同一版本對同一管道只遞送一次。
- 分析管線（S06 之後）與管理端 verify 會呼叫 `sync_event_alert`。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from sqlalchemy import URL, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.llm.base import LLMResult, Message, Usage
from app.ai.llm.config import load_llm_config
from app.ai.llm.fake_provider import FakeEmbeddingProvider
from app.ai.runner.loader import discover_skills
from app.alerts.delivery import INBOX, OWNER, deliver
from app.alerts.service import SyncAction, idempotency_key, sync_event_alert
from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.domain.states import AlertState, EventStatus
from app.main import create_app
from app.models import (
    AIAnalysisRun,
    Alert,
    AlertDelivery,
    AuditLog,
    Event,
    EventEvidence,
    Location,
    RiskAssessment,
    Source,
    SourceItem,
)
from app.models.source import SourceItemVersion
from app.pipeline.analyze_item import PipelineDeps, analyze_event
from app.risk.engine import load_policy as load_risk_policy

pytestmark = pytest.mark.anyio

ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


async def make_event(
    session: AsyncSession,
    *,
    status: EventStatus = EventStatus.VERIFIED,
    level: str | None = "high",
    title: str = "工程一館停電",
    type_: str = "facility",
) -> Event:
    location = Location(name="工程一館")
    session.add(location)
    await session.flush()
    event = Event(
        title=title,
        type=type_,
        status=status.value,
        location_id=location.id,
        occurrence_start_date=date(2026, 10, 1),
    )
    session.add(event)
    await session.flush()
    if level is not None:
        await add_risk(session, event, level)
    await session.commit()
    return event


async def add_risk(session: AsyncSession, event: Event, level: str) -> None:
    session.add(
        RiskAssessment(
            event_id=event.id,
            dimensions={"safety": 80},
            score=Decimal("80"),
            level=level,
            policy_version="1",
        )
    )
    await session.commit()


async def set_status(session: AsyncSession, event: Event, status: EventStatus) -> None:
    event.status = status.value
    await session.commit()


async def alerts(session: AsyncSession) -> list[Alert]:
    return list((await session.scalars(select(Alert).order_by(Alert.version))).all())


async def deliveries(session: AsyncSession) -> list[AlertDelivery]:
    rows = await session.scalars(select(AlertDelivery).order_by(AlertDelivery.version))
    return list(rows.all())


async def audit_actions(session: AsyncSession) -> list[str]:
    rows = await session.scalars(select(AuditLog.action).order_by(AuditLog.created_at))
    return list(rows.all())


async def sync(session: AsyncSession, event: Event) -> Any:
    result = await sync_event_alert(session, event.id)
    await session.commit()
    return result


# ---------- idempotency_key ----------


def test_idempotency_key_is_hash_of_event_version_audience() -> None:
    event_id = uuid.uuid4()
    key = idempotency_key(event_id, 1, OWNER)
    assert key == hashlib.sha256(f"{event_id}:1:{OWNER}".encode()).hexdigest()
    assert len(key) == 64
    assert key != idempotency_key(event_id, 2, OWNER)
    assert key != idempotency_key(uuid.uuid4(), 1, OWNER)


# ---------- 發布與重送 ----------


async def test_eligible_event_is_published_and_delivered_once(session: AsyncSession) -> None:
    event = await make_event(session)

    result = await sync(session, event)

    assert result.action is SyncAction.PUBLISHED
    (alert,) = await alerts(session)
    assert alert.state == AlertState.PUBLISHED.value
    assert (alert.version, alert.audience) == (1, OWNER)
    assert alert.idempotency_key == idempotency_key(event.id, 1, OWNER)
    assert "工程一館" in alert.body
    (delivery,) = await deliveries(session)
    assert (delivery.channel, delivery.destination, delivery.version) == (INBOX, OWNER, 1)
    assert delivery.status == "delivered"
    assert await audit_actions(session) == ["alert.publish"]
    audit = (await session.scalars(select(AuditLog))).one()
    assert audit.actor == "service:notifier"
    assert "body" not in audit.meta
    assert audit.meta["version"] == 1


async def test_resync_same_version_does_not_redeliver(session: AsyncSession) -> None:
    event = await make_event(session)
    await sync(session, event)

    for _ in range(3):
        result = await sync(session, event)
        assert result.action is SyncAction.NONE

    assert len(await alerts(session)) == 1
    assert len(await deliveries(session)) == 1
    assert await audit_actions(session) == ["alert.publish"]


async def test_deliver_twice_for_same_version_is_noop(session: AsyncSession) -> None:
    event = await make_event(session)
    await sync(session, event)
    (alert,) = await alerts(session)

    assert await deliver(session, alert) == ()
    await session.commit()

    assert len(await deliveries(session)) == 1


async def test_concurrent_syncs_publish_once(session: AsyncSession, test_db_url: URL) -> None:
    event = await make_event(session)
    engine = create_async_engine(test_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def run() -> SyncAction:
        async with maker() as s:
            result = await sync_event_alert(s, event.id)
            await s.commit()
            return result.action

    try:
        actions = await asyncio.gather(*(run() for _ in range(5)))
    finally:
        await engine.dispose()

    assert sorted(actions).count(SyncAction.PUBLISHED) == 1
    assert len(await alerts(session)) == 1
    assert len(await deliveries(session)) == 1


async def test_ineligible_event_creates_no_alert(session: AsyncSession) -> None:
    event = await make_event(session, level="medium")

    result = await sync(session, event)

    assert result.action is SyncAction.NONE
    assert "below_threshold" in result.reasons
    assert await alerts(session) == []
    assert await deliveries(session) == []
    assert await audit_actions(session) == []


async def test_unknown_event_type_does_not_notify(session: AsyncSession) -> None:
    event = await make_event(session, type_="power_outage")

    result = await sync(session, event)

    assert result.action is SyncAction.NONE
    assert result.reasons == ("unknown_event_type",)
    assert await alerts(session) == []


async def test_missing_event_raises(session: AsyncSession) -> None:
    with pytest.raises(LookupError):
        await sync_event_alert(session, uuid.uuid4())


# ---------- 改版 ----------


async def test_content_change_updates_version_and_delivers_new_version(
    session: AsyncSession,
) -> None:
    event = await make_event(session, status=EventStatus.UNVERIFIED)
    await sync(session, event)
    (first,) = await alerts(session)
    unverified_body = first.body
    assert "一般安全提醒" in unverified_body

    await set_status(session, event, EventStatus.VERIFIED)
    result = await sync(session, event)

    assert result.action is SyncAction.UPDATED
    (alert,) = await alerts(session)
    assert alert.state == AlertState.UPDATED.value
    assert alert.version == 2
    assert alert.idempotency_key == idempotency_key(event.id, 2, OWNER)
    assert alert.body != unverified_body
    assert [d.version for d in await deliveries(session)] == [1, 2]

    assert (await sync(session, event)).action is SyncAction.NONE
    assert len(await deliveries(session)) == 2
    assert await audit_actions(session) == ["alert.publish", "alert.update"]


async def test_official_source_uses_factual_template(session: AsyncSession) -> None:
    event = await make_event(session, status=EventStatus.UNVERIFIED)
    source = Source(
        code="ncu-ga", name="總務處", type="rpage", access_policy="approved", poll_interval=3600
    )
    session.add(source)
    await session.flush()
    item = SourceItem(source_id=source.id, url="https://example.edu/a", published_at=T0)
    session.add(item)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    session.add(
        AIAnalysisRun(
            event_id=event.id,
            skill_name="evidence-verifier",
            skill_version="1.0.0",
            schema_version="1",
            provider="fake",
            model_id="fake",
            status="succeeded",
            result={
                "assessments": [
                    {"evidence_id": item.id.hex, "stance": "supports", "claim_type": "official"}
                ]
            },
            attempts=1,
            trace_id="t-1",
        )
    )
    await session.commit()

    await sync(session, event)

    (alert,) = await alerts(session)
    assert "一般安全提醒" not in alert.body
    assert "工程一館停電" in alert.body


# ---------- 撤回與過期 ----------


async def test_retracted_alert_stops_further_delivery(session: AsyncSession) -> None:
    event = await make_event(session, status=EventStatus.UNVERIFIED)
    await sync(session, event)
    (alert,) = await alerts(session)
    alert.state = AlertState.RETRACTED.value
    alert.retract_reason = "使用者撤回"
    await session.commit()

    await set_status(session, event, EventStatus.VERIFIED)  # 內容改變也不再通知
    result = await sync(session, event)

    assert result.action is SyncAction.NONE
    assert "retracted" in result.reasons
    (alert,) = await alerts(session)
    assert (alert.state, alert.version) == (AlertState.RETRACTED.value, 1)
    assert len(await deliveries(session)) == 1


async def test_false_verification_retracts_and_stops(session: AsyncSession) -> None:
    event = await make_event(session)
    await sync(session, event)

    await set_status(session, event, EventStatus.FALSE)
    result = await sync(session, event)

    assert result.action is SyncAction.RETRACTED
    (alert,) = await alerts(session)
    assert alert.state == AlertState.RETRACTED.value
    assert alert.retract_reason
    assert len(await deliveries(session)) == 1

    await set_status(session, event, EventStatus.VERIFIED)
    assert (await sync(session, event)).action is SyncAction.NONE
    assert len(await alerts(session)) == 1
    assert len(await deliveries(session)) == 1
    assert await audit_actions(session) == ["alert.publish", "alert.retract"]


@pytest.mark.parametrize("status", [EventStatus.RESOLVED, EventStatus.ARCHIVED])
async def test_resolved_or_archived_event_expires_alert(
    session: AsyncSession, status: EventStatus
) -> None:
    event = await make_event(session)
    await sync(session, event)

    await set_status(session, event, status)
    result = await sync(session, event)

    assert result.action is SyncAction.EXPIRED
    (alert,) = await alerts(session)
    assert alert.state == AlertState.EXPIRED.value
    assert len(await deliveries(session)) == 1
    assert await audit_actions(session) == ["alert.publish", "alert.expire"]


async def test_risk_drop_expires_and_new_alert_uses_next_version(session: AsyncSession) -> None:
    event = await make_event(session)
    await sync(session, event)

    await add_risk(session, event, "low")
    assert (await sync(session, event)).action is SyncAction.EXPIRED

    await add_risk(session, event, "critical")
    result = await sync(session, event)

    assert result.action is SyncAction.PUBLISHED
    states = [(a.version, a.state) for a in await alerts(session)]
    assert states == [(1, AlertState.EXPIRED.value), (2, AlertState.PUBLISHED.value)]
    assert [d.version for d in await deliveries(session)] == [1, 2]


# ---------- 呼叫端：管理端 verify ----------


@pytest.fixture()
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_principal] = lambda: ADMIN
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_verify_endpoint_syncs_alert(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    event = await make_event(session, status=EventStatus.UNVERIFIED)

    res = await client.post(f"/api/v1/admin/events/{event.id}/verify", json={"status": "verified"})
    assert res.status_code == 200
    (alert,) = await alerts(session)
    assert alert.state == AlertState.PUBLISHED.value
    assert "一般安全提醒" not in alert.body

    res = await client.post(f"/api/v1/admin/events/{event.id}/verify", json={"status": "false"})
    assert res.status_code == 200
    (alert,) = await alerts(session)
    assert alert.state == AlertState.RETRACTED.value
    count = await session.scalar(select(func.count()).select_from(AlertDelivery))
    assert count == 1


# ---------- 呼叫端：分析管線（S06 之後） ----------


class ScriptedProvider:
    """依 schema 名稱回傳固定輸出的假模型（只供本檔的管線測試）。"""

    name = "fake"

    def __init__(self, outputs: dict[str, dict[str, Any]]) -> None:
        self._outputs = outputs

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
        return LLMResult(
            provider=self.name,
            model=model,
            text=json.dumps(self._outputs[skill], ensure_ascii=False),
            usage=Usage(input_tokens=10, output_tokens=10),
            response_id=None,
            latency_ms=1,
        )


def _dim(score: int) -> dict[str, Any]:
    return {"score": score, "uncertainty": 0.3, "reason": "依公告內容評分。"}


async def test_pipeline_analyze_event_syncs_alert(session: AsyncSession) -> None:
    event = await make_event(session, status=EventStatus.UNVERIFIED, level=None)
    source = Source(code="ncu-ga", name="總務處", type="rpage", access_policy="approved")
    session.add(source)
    await session.flush()
    item = SourceItem(
        source_id=source.id, external_id="a-1", url="https://example.test/a", published_at=T0
    )
    session.add(item)
    await session.flush()
    session.add(
        SourceItemVersion(item_id=item.id, content_hash="h" * 64, normalized_text="工程一館停電")
    )
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    await session.commit()
    e = item.id.hex
    provider = ScriptedProvider(
        {
            "evidence-verifier": {
                "status": "ok",
                "assessments": [{"evidence_id": e, "stance": "supports", "claim_type": "official"}],
                "suggested_status": "verified",
                "confidence": 0.9,
                "evidence_ids": [e],
                "evidence_spans": [],
                "reason": "官方公告。",
                "abstain_reason": None,
            },
            "risk-assessor": {
                "status": "ok",
                "dimensions": {
                    "personal_safety": _dim(85),
                    "operations": _dim(80),
                    "property": _dim(60),
                    "reputation": _dim(20),
                    "scope": _dim(70),
                },
                "evidence_ids": [e],
                "evidence_spans": [],
                "reason": "停電影響安全。",
                "abstain_reason": None,
            },
        }
    )
    deps = PipelineDeps(
        provider=provider,
        embedder=FakeEmbeddingProvider(),
        config=load_llm_config(),
        skills=discover_skills(),
        policy=load_risk_policy(),
    )

    result = await analyze_event(session, event.id, deps, trace_id="trace-a")

    assert result.risk_level in ("high", "critical")
    (alert,) = await alerts(session)
    assert alert.state == AlertState.PUBLISHED.value
    assert len(await deliveries(session)) == 1
    publish = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "alert.publish"))
    ).one()
    assert publish.request_id == "trace-a"
