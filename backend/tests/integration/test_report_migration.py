"""P8-T13：停用單一事件小報告並遷移到系列（ADR-0020 決策 3）。

- migration `0024_series_actions_backfill`：既有事件層級的改善措施（`response_actions.event_id`）
  與舊小報告的人工確認（`report_cause_decisions`）掛到事件所屬的系列，原本的 event_id／report_id
  保留；downgrade 只拿掉掛上的系列，不刪資料。
- 遷移後在 `GET /api/v1/admin/series/{id}` 查得到（措施附 event_id；舊確認列在
  `legacy_cause_decisions`）。
- 舊小報告唯讀：事件詳細的報告標 `legacy`；手動產生報告與確認舊報告原因的端點回 CONFLICT，
  不寫資料、不排入任務。
- 事件建立後不再呼叫 S07／S08：管線沒有 report_hook，報告的 Celery 任務不再註冊。
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import anyio.to_thread
import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.main import create_app
from app.models import AuditLog, Event, EventEvidence
from app.models.report import EventReport, ReportCauseDecision
from app.models.response_action import ResponseAction
from app.models.series import EventSeries, EventSeriesMember
from app.pipeline.analyze_item import PipelineDeps, analyze_event
from app.resilience.actions import create_action
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

BEFORE = "0023_series_analysis"
HEAD = "head"
CAUSE = {
    "cause_id": "c1",
    "description": "屋頂防水層老化",
    "basis": "hypothesized",
    "label": "推測，尚未查證",
    "quotes": [],
    "to_verify": "向總務處確認維修紀錄",
    "evidence_ids": [],
}
BASIS = "11/5 電話向總務處確認"


async def migrate(cfg: Config, revision: str, *, down: bool = False) -> None:
    """在另一個執行緒跑 alembic（env.py 內部使用 asyncio.run）。"""
    fn = command.downgrade if down else command.upgrade
    await anyio.to_thread.run_sync(fn, cfg, revision)


@dataclasses.dataclass
class Legacy:
    series: EventSeries
    event: Event
    report: EventReport
    lone: Event


async def add_event(session: AsyncSession, title: str) -> Event:
    event = Event(title=title, type="facility", status="verified")
    session.add(event)
    await session.flush()
    return event


async def add_report(session: AsyncSession, event: Event) -> EventReport:
    report = EventReport(
        event_id=event.id,
        trigger="auto",
        verification_status="verified",
        causes_status="ok",
        causes=[CAUSE],
        measures_status="ok",
        measures=[{"action": "更換屋頂防水層", "audience": "school", "cause_id": "c1"}],
        trace_id="t",
    )
    session.add(report)
    await session.flush()
    return report


async def legacy_rows(session: AsyncSession) -> Legacy:
    """0023 時的資料：一個在系列中的事件（舊報告、確認、事件層級措施）與一個不在系列的事件。"""
    series = EventSeries(category="facility", title="圖書館漏水")
    session.add(series)
    event = await add_event(session, "總圖三樓漏水")
    lone = await add_event(session, "被合併的舊事件")
    session.add(
        EventSeriesMember(
            event_id=event.id, series_id=series.id, method="ai", joined_at=datetime.now(UTC)
        )
    )
    report = await add_report(session, event)
    # 0023 時 report_cause_decisions 還沒有 series_id，用 SQL 只寫當時存在的欄位
    await session.execute(
        text(
            "INSERT INTO report_cause_decisions (id, report_id, cause_id, decision, basis, "
            "decided_by) VALUES (:id, :report, 'c1', 'confirmed', :basis, 'u-1')"
        ),
        {"id": uuid.uuid4(), "report": report.id, "basis": BASIS},
    )
    for values in (
        {"event_id": event.id, "origin": "measure", "report_id": report.id, "measure_index": 0},
        {"event_id": event.id, "origin": "manual"},
        {"event_id": lone.id, "origin": "manual"},
        {"series_id": series.id, "origin": "manual"},
    ):
        session.add(ResponseAction(title="更換屋頂防水層", state="handling", **values))
    await session.commit()
    return Legacy(series, event, report, lone)


async def actions_by_event(session: AsyncSession) -> dict[uuid.UUID | None, list[Any]]:
    rows = (
        await session.execute(
            select(ResponseAction.event_id, ResponseAction.series_id).execution_options(
                populate_existing=True
            )
        )
    ).all()
    out: dict[uuid.UUID | None, list[Any]] = {}
    for event_id, series_id in rows:
        out.setdefault(event_id, []).append(series_id)
    return out


async def decision_series(session: AsyncSession) -> list[Any]:
    rows = await session.execute(text("SELECT series_id FROM report_cause_decisions"))
    return list(rows.scalars())


# ---------- migration ----------


async def test_backfill_attaches_event_actions_and_confirmations_to_series(
    session: AsyncSession, alembic_cfg: Config
) -> None:
    await migrate(alembic_cfg, BEFORE, down=True)
    try:
        legacy = await legacy_rows(session)
        await session.close()
        await migrate(alembic_cfg, HEAD)

        by_event = await actions_by_event(session)
        assert by_event[legacy.event.id] == [legacy.series.id, legacy.series.id]
        assert by_event[legacy.lone.id] == [None]  # 不在系列的事件不掛
        assert by_event[None] == [legacy.series.id]  # 系列層級的措施不變
        assert await decision_series(session) == [legacy.series.id]
        report_ids = list(await session.scalars(select(ReportCauseDecision.report_id)))
        assert report_ids == [legacy.report.id]  # 原本的 report_id 保留
        await session.commit()
        await session.close()

        await migrate(alembic_cfg, BEFORE, down=True)
        by_event = await actions_by_event(session)
        assert by_event[legacy.event.id] == [None, None]  # 只拿掉掛上的系列
        assert by_event[None] == [legacy.series.id]
        assert await session.scalar(select(func.count()).select_from(ResponseAction)) == 4
        count = await session.scalar(text("SELECT count(*) FROM report_cause_decisions"))
        assert count == 1  # 不刪資料
        await session.commit()
        await session.close()
    finally:
        await migrate(alembic_cfg, HEAD)


# ---------- 遷移後在系列查得到 ----------


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


async def migrated_rows(session: AsyncSession) -> Legacy:
    """遷移後的樣子：事件層級措施與舊確認都帶 series_id。"""
    series = EventSeries(category="facility", title="圖書館漏水")
    session.add(series)
    event = await add_event(session, "總圖三樓漏水")
    lone = await add_event(session, "其他事件")
    session.add(
        EventSeriesMember(
            event_id=event.id, series_id=series.id, method="ai", joined_at=datetime.now(UTC)
        )
    )
    report = await add_report(session, event)
    session.add(
        ReportCauseDecision(
            report_id=report.id,
            series_id=series.id,
            cause_id="c1",
            decision="confirmed",
            basis=BASIS,
            decided_by="u-1",
        )
    )
    session.add(
        ResponseAction(
            event_id=event.id,
            series_id=series.id,
            origin="measure",
            report_id=report.id,
            measure_index=0,
            title="更換屋頂防水層",
            state="handling",
        )
    )
    await session.commit()
    return Legacy(series, event, report, lone)


async def test_series_detail_lists_migrated_actions_and_confirmations(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    legacy = await migrated_rows(session)

    data = (await client.get(f"/api/v1/admin/series/{legacy.series.id}")).json()["data"]

    (action,) = data["actions"]
    assert action["title"] == "更換屋頂防水層"
    assert action["event_id"] == str(legacy.event.id)
    assert action["origin"] == "measure"
    (decision,) = data["legacy_cause_decisions"]
    assert decision["event_id"] == str(legacy.event.id)
    assert decision["report_id"] == str(legacy.report.id)
    assert decision["cause_id"] == "c1"
    assert decision["description"] == "屋頂防水層老化"
    assert decision["decision"] == "confirmed"
    assert decision["basis"] == BASIS


async def test_legacy_confirmation_shows_only_latest_decision(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    legacy = await migrated_rows(session)
    session.add(
        ReportCauseDecision(
            report_id=legacy.report.id,
            series_id=legacy.series.id,
            cause_id="c1",
            decision="hypothesized",
            decided_by="u-1",
            decided_at=datetime.now(UTC),
        )
    )
    await session.commit()

    data = (await client.get(f"/api/v1/admin/series/{legacy.series.id}")).json()["data"]

    (decision,) = data["legacy_cause_decisions"]
    assert (decision["decision"], decision["basis"]) == ("hypothesized", None)


async def test_new_event_action_is_attached_to_its_series(session: AsyncSession) -> None:
    legacy = await migrated_rows(session)

    attached = await create_action(session, ADMIN, legacy.event.id, title="加裝防水布")
    alone = await create_action(session, ADMIN, legacy.lone.id, title="清理排水孔")

    assert (attached.event_id, attached.series_id) == (legacy.event.id, legacy.series.id)
    assert (alone.event_id, alone.series_id) == (legacy.lone.id, None)


# ---------- 舊小報告唯讀 ----------


async def test_old_report_is_read_only_and_marked_legacy(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    legacy = await migrated_rows(session)

    detail = (await client.get(f"/api/v1/admin/events/{legacy.event.id}")).json()["data"]
    assert detail["report"]["legacy"] is True
    assert detail["report"]["id"] == str(legacy.report.id)
    assert detail["series_id"] == str(legacy.series.id)

    res = await client.post(f"/api/v1/admin/events/{legacy.event.id}/report")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"

    res = await client.post(
        f"/api/v1/admin/events/{legacy.event.id}/report/causes/c1/decision",
        json={"report_id": str(legacy.report.id), "decision": "hypothesized"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"

    assert await session.scalar(select(func.count()).select_from(EventReport)) == 1
    assert await session.scalar(select(func.count()).select_from(ReportCauseDecision)) == 1
    actions = {"event.report", "report.cause_decide"}
    assert await session.scalar(select(AuditLog.id).where(AuditLog.action.in_(actions))) is None


# ---------- 事件建立後不再呼叫 S07／S08 ----------


def test_pipeline_has_no_report_hook_and_task_is_gone() -> None:
    assert "report_hook" not in {f.name for f in dataclasses.fields(PipelineDeps)}
    assert "reports.generate_event_report" not in celery_app.tasks


async def test_high_risk_event_does_not_call_s07_or_s08(session: AsyncSession) -> None:
    official = await add_item(session, await add_source(session, "ncu-announcements"), "停電公告")
    event = Event(title="工程一館停電", type="facility", status="unverified")
    session.add(event)
    await session.flush()
    session.add(
        EventEvidence(event_id=event.id, source_item_id=official.id, support_type="mentions")
    )
    await session.commit()
    e = official.id.hex
    provider = SkillScript(
        {
            "evidence-verifier": [verification({e: ("supports", "official")}, "verified")],
            "risk-assessor": [risk(e, safety=85)],  # critical
        }
    )

    await analyze_event(session, event.id, deps(provider))

    assert provider.called == ["evidence-verifier", "risk-assessor"]
    assert await session.scalar(select(func.count()).select_from(EventReport)) == 0
