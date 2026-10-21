"""P3-T03：管理 API（事件列表／詳細／查證、警示列表／撤回、儀表板、稽核查詢）。

- 回應一律 `{data, meta, error}`，列表為 cursor 分頁（規格 §10.1）。
- 所有寫入帶 request_id 並寫入稽核；狀態轉移由 app/domain/states.py 檢查，非法回 CONFLICT。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.main import REQUEST_ID_HEADER, create_app
from app.models import (
    AIAnalysisRun,
    Alert,
    AlertDelivery,
    AuditLog,
    Event,
    EventEvidence,
    RiskAssessment,
    Source,
    SourceItem,
)
from app.services.auth import create_or_reset_admin

pytestmark = pytest.mark.anyio

EVENTS = "/api/v1/admin/events"
ALERTS = "/api/v1/admin/alerts"
DASHBOARD = "/api/v1/admin/dashboard"
AUDIT_LOGS = "/api/v1/admin/audit-logs"

ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
# 服務帳號沒有管理端權限：用來驗證 403 與 auth.forbidden 稽核
INGESTION = Principal(
    kind="service", subject="ingestion", roles=frozenset({Role.SERVICE_INGESTION})
)
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


class Api:
    def __init__(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        self.app = app
        self.client = client

    def as_principal(self, principal: Principal) -> None:
        self.app.dependency_overrides[get_principal] = lambda: principal

    async def get(self, path: str, **params: Any) -> httpx2.Response:
        return await self.client.get(path, params=params)

    async def post(
        self, path: str, body: dict[str, Any] | None = None, **headers: str
    ) -> httpx2.Response:
        return await self.client.post(path, json=body, headers=headers)


@pytest.fixture()
async def api(session: AsyncSession) -> AsyncIterator[Api]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://test") as client:
        api = Api(app, client)
        api.as_principal(ADMIN)
        yield api


def assert_envelope(body: dict[str, Any], *, ok: bool = True) -> None:
    assert set(body) == {"data", "meta", "error"}
    assert body["meta"]["request_id"]
    if ok:
        assert body["error"] is None
    else:
        assert body["data"] is None
        assert body["error"]["code"]


async def make_event(
    session: AsyncSession,
    *,
    title: str = "工程四館停電",
    type_: str = "power_outage",
    status: str = "unverified",
    created_at: datetime = T0,
) -> Event:
    event = Event(title=title, type=type_, status=status, created_at=created_at)
    session.add(event)
    await session.flush()
    return event


async def make_alert(
    session: AsyncSession, event: Event, *, state: str = "published", created_at: datetime = T0
) -> Alert:
    alert = Alert(
        event_id=event.id,
        audience="owner",
        state=state,
        version=1,
        body="工程四館停電，請留意。",
        idempotency_key=uuid.uuid4().hex,
        created_at=created_at,
    )
    session.add(alert)
    await session.flush()
    return alert


async def audits(session: AsyncSession, action: str) -> list[AuditLog]:
    rows = await session.execute(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at, AuditLog.id)
    )
    return list(rows.scalars())


# --- 事件列表 -----------------------------------------------------------------------------


async def test_list_events_newest_first_with_cursor(api: Api, session: AsyncSession) -> None:
    for i in range(5):
        await make_event(session, title=f"事件{i}", created_at=T0 + timedelta(hours=i))
    await session.commit()

    first = await api.get(EVENTS, limit=2)
    assert first.status_code == 200
    body = first.json()
    assert_envelope(body)
    assert [e["title"] for e in body["data"]] == ["事件4", "事件3"]
    assert body["meta"]["limit"] == 2
    cursor = body["meta"]["next_cursor"]
    assert cursor

    titles: list[str] = [e["title"] for e in body["data"]]
    while cursor:
        page = (await api.get(EVENTS, limit=2, cursor=cursor)).json()
        titles += [e["title"] for e in page["data"]]
        cursor = page["meta"]["next_cursor"]
    assert titles == ["事件4", "事件3", "事件2", "事件1", "事件0"]


async def test_list_events_filters_and_includes_latest_risk(
    api: Api, session: AsyncSession
) -> None:
    outage = await make_event(session, status="verified")
    await make_event(session, title="宿舍漏水", type_="facility", created_at=T0 + timedelta(1))
    for level, score, minutes in (("medium", "40", 0), ("high", "72.5", 5)):
        session.add(
            RiskAssessment(
                event_id=outage.id,
                dimensions={},
                score=Decimal(score),
                level=level,
                policy_version="1",
                created_at=T0 + timedelta(minutes=minutes),
            )
        )
    await session.commit()

    res = await api.get(EVENTS, status="verified")
    data = res.json()["data"]
    assert [e["id"] for e in data] == [str(outage.id)]
    assert data[0]["risk"] == {"score": 72.5, "level": "high"}
    assert data[0]["evidence_count"] == 0

    by_type = (await api.get(EVENTS, type="facility")).json()["data"]
    assert [e["title"] for e in by_type] == ["宿舍漏水"]
    assert by_type[0]["risk"] is None


@pytest.mark.parametrize(
    "params",
    [{"cursor": "not-a-cursor"}, {"limit": 0}, {"limit": 101}, {"status": "nope"}],
)
async def test_list_events_rejects_bad_params(api: Api, params: dict[str, Any]) -> None:
    res = await api.get(EVENTS, **params)
    assert res.status_code == 422
    body = res.json()
    assert_envelope(body, ok=False)
    assert body["error"]["code"] == "VALIDATION_ERROR"


# --- 事件詳細 -----------------------------------------------------------------------------


async def test_event_detail_includes_evidence_and_risk_history(
    api: Api, session: AsyncSession
) -> None:
    event = await make_event(session)
    source = Source(
        code="ncu-sec", name="ncu-sec", type="rpage", access_policy="approved", poll_interval=3600
    )
    session.add(source)
    await session.flush()
    item = SourceItem(source_id=source.id, url="https://example.edu/a", published_at=T0)
    session.add(item)
    await session.flush()
    session.add(
        EventEvidence(
            event_id=event.id, source_item_id=item.id, support_type="supports", excerpt="停電"
        )
    )
    session.add(
        RiskAssessment(
            event_id=event.id,
            dimensions={"safety": 3},
            score=Decimal("55"),
            level="medium",
            policy_version="1",
        )
    )
    await session.commit()

    res = await api.get(f"{EVENTS}/{event.id}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["id"] == str(event.id)
    assert data["status"] == "unverified"
    assert data["evidence"] == [
        {
            "source_item_id": str(item.id),
            "source_code": "ncu-sec",
            "url": "https://example.edu/a",
            "published_at": T0.isoformat().replace("+00:00", "Z"),
            "support_type": "supports",
            "excerpt": "停電",
        }
    ]
    assert [r["level"] for r in data["risk_assessments"]] == ["medium"]
    assert data["risk_assessments"][0]["dimensions"] == {"safety": 3}


async def test_event_detail_not_found(api: Api) -> None:
    res = await api.get(f"{EVENTS}/{uuid.uuid4()}")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


# --- 事件查證 -----------------------------------------------------------------------------


async def test_verify_changes_status_and_writes_audit_with_request_id(
    api: Api, session: AsyncSession
) -> None:
    event = await make_event(session, status="unverified")
    await session.commit()
    rid = str(uuid.uuid4())

    res = await api.post(
        f"{EVENTS}/{event.id}/verify",
        {"status": "verified", "note": "校方公告證實"},
        **{REQUEST_ID_HEADER: rid},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert_envelope(body)
    assert body["meta"]["request_id"] == rid
    assert body["data"] == {"id": str(event.id), "status": "verified", "previous": "unverified"}

    await session.refresh(event)
    assert event.status == "verified"
    [log] = await audits(session, "event.verify")
    assert log.request_id == rid
    assert log.actor == ADMIN.actor
    assert log.target == f"event:{event.id}"
    assert log.meta == {"from": "unverified", "to": "verified", "note": "校方公告證實"}


@pytest.mark.parametrize(
    ("current", "target"),
    [("archived", "verified"), ("analyzing", "verified"), ("verified", "verified")],
)
async def test_verify_illegal_transition_is_conflict(
    api: Api, session: AsyncSession, current: str, target: str
) -> None:
    event = await make_event(session, status=current)
    await session.commit()

    res = await api.post(f"{EVENTS}/{event.id}/verify", {"status": target})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
    await session.refresh(event)
    assert event.status == current
    assert await audits(session, "event.verify") == []


@pytest.mark.parametrize("target", ["detected", "analyzing", "bogus"])
async def test_verify_rejects_pipeline_only_or_unknown_status(
    api: Api, session: AsyncSession, target: str
) -> None:
    event = await make_event(session)
    await session.commit()
    res = await api.post(f"{EVENTS}/{event.id}/verify", {"status": target})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_verify_note_with_pii_is_rejected_without_echo(
    api: Api, session: AsyncSession
) -> None:
    event = await make_event(session)
    await session.commit()
    res = await api.post(
        f"{EVENTS}/{event.id}/verify", {"status": "verified", "note": "聯絡 0912345678"}
    )
    assert res.status_code == 422
    assert "0912345678" not in res.text
    await session.refresh(event)
    assert event.status == "unverified"


async def test_verify_unknown_event_is_not_found(api: Api) -> None:
    res = await api.post(f"{EVENTS}/{uuid.uuid4()}/verify", {"status": "verified"})
    assert res.status_code == 404


async def test_verify_requires_permission_and_audits_forbidden(
    api: Api, session: AsyncSession
) -> None:
    event = await make_event(session)
    await session.commit()
    api.as_principal(INGESTION)

    res = await api.post(f"{EVENTS}/{event.id}/verify", {"status": "verified"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"
    [log] = await audits(session, "auth.forbidden")
    assert log.meta == {"permission": "events:verify"}


async def test_verify_with_real_session_needs_csrf(session: AsyncSession) -> None:
    """不覆寫身分：真實登入的 cookie session，寫入時缺 CSRF 被拒、帶上則成功。"""
    await create_or_reset_admin(session, "jamie", "a-long-test-passphrase")
    event = await make_event(session)
    await session.commit()
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://test") as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "jamie", "password": "a-long-test-passphrase"},
        )
        csrf = login.json()["data"]["csrf_token"]
        url = f"{EVENTS}/{event.id}/verify"

        denied = await client.post(url, json={"status": "verified"})
        assert denied.status_code == 403
        ok = await client.post(url, json={"status": "verified"}, headers={"X-CSRF-Token": csrf})
        assert ok.status_code == 200, ok.text


# --- 警示 ---------------------------------------------------------------------------------


async def test_list_alerts_with_state_filter(api: Api, session: AsyncSession) -> None:
    event = await make_event(session)
    published = await make_alert(session, event, state="published")
    await make_alert(session, event, state="blocked", created_at=T0 + timedelta(hours=1))
    await session.commit()

    everything = (await api.get(ALERTS)).json()
    assert_envelope(everything)
    assert [a["state"] for a in everything["data"]] == ["blocked", "published"]
    assert everything["data"][1]["event_title"] == "工程四館停電"

    only = (await api.get(ALERTS, state="published")).json()["data"]
    assert [a["id"] for a in only] == [str(published.id)]
    assert only[0]["body"] == "工程四館停電，請留意。"


@pytest.mark.parametrize("state", ["published", "updated"])
async def test_retract_alert_records_reason_and_audit(
    api: Api, session: AsyncSession, state: str
) -> None:
    event = await make_event(session)
    alert = await make_alert(session, event, state=state)
    await session.commit()
    rid = str(uuid.uuid4())

    res = await api.post(
        f"{ALERTS}/{alert.id}/retract", {"reason": "官方已澄清為誤報"}, **{REQUEST_ID_HEADER: rid}
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"] == {"id": str(alert.id), "state": "retracted", "previous": state}

    await session.refresh(alert)
    assert alert.state == "retracted"
    assert alert.retract_reason == "官方已澄清為誤報"
    [log] = await audits(session, "alert.retract")
    assert log.request_id == rid
    assert log.target == f"alert:{alert.id}"
    assert log.meta == {"from": state, "reason": "官方已澄清為誤報", "version": 1}


@pytest.mark.parametrize("state", ["candidate", "blocked", "retracted", "expired"])
async def test_retract_from_illegal_state_is_conflict(
    api: Api, session: AsyncSession, state: str
) -> None:
    event = await make_event(session)
    alert = await make_alert(session, event, state=state)
    await session.commit()

    res = await api.post(f"{ALERTS}/{alert.id}/retract", {"reason": "測試"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
    await session.refresh(alert)
    assert alert.state == state


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 201}])
async def test_retract_requires_reason(
    api: Api, session: AsyncSession, body: dict[str, Any]
) -> None:
    event = await make_event(session)
    alert = await make_alert(session, event)
    await session.commit()
    res = await api.post(f"{ALERTS}/{alert.id}/retract", body)
    assert res.status_code == 422


async def test_retract_unknown_alert_is_not_found(api: Api) -> None:
    res = await api.post(f"{ALERTS}/{uuid.uuid4()}/retract", {"reason": "測試"})
    assert res.status_code == 404


async def test_alert_delivery_unique_per_channel_destination_version(
    session: AsyncSession,
) -> None:
    from sqlalchemy.exc import IntegrityError

    event = await make_event(session)
    alert = await make_alert(session, event)
    for _ in range(2):
        session.add(
            AlertDelivery(
                alert_id=alert.id, channel="inbox", destination="owner", version=1, status="sent"
            )
        )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


# --- 儀表板 -------------------------------------------------------------------------------


async def test_dashboard_summary(api: Api, session: AsyncSession) -> None:
    now = datetime.now(UTC)
    hot = await make_event(session, status="verified", created_at=now - timedelta(days=1))
    await make_event(session, status="unverified", type_="facility", created_at=now)
    await make_event(session, status="archived", created_at=now - timedelta(days=30))
    session.add(
        RiskAssessment(
            event_id=hot.id, dimensions={}, score=Decimal("80"), level="high", policy_version="1"
        )
    )
    await make_alert(session, hot, state="published")
    for status, cost in (("succeeded", "0.010000"), ("failed", "0.002000")):
        session.add(
            AIAnalysisRun(
                skill_name="s05-evidence-verifier",
                skill_version="1.0.0",
                schema_version="1",
                provider="fake",
                model_id="fake",
                status=status,
                attempts=1,
                cost_usd=Decimal(cost),
                trace_id="t",
            )
        )
    await session.commit()

    res = await api.get(DASHBOARD)
    assert res.status_code == 200
    body = res.json()
    assert_envelope(body)
    data = body["data"]
    assert data["events"]["by_status"] == {"verified": 1, "unverified": 1, "archived": 1}
    assert data["events"]["by_type"] == {"power_outage": 2, "facility": 1}
    assert data["events"]["created_last_7d"] == 2
    assert data["events"]["high_risk_open"] == [
        {"id": str(hot.id), "title": hot.title, "level": "high"}
    ]
    assert data["alerts"]["by_state"] == {"published": 1}
    [skill] = data["ai_runs_last_7d"]
    assert skill == {
        "skill_name": "s05-evidence-verifier",
        "runs": 2,
        "failed": 1,
        "abstained": 0,
        "failure_rate": 0.5,
        "cost_usd": 0.012,
    }
    assert body["meta"]["window_days"] == 7


async def test_dashboard_requires_permission(api: Api) -> None:
    api.as_principal(INGESTION)
    assert (await api.get(DASHBOARD)).status_code == 403


# --- 稽核查詢 -----------------------------------------------------------------------------


async def test_audit_logs_list_filter_and_reading_is_audited(
    api: Api, session: AsyncSession
) -> None:
    for i, action in enumerate(["event.verify", "alert.retract", "event.verify"]):
        session.add(
            AuditLog(
                actor="admin:u-1",
                action=action,
                target=f"event:{i}",
                request_id=f"r-{i}",
                created_at=T0 + timedelta(minutes=i),
            )
        )
    await session.commit()

    res = await api.get(AUDIT_LOGS, action="event.verify", limit=1)
    assert res.status_code == 200
    body = res.json()
    assert_envelope(body)
    assert [log["request_id"] for log in body["data"]] == ["r-2"]
    rest = (
        await api.get(AUDIT_LOGS, action="event.verify", cursor=body["meta"]["next_cursor"])
    ).json()
    assert [log["request_id"] for log in rest["data"]] == ["r-0"]
    assert rest["meta"]["next_cursor"] is None

    reads = await audits(session, "audit_logs.read")
    assert len(reads) == 2
    assert reads[0].meta == {"action": "event.verify", "limit": 1}


async def test_audit_logs_time_range(api: Api, session: AsyncSession) -> None:
    for i in range(3):
        session.add(
            AuditLog(
                actor="a",
                action="event.verify",
                request_id=f"r-{i}",
                created_at=T0 + timedelta(days=i),
            )
        )
    await session.commit()
    res = await api.get(
        AUDIT_LOGS,
        since=(T0 + timedelta(hours=1)).isoformat(),
        until=(T0 + timedelta(days=1, hours=1)).isoformat(),
        action="event.verify",
    )
    assert [log["request_id"] for log in res.json()["data"]] == ["r-1"]


async def test_audit_logs_reject_naive_time(api: Api) -> None:
    res = await api.get(AUDIT_LOGS, since="2026-10-01T00:00:00")
    assert res.status_code == 422


async def test_audit_logs_require_permission(api: Api, session: AsyncSession) -> None:
    api.as_principal(INGESTION)
    res = await api.get(AUDIT_LOGS)
    assert res.status_code == 403
    assert await audits(session, "audit_logs.read") == []


# --- 統一錯誤格式 ---------------------------------------------------------------------------


async def test_unexpected_error_uses_envelope_without_details(session: AsyncSession) -> None:
    app = create_app()

    async def broken() -> AsyncIterator[AsyncSession]:
        raise RuntimeError("secret detail 0912345678")
        yield session  # pragma: no cover

    app.dependency_overrides[get_db_session] = broken
    app.dependency_overrides[get_principal] = lambda: ADMIN
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="https://test") as client:
        res = await client.get(EVENTS)
    assert res.status_code == 500
    body = res.json()
    # 未預期例外在最外層處理，此時已離開 request_id 的範圍，不檢查 meta.request_id
    assert set(body) == {"data", "meta", "error"}
    assert body["data"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in res.text
