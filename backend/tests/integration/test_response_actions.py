"""P4-T05：改善措施追蹤（F10，G4-4）。

- 追蹤項目可從事件小報告中 S08 的校方措施建立，也可手動新增；同學措施不能建立追蹤項目。
- 生命週期：發現（discovered）→ 處理（handling）→ 恢復（recovered）→ 改進（improved）；
  另可改為不追蹤（dropped）。不在轉移表中的轉移回 CONFLICT；恢復與改進必須附佐證。
- 每次狀態變更都寫入 response_action_transitions（含建立時的「發現」），並寫入稽核紀錄。
- 名稱、負責人、備註、佐證含個資時拒絕。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.domain.states import EventStatus
from app.main import create_app
from app.models import AuditLog
from app.models.report import EventReport
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.reports.service import generate_report, invalidate_reports
from app.resilience.actions import ActionState
from tests.integration.test_event_report import Scene, full_report_script, scene
from tests.integration.test_pipeline import ADMIN, deps

pytestmark = pytest.mark.anyio

# full_report_script 的措施順序：0 同學、1 校方（確認原因）、2 校方（推測原因）
STUDENT_MEASURE = 0
CONFIRMED_MEASURE = 1
HYPOTHESIZED_MEASURE = 2


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


async def reported(session: AsyncSession) -> tuple[Scene, EventReport]:
    s = await scene(session)
    report = await generate_report(session, s.event.id, deps(full_report_script(s)), trigger="auto")
    assert report is not None
    return s, report


async def from_measure(
    client: httpx2.AsyncClient, s: Scene, report: EventReport, index: int, **extra: Any
) -> httpx2.Response:
    return await client.post(
        f"/api/v1/admin/events/{s.event.id}/actions",
        json={"report_id": str(report.id), "measure_index": index, **extra},
    )


async def move(
    client: httpx2.AsyncClient, action_id: str, to: str, **extra: Any
) -> httpx2.Response:
    return await client.post(
        f"/api/v1/admin/actions/{action_id}/transitions", json={"to": to, **extra}
    )


# ---------- 完整生命週期（G4-4） ----------


async def test_full_lifecycle_is_recorded(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s, report = await reported(session)
    due = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    created = await from_measure(client, s, report, CONFIRMED_MEASURE, due_at=due)
    assert created.status_code == 201
    action = created.json()["data"]
    assert action["state"] == "discovered"
    assert action["origin"] == "measure"
    assert action["title"] == "盤點同年份的變電箱並排定汰換。"
    assert action["owner"] == "總務處"  # 預設為 S08 的建議單位
    assert action["measure"]["cause_basis"] == "confirmed"
    assert action["measure"]["evidence_ids"] == [s.official]
    action_id = action["id"]

    assert (await move(client, action_id, "handling", note="已向總務處反映")).status_code == 200
    assert (
        await move(client, action_id, "recovered", proof="總務處公告：工程一館已恢復供電")
    ).status_code == 200
    done = await move(client, action_id, "improved", proof="同年份變電箱已全數汰換")
    assert done.status_code == 200
    assert done.json()["data"]["state"] == "improved"

    detail = (await client.get(f"/api/v1/admin/actions/{action_id}")).json()["data"]
    assert detail["proof"] == "同年份變電箱已全數汰換"
    history = [(h["from_state"], h["to_state"]) for h in detail["history"]]
    assert history == [
        (None, "discovered"),
        ("discovered", "handling"),
        ("handling", "recovered"),
        ("recovered", "improved"),
    ]
    assert detail["history"][1]["note"] == "已向總務處反映"
    assert detail["history"][2]["proof"] == "總務處公告：工程一館已恢復供電"
    assert all(h["actor"] and h["created_at"] for h in detail["history"])

    actions = (
        await session.scalars(
            select(AuditLog.action)
            .where(AuditLog.action.like("response_action.%"))
            .order_by(AuditLog.created_at)
        )
    ).all()
    assert actions == [
        "response_action.create",
        "response_action.transition",
        "response_action.transition",
        "response_action.transition",
    ]


async def test_transitions_share_request_id_with_audit(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s, report = await reported(session)
    action_id = (await from_measure(client, s, report, CONFIRMED_MEASURE)).json()["data"]["id"]

    await move(client, action_id, "handling")

    row = await session.scalar(
        select(ResponseActionTransition).where(ResponseActionTransition.to_state == "handling")
    )
    audit = await session.scalar(
        select(AuditLog).where(AuditLog.action == "response_action.transition")
    )
    assert row is not None and audit is not None
    assert row.request_id == audit.request_id
    assert audit.target == f"response_action:{action_id}"


# ---------- 從 S08 建立 ----------


async def test_hypothesized_measure_keeps_cause_basis(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s, report = await reported(session)

    response = await from_measure(client, s, report, HYPOTHESIZED_MEASURE)

    assert response.status_code == 201
    measure = response.json()["data"]["measure"]
    assert measure["cause_basis"] == "hypothesized"
    assert measure["kind"] == "investigate"


async def test_student_measure_cannot_be_tracked(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s, report = await reported(session)

    response = await from_measure(client, s, report, STUDENT_MEASURE)

    assert response.status_code == 422
    assert await session.scalar(select(ResponseAction)) is None


async def test_same_measure_cannot_be_tracked_twice(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s, report = await reported(session)
    assert (await from_measure(client, s, report, CONFIRMED_MEASURE)).status_code == 201

    response = await from_measure(client, s, report, CONFIRMED_MEASURE)

    assert response.status_code == 409


async def test_measure_must_exist_in_event_report(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s, report = await reported(session)
    other, _ = await reported(session)

    out_of_range = await from_measure(client, s, report, 99)
    wrong_event = await client.post(
        f"/api/v1/admin/events/{other.event.id}/actions",
        json={"report_id": str(report.id), "measure_index": CONFIRMED_MEASURE},
    )

    assert out_of_range.status_code == 422
    assert wrong_event.status_code == 404


async def test_invalidated_report_cannot_be_tracked(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s, report = await reported(session)
    await invalidate_reports(session, s.event.id, "event_false")
    await session.commit()

    response = await from_measure(client, s, report, CONFIRMED_MEASURE)

    assert response.status_code == 409


# ---------- 手動新增 ----------


async def test_manual_action(client: httpx2.AsyncClient, session: AsyncSession) -> None:
    s = await scene(session)

    response = await client.post(
        f"/api/v1/admin/events/{s.event.id}/actions",
        json={"title": "  請總務處說明停電復電流程  ", "owner": "總務處"},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["origin"] == "manual"
    assert data["title"] == "請總務處說明停電復電流程"
    assert data["measure"] is None
    assert data["report_id"] is None


async def test_manual_action_requires_title(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s = await scene(session)

    response = await client.post(f"/api/v1/admin/events/{s.event.id}/actions", json={})

    assert response.status_code == 422


async def test_refuted_or_archived_event_cannot_get_actions(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    for status in (EventStatus.FALSE, EventStatus.ARCHIVED):
        s = await scene(session, status=status)
        response = await client.post(
            f"/api/v1/admin/events/{s.event.id}/actions", json={"title": "追蹤"}
        )
        assert response.status_code == 409


async def test_unknown_event_or_action(client: httpx2.AsyncClient) -> None:
    missing = uuid.uuid4()

    assert (
        await client.post(f"/api/v1/admin/events/{missing}/actions", json={"title": "追蹤"})
    ).status_code == 404
    assert (await client.get(f"/api/v1/admin/actions/{missing}")).status_code == 404
    assert (await move(client, str(missing), "handling")).status_code == 404


# ---------- 狀態規則 ----------


async def test_illegal_transition_is_conflict(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s = await scene(session)
    action_id = (
        await client.post(f"/api/v1/admin/events/{s.event.id}/actions", json={"title": "追蹤"})
    ).json()["data"]["id"]

    skipped = await move(client, action_id, "improved", proof="已改善")
    same = await move(client, action_id, "discovered")

    assert skipped.status_code == 409
    assert same.status_code == 409
    rows = (await session.scalars(select(ResponseActionTransition))).all()
    assert len(rows) == 1


async def test_recovered_and_improved_require_proof(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s = await scene(session)
    action_id = (
        await client.post(f"/api/v1/admin/events/{s.event.id}/actions", json={"title": "追蹤"})
    ).json()["data"]["id"]
    await move(client, action_id, "handling")

    response = await move(client, action_id, "recovered", proof="   ")

    assert response.status_code == 422
    action = await session.get(ResponseAction, uuid.UUID(action_id))
    assert action is not None
    await session.refresh(action)
    assert action.state == "handling"


async def test_recovered_can_reopen_and_dropped_is_final(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s = await scene(session)
    action_id = (
        await client.post(f"/api/v1/admin/events/{s.event.id}/actions", json={"title": "追蹤"})
    ).json()["data"]["id"]
    await move(client, action_id, "handling")
    await move(client, action_id, "recovered", proof="已恢復")

    reopened = await move(client, action_id, "handling", note="又停電")
    dropped = await move(client, action_id, "dropped", note="改由其他項目追蹤")
    after = await move(client, action_id, "handling")

    assert reopened.status_code == 200
    assert dropped.status_code == 200
    assert after.status_code == 409


def test_transition_table_covers_every_state() -> None:
    from app.resilience.actions import ACTION_TRANSITIONS, PROOF_REQUIRED

    assert set(ACTION_TRANSITIONS) == set(ActionState)
    assert ACTION_TRANSITIONS[ActionState.IMPROVED] == frozenset()
    assert ACTION_TRANSITIONS[ActionState.DROPPED] == frozenset()
    assert {ActionState.RECOVERED, ActionState.IMPROVED} == PROOF_REQUIRED


# ---------- 個資 ----------


async def test_pii_is_rejected(client: httpx2.AsyncClient, session: AsyncSession) -> None:
    s = await scene(session)
    url = f"/api/v1/admin/events/{s.event.id}/actions"

    title = await client.post(url, json={"title": "聯絡 0912345678 確認"})
    owner = await client.post(url, json={"title": "追蹤", "owner": "someone@example.com"})
    assert title.status_code == 422
    assert owner.status_code == 422

    action_id = (await client.post(url, json={"title": "追蹤"})).json()["data"]["id"]
    note = await move(client, action_id, "handling", note="打 0912345678 問")
    assert note.status_code == 422
    await move(client, action_id, "handling")
    proof = await move(client, action_id, "recovered", proof="來信 someone@example.com")
    assert proof.status_code == 422


# ---------- 修改與列表 ----------


async def test_update_owner_and_due_date(client: httpx2.AsyncClient, session: AsyncSession) -> None:
    s, report = await reported(session)
    action_id = (await from_measure(client, s, report, CONFIRMED_MEASURE)).json()["data"]["id"]
    due = datetime(2026, 12, 31, 16, tzinfo=UTC)

    response = await client.patch(
        f"/api/v1/admin/actions/{action_id}", json={"owner": "營繕組", "due_at": due.isoformat()}
    )
    cleared = await client.patch(f"/api/v1/admin/actions/{action_id}", json={"due_at": None})

    assert response.status_code == 200
    assert response.json()["data"]["owner"] == "營繕組"
    assert cleared.json()["data"]["owner"] == "營繕組"
    assert cleared.json()["data"]["due_at"] is None
    audit = await session.scalar(
        select(AuditLog).where(AuditLog.action == "response_action.update")
    )
    assert audit is not None


async def test_list_filters_and_marks_overdue(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    s = await scene(session)
    url = f"/api/v1/admin/events/{s.event.id}/actions"
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    late = (await client.post(url, json={"title": "逾期", "due_at": past})).json()["data"]["id"]
    done = (await client.post(url, json={"title": "已結束", "due_at": past})).json()["data"]["id"]
    await move(client, done, "dropped")

    everything = (await client.get("/api/v1/admin/actions")).json()["data"]
    open_only = (await client.get("/api/v1/admin/actions", params={"state": "discovered"})).json()[
        "data"
    ]
    by_event = (
        await client.get("/api/v1/admin/actions", params={"event_id": str(uuid.uuid4())})
    ).json()["data"]

    flags = {a["id"]: a["overdue"] for a in everything}
    assert flags == {late: True, done: False}  # 已結束的不算逾期
    assert everything[0]["event"] == {"id": str(s.event.id), "title": "工程一館停電"}
    assert [a["id"] for a in open_only] == [late]
    assert by_event == []
