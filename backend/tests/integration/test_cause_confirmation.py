"""P7-T07：人工確認原因（ADR-0017，`app/reports/cause_decisions.py`）；P8-T13 起舊報告唯讀。

- 只能確認最新一份有效小報告中 basis 為 hypothesized 的原因；確認依據必填、最多 200 字、不得含個資；
- P8-T13（ADR-0020 決策 3）：小報告的措施不再進入公開快照；P8-T06 起改善進度只在系列分析頁公開，
  舊的確認與回滾不再改變事件的公開快照；
- 疑似指控的措施不出現在公開端；重新產生小報告後要重新確認；
- 決策 append-only 保存（最新一筆為目前狀態），稽核 `report.cause_decide` 不含依據文字；
- 管理端 API：`POST /api/v1/admin/events/{id}/report/causes/{cause_id}/decision` 停用，回 CONFLICT
  （改到系列分析確認原因，`series.cause_decide`）；事件詳細的原因仍附既有的 `manual_confirmation`。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.main import create_app
from app.models import AuditLog
from app.models.event import Event
from app.models.publication import PublicationVersion
from app.models.report import EventReport, ReportCauseDecision
from app.publishing.sync import SyncAction
from app.reports.cause_decisions import (
    BASIS_MAX,
    CauseConflictError,
    CauseDecision,
    CauseInvalidError,
    CauseNotFoundError,
    confirmed_cause_ids,
    decide_cause,
)
from app.reports.service import latest_report
from tests.integration.test_public_api import (
    ADMIN,
    add_event,
    measure,
    publication_of,
    publish,
)

pytestmark = pytest.mark.anyio

CAUSE = {
    "cause_id": "c1",
    "description": "屋頂防水層老化",
    "basis": "hypothesized",
    "label": "推測，尚未查證",
    "quotes": [],
    "to_verify": "向總務處確認維修紀錄",
    "evidence_ids": [],
}
OFFICIAL_CAUSE = {**CAUSE, "cause_id": "c2", "basis": "confirmed", "to_verify": None}
SCHOOL_MEASURE = {**measure("更換三樓屋頂防水層", cause_basis="hypothesized"), "audience": "school"}
BASIS = "11/5 電話向總務處確認"
URL = "/api/v1/admin/events/{}/report/causes/{}/decision"


async def seed(session: AsyncSession, **overrides: Any) -> tuple[Event, EventReport]:
    values: dict[str, Any] = {
        "causes": [CAUSE, OFFICIAL_CAUSE],
        "measures": [SCHOOL_MEASURE],
        "actions": [{"title": "更換屋頂防水層", "measure": SCHOOL_MEASURE}],
    }
    values.update(overrides)
    event = await add_event(session, **values)
    report = await latest_report(session, event.id)
    assert report is not None
    return event, report


async def confirm(
    session: AsyncSession, event: Event, report: EventReport, cause_id: str = "c1"
) -> ReportCauseDecision:
    decision = await decide_cause(
        session, ADMIN, event.id, report.id, cause_id, CauseDecision.CONFIRMED, basis=BASIS
    )
    await session.commit()
    return decision


async def rollback(session: AsyncSession, event: Event, report: EventReport) -> None:
    await decide_cause(session, ADMIN, event.id, report.id, "c1", CauseDecision.HYPOTHESIZED)
    await session.commit()


async def snapshot(session: AsyncSession, event: Event) -> dict[str, Any]:
    pub = await publication_of(session, event)
    version = await session.scalar(
        select(PublicationVersion).where(
            PublicationVersion.publication_id == pub.id, PublicationVersion.version == pub.version
        )
    )
    assert version is not None
    return version.content or {}


def public_measures(content: dict[str, Any]) -> list[str]:
    return [m["action"] for m in (content.get("report") or {}).get("measures", [])]


def public_progress(content: dict[str, Any]) -> list[str]:
    return [a["title"] for a in content.get("progress", [])]


# ---------- 公開同步 ----------


async def test_confirmation_no_longer_changes_event_snapshot(
    session: AsyncSession,
) -> None:
    """P8-T06：改善進度移到系列分析頁，事件快照不因舊的確認或回滾而改變。"""
    event, report = await seed(session)
    await publish(session, event)
    before = await snapshot(session, event)
    assert public_measures(before) == []
    assert public_progress(before) == []

    await confirm(session, event, report)
    assert await publish(session, event) is SyncAction.NONE
    after = await snapshot(session, event)
    assert after == before
    assert after["report"] is None
    assert public_progress(after) == []
    assert "屋頂防水層老化" not in str(after)  # 原因本身仍不公開
    assert BASIS not in str(after)

    await rollback(session, event, report)
    assert await publish(session, event) is SyncAction.NONE
    assert public_measures(await snapshot(session, event)) == []
    assert public_progress(await snapshot(session, event)) == []


async def test_allegation_event_stays_private_after_confirmation(session: AsyncSession) -> None:
    accusation = {**SCHOOL_MEASURE, "action": "調查警衛偷竊座位上的筆電"}
    event, report = await seed(session, measures=[accusation], actions=[])
    await confirm(session, event, report)
    await publish(session, event)
    assert "偷竊" not in str(await snapshot(session, event))  # 小報告的措施不再進入公開端


async def test_regenerated_report_needs_new_confirmation(session: AsyncSession) -> None:
    event, old = await seed(session)
    await confirm(session, event, old)
    new = EventReport(
        event_id=event.id,
        trigger="manual",
        verification_status="verified",
        risk_level="high",
        causes_status="ok",
        causes=[CAUSE],
        measures_status="ok",
        measures=[SCHOOL_MEASURE],
        trace_id="t2",
    )
    session.add(new)
    await session.commit()

    assert await confirmed_cause_ids(session, new.id) == frozenset()
    await publish(session, event)
    content = await snapshot(session, event)
    assert public_measures(content) == []
    assert public_progress(content) == []  # 改善措施屬於舊報告，不沿用舊確認

    with pytest.raises(CauseConflictError):
        await confirm(session, event, old)


async def test_invalidated_report_cannot_be_confirmed(session: AsyncSession) -> None:
    event, report = await seed(session)
    report.invalidated_at = report.created_at
    await session.commit()
    with pytest.raises(CauseConflictError):
        await confirm(session, event, report)


# ---------- 確認的規則 ----------


@pytest.mark.parametrize("basis", [None, "", "   "])
async def test_confirmation_requires_basis(session: AsyncSession, basis: str | None) -> None:
    event, report = await seed(session)
    with pytest.raises(CauseInvalidError) as err:
        await decide_cause(
            session, ADMIN, event.id, report.id, "c1", CauseDecision.CONFIRMED, basis=basis
        )
    assert err.value.field == "basis"


@pytest.mark.parametrize(
    "basis", ["打給 0912-345-678 的王先生確認", "寄信到 someone@example.com 確認", "x" * 201]
)
async def test_basis_with_pii_or_too_long_is_rejected(session: AsyncSession, basis: str) -> None:
    event, report = await seed(session)
    with pytest.raises(CauseInvalidError):
        await decide_cause(
            session, ADMIN, event.id, report.id, "c1", CauseDecision.CONFIRMED, basis=basis
        )
    assert await session.scalar(select(ReportCauseDecision.id)) is None
    assert BASIS_MAX == 200


async def test_only_hypothesized_causes_can_be_confirmed(session: AsyncSession) -> None:
    event, report = await seed(session)
    with pytest.raises(CauseConflictError):
        await confirm(session, event, report, "c2")
    with pytest.raises(CauseNotFoundError):
        await confirm(session, event, report, "c9")
    with pytest.raises(CauseNotFoundError):
        await decide_cause(
            session, ADMIN, uuid.uuid4(), report.id, "c1", CauseDecision.CONFIRMED, basis=BASIS
        )


async def test_decision_transitions(session: AsyncSession) -> None:
    event, report = await seed(session)
    with pytest.raises(CauseConflictError):  # 尚未確認，不能回滾
        await rollback(session, event, report)
    await confirm(session, event, report)
    with pytest.raises(CauseConflictError):  # 已確認，不能重複確認
        await confirm(session, event, report)
    await rollback(session, event, report)
    await confirm(session, event, report)  # 回滾後可以再確認
    rows = list(await session.scalars(select(ReportCauseDecision.decision)))
    assert sorted(rows) == ["confirmed", "confirmed", "hypothesized"]
    assert await confirmed_cause_ids(session, report.id) == frozenset({"c1"})


async def test_decision_keeps_basis_and_audit_has_codes_only(session: AsyncSession) -> None:
    event, report = await seed(session)
    row = await confirm(session, event, report)
    assert (row.basis, row.decided_by) == (BASIS, ADMIN.actor)
    assert row.decided_at is not None
    log = await session.scalar(select(AuditLog).where(AuditLog.action == "report.cause_decide"))
    assert log is not None
    assert log.target == f"event:{event.id}"
    assert log.meta == {
        "report_id": str(report.id),
        "cause_id": "c1",
        "from": "hypothesized",
        "to": "confirmed",
    }
    assert BASIS not in str(log.meta)


# ---------- 管理端 API ----------


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


async def test_api_is_retired_and_keeps_existing_decisions(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    event, report = await seed(session)
    await confirm(session, event, report)  # P8-T13 之前做的確認
    url = URL.format(event.id, "c1")
    for body in (
        {"report_id": str(report.id), "decision": "hypothesized"},
        {"report_id": str(report.id), "decision": "confirmed", "basis": BASIS},
        {"report_id": str(uuid.uuid4()), "decision": "confirmed", "basis": BASIS},
    ):
        res = await client.post(url, json=body)
        assert res.status_code == 409, res.text
        assert res.json()["error"]["code"] == "CONFLICT"
    rows = list(await session.scalars(select(ReportCauseDecision.decision)))
    assert rows == ["confirmed"]

    detail = (await client.get(f"/api/v1/admin/events/{event.id}")).json()["data"]
    causes = detail["report"]["causes"]["items"]
    assert causes[0]["manual_confirmation"]["basis"] == BASIS
    assert causes[0]["basis"] == "hypothesized"  # S07 的判定不改寫
    assert causes[1]["manual_confirmation"] is None


async def test_api_requires_verify_permission(session: AsyncSession) -> None:
    event, report = await seed(session)
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    app.dependency_overrides[get_principal] = lambda: Principal(
        kind="service", subject="ai", roles=frozenset({Role.SERVICE_AI})
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.post(
            URL.format(event.id, "c1"),
            json={"report_id": str(report.id), "decision": "confirmed", "basis": BASIS},
        )
    assert res.status_code == 403
    assert await session.scalar(select(ReportCauseDecision.id)) is None
