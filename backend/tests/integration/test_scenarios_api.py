"""P7-T02：What-if 模擬 API（規格 §10 `POST /api/v1/admin/scenarios`、F09、A09）。

- `POST /api/v1/admin/scenarios`：引擎計算 → S10 解釋 → 寫入 scenario_runs 與稽核
  `scenario.create`；
  需要 scenarios:create；未給種子時自動產生並存下，同參數同種子重跑數值相同（G7-1）；
- 效果全部未知時回傳「無法估計」（not_estimable），不呼叫模型、不自造百分比；
- S10 失敗時仍保存引擎結果，解釋狀態為 failed；
- `GET /api/v1/admin/scenarios`、`GET /api/v1/admin/scenarios/{id}`：查詢；需要 events:read_all；
- 只在管理端，未登入回 UNAUTHORIZED，公開端沒有對應路由。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.config import load_llm_config
from app.ai.llm.fake_provider import FakeProvider
from app.ai.runner.loader import SKILLS_DIR, load_skill
from app.api.deps import get_db_session, get_principal
from app.api.v1.admin.scenarios import get_explainer
from app.main import create_app
from app.models.ai import AIAnalysisRun
from app.models.audit import AuditLog
from app.models.event import Event
from app.models.scenario import ScenarioRun
from app.simulations.service import Explainer
from tests.integration.test_pipeline import ADMIN

pytestmark = pytest.mark.anyio

URL = "/api/v1/admin/scenarios"
SKILL = load_skill(SKILLS_DIR / "scenario-simulator")

EXPLANATION: dict[str, Any] = {
    "status": "ok",
    "summary": "若兩項假設都成立，180 天內約 9.6 件（範圍 5.6～14.4 件），這是假設情境。",
    "assumptions": [
        {"measure_id": "m1", "statement": "假設加裝照明能少一到三成。", "evidence_ids": ["m1"]},
        {
            "measure_id": "m2",
            "statement": "假設巡邏能少二到六成，只是估計。",
            "evidence_ids": ["m2"],
        },
    ],
    "key_driver": "m2",
    "limitations": ["hypothetical_not_prediction", "independence_assumed"],
    "evidence_ids": ["m1", "m2"],
    "reason": "結果主要取決於巡邏的假設；這是假設情境，不是成效預測。",
    "abstain_reason": None,
}


def body(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "baseline": 20,
        "window_days": 180,
        "event_type": "facility",
        "location": "工程二館",
        "seed": 42,
        "measures": [
            {
                "name": "加裝照明",
                "effect_low": 0.1,
                "effect_high": 0.3,
                "cost": 30000,
                "rationale": "其他學校經驗。",
            },
            {
                "name": "增加巡邏",
                "effect_low": 0.2,
                "effect_high": 0.6,
                "cost": 400000,
                "rationale": "個人估計。",
            },
        ],
    }
    data.update(changes)
    return data


class Harness:
    def __init__(self, session: AsyncSession) -> None:
        self.provider = FakeProvider([])
        self.app = create_app()

        async def test_session() -> AsyncIterator[AsyncSession]:
            yield session

        self.app.dependency_overrides[get_db_session] = test_session
        self.app.dependency_overrides[get_explainer] = lambda: Explainer(
            skill=SKILL, provider=self.provider, config=load_llm_config()
        )

    def client(self, *, logged_in: bool = True) -> httpx2.AsyncClient:
        if logged_in:
            self.app.dependency_overrides[get_principal] = lambda: ADMIN
        else:
            self.app.dependency_overrides.pop(get_principal, None)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url="https://test"
        )

    def script(self, *responses: Any) -> None:
        self.provider = FakeProvider(list(responses))


@pytest.fixture()
def harness(session: AsyncSession) -> Harness:
    return Harness(session)


async def test_create_runs_engine_and_explanation(harness: Harness, session: AsyncSession) -> None:
    harness.script(EXPLANATION)
    async with harness.client() as c:
        r = await c.post(URL, json=body())
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["engine_version"] == "1.0.0"
    assert data["seed"] == 42
    assert data["output"]["status"] == "estimated"
    assert data["output"]["estimate"]["point"] == pytest.approx(9.6)
    assert [m["measure_id"] for m in data["input"]["measures"]] == ["m1", "m2"]
    explanation = data["explanation"]
    assert explanation["status"] == "succeeded"
    assert explanation["output"]["key_driver"] == "m2"

    # S10 拿到的是引擎算好的數字，措施依據包在 untrusted_source 中
    assert len(harness.provider.calls) == 1
    user = harness.provider.calls[0].messages[1].content
    assert '<untrusted_source id="m1" source="scenario-assumption">' in user
    assert '"point": 9.6' in user

    run = await session.get(ScenarioRun, uuid.UUID(data["id"]))
    assert run is not None and run.ai_run_id is not None
    ai_run = await session.get(AIAnalysisRun, run.ai_run_id)
    assert ai_run is not None and ai_run.skill_name == "scenario-simulator"
    logs = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "scenario.create"))
    ).all()
    assert len(logs) == 1
    assert logs[0].target == f"scenario_run:{run.id}"
    assert logs[0].meta["status"] == "estimated"
    assert "加裝照明" not in str(logs[0].meta)


async def test_same_params_and_seed_reproduce(harness: Harness) -> None:
    harness.script(EXPLANATION, EXPLANATION)
    async with harness.client() as c:
        first = (await c.post(URL, json=body())).json()["data"]
        second = (await c.post(URL, json=body())).json()["data"]
    assert first["id"] != second["id"]
    assert first["output"] == second["output"]


async def test_seed_generated_when_missing(harness: Harness) -> None:
    harness.script(EXPLANATION, EXPLANATION)
    async with harness.client() as c:
        first = (await c.post(URL, json=body(seed=None))).json()["data"]
        again = (await c.post(URL, json=body(seed=first["seed"]))).json()["data"]
    assert isinstance(first["seed"], int)
    assert first["input"]["seed"] == first["seed"]
    assert again["output"] == first["output"]


async def test_all_unknown_is_not_estimable_without_model(harness: Harness) -> None:
    unknown = [
        {"name": "增設監視器", "effect_low": None, "effect_high": None},
        {"name": "加強門禁", "effect_low": None, "effect_high": None, "cost": 50000},
    ]
    async with harness.client() as c:
        r = await c.post(URL, json=body(measures=unknown))
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["output"]["status"] == "not_estimable"
    assert data["output"]["estimate"] is None
    assert data["explanation"] is None
    assert harness.provider.calls == []
    assert "%" not in r.text


async def test_explanation_failure_keeps_engine_result(harness: Harness) -> None:
    async with harness.client() as c:  # FakeProvider 沒有回應 → LLMError → failed
        r = await c.post(URL, json=body())
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["output"]["status"] == "estimated"
    assert data["explanation"]["status"] == "failed"
    assert data["explanation"]["output"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"baseline": -1},
        {"window_days": 0},
        {"measures": []},
        {"measures": [{"name": "x", "effect_low": 0.5, "effect_high": 0.2}]},
        {"measures": [{"name": "x", "effect_low": 0.1, "effect_high": None}]},
        {"measures": [{"name": "x", "effect_low": 0.1, "effect_high": 1.5}]},
        {"measures": [{"name": "", "effect_low": 0.1, "effect_high": 0.2}]},
        {"measures": [{"name": "x", "effect_low": 0.1, "effect_high": 0.2, "cost": -1}]},
        {"seed": -3},
    ],
)
async def test_invalid_input(harness: Harness, changes: dict[str, Any]) -> None:
    async with harness.client() as c:
        r = await c.post(URL, json=body(**changes))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert harness.provider.calls == []


async def test_unknown_event(harness: Harness) -> None:
    async with harness.client() as c:
        r = await c.post(URL, json=body(event_id=str(uuid.uuid4())))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_list_and_get(harness: Harness, session: AsyncSession) -> None:
    event = Event(title="工程二館夜間竊盜", type="security", status="unverified")
    session.add(event)
    await session.commit()
    harness.script(EXPLANATION, EXPLANATION)
    async with harness.client() as c:
        linked = (await c.post(URL, json=body(event_id=str(event.id)))).json()["data"]
        await c.post(URL, json=body())
        all_runs = (await c.get(URL)).json()["data"]
        by_event = (await c.get(URL, params={"event_id": str(event.id)})).json()["data"]
        one = await c.get(f"{URL}/{linked['id']}")
        missing = await c.get(f"{URL}/{uuid.uuid4()}")
    assert len(all_runs) == 2
    assert [r["id"] for r in by_event] == [linked["id"]]
    assert by_event[0]["event_id"] == str(event.id)
    assert one.status_code == 200
    assert one.json()["data"]["explanation"]["output"]["key_driver"] == "m2"
    assert missing.status_code == 404


async def test_admin_only(harness: Harness) -> None:
    async with harness.client(logged_in=False) as c:
        assert (await c.post(URL, json=body())).status_code == 401
        assert (await c.get(URL)).status_code == 401
        assert (await c.get("/api/v1/public/scenarios")).status_code == 404
