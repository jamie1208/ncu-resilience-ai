"""P7-T06：S09 trend-forecaster 管線與管理端 API（fake provider，不呼叫真實 API）。

- `POST /api/v1/admin/trends/forecast`：重新整理 P6-T03 的 views → 組出 series（`load_counts`
  與突增偵測）、coverage（ingestion_runs）、evidence（異常週期的事件標題與摘要，經
  `mask_pii()`）→ 經 Skill Runner 呼叫 S09，結果寫入 ai_analysis_runs，稽核 `trend_forecast.run`；
- 沒有異常、或覆蓋率 low 時不呼叫模型（no_anomaly／insufficient_data）；
- 輸入的數字與 P6-T03 的 `count_series`／`detect_changes` 一致；
- `GET /api/v1/admin/trends/forecast`：最近一次趨勢解讀；
- 只在管理端：未登入回 UNAUTHORIZED，公開端沒有對應路由。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.config import load_llm_config
from app.ai.llm.fake_provider import FakeProvider
from app.ai.runner.loader import SKILLS_DIR, load_skill
from app.analytics.forecast import Forecaster
from app.analytics.timeseries import count_series, detect_changes, load_counts
from app.api.deps import get_db_session, get_principal
from app.api.v1.admin.trends import get_forecaster
from app.main import create_app
from app.models.ai import AIAnalysisRun
from app.models.audit import AuditLog
from app.models.event import Event, Location
from app.models.source import IngestionRun, Source
from tests.integration.test_pipeline import ADMIN

pytestmark = pytest.mark.anyio

URL = "/api/v1/admin/trends/forecast"
SKILL = load_skill(SKILLS_DIR / "trend-forecaster")
AS_OF = date(2026, 11, 1)  # 週日
ADDED = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
FIRST_WEEK = date(2026, 8, 31)  # 來源新增日所在週的週一
SPIKE_WEEK = date(2026, 10, 26)
PHONE = "0912-345-678"


class Harness:
    def __init__(self, session: AsyncSession) -> None:
        self.provider = FakeProvider([])
        self.app = create_app()

        async def test_session() -> AsyncIterator[AsyncSession]:
            yield session

        self.app.dependency_overrides[get_db_session] = test_session
        self.app.dependency_overrides[get_forecaster] = lambda: Forecaster(
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


async def seed_source(session: AsyncSession, *, ingest: bool = True) -> Source:
    """一個核准來源，9/1 新增；`ingest` 時 9/1～11/1 每天都有成功擷取。"""
    source = Source(
        code="ncu-news",
        name="中央大學新聞網",
        type="ncu_news",
        access_policy="approved",
        created_at=ADDED,
    )
    session.add(source)
    await session.flush()
    if ingest:
        day = ADDED + timedelta(hours=2)
        while day.date() <= AS_OF:
            session.add(
                IngestionRun(source_id=source.id, started_at=day, status="succeeded", counts={})
            )
            day += timedelta(days=1)
    await session.flush()
    return source


async def seed_events(session: AsyncSession, *, spike: bool) -> list[Event]:
    """工學院設施事件每週 1 件（8 週）；`spike` 時 10/26 那週 6 件。另有零星天氣事件。"""
    place = Location(name="工學院")
    session.add(place)
    await session.flush()
    for week in range(8):
        session.add(
            Event(
                title=f"例行設施維修 {week}",
                type="facility",
                status="unverified",
                location_id=place.id,
                occurrence_start_date=FIRST_WEEK + timedelta(weeks=week, days=2),
            )
        )
    for week in (1, 4):
        session.add(
            Event(
                title="午後雷陣雨",
                type="weather",
                status="unverified",
                occurrence_start_date=FIRST_WEEK + timedelta(weeks=week, days=3),
            )
        )
    spiked: list[Event] = []
    if spike:
        for n in range(6):
            e = Event(
                title=f"工學院停電 {n}",
                type="facility",
                status="unverified",
                location_id=place.id,
                occurrence_start_date=SPIKE_WEEK + timedelta(days=n % 5),
                summary=f"工學院跳電，聯絡電話 {PHONE}。",
            )
            session.add(e)
            spiked.append(e)
    # 查證為不實的事件不列入
    session.add(
        Event(
            title="謠言",
            type="facility",
            status="false",
            location_id=place.id,
            occurrence_start_date=SPIKE_WEEK,
        )
    )
    await session.flush()
    return spiked


def explanation(evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "status": "ok",
        "explanations": [
            {
                "signal_id": "s1",
                "summary": "同一週有多起停電事件，件數增加可能與電力問題集中有關。",
                "driver": "incident_cluster",
                "basis": "supported",
                "evidence_ids": evidence_ids[:2],
            },
            {
                "signal_id": "s2",
                "summary": "工學院本週停電事件集中，件數因此增加。",
                "driver": "incident_cluster",
                "basis": "supported",
                "evidence_ids": evidence_ids[:2],
            },
        ],
        "outlook": [],
        "coverage_level": "full",
        "caveats": ["volume_not_event_count", "small_counts"],
        "evidence_ids": evidence_ids[:2],
        "reason": "異常週集中在停電事件；回報件數不等於實際發生次數。",
        "abstain_reason": None,
    }


async def test_anomaly_runs_s09_with_p6_numbers(harness: Harness, session: AsyncSession) -> None:
    await seed_source(session)
    spiked = await seed_events(session, spike=True)
    ids = [str(e.id) for e in spiked]
    harness.script(explanation(ids))
    async with harness.client() as c:
        r = await c.post(URL, json={"as_of": AS_OF.isoformat()})
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["outcome"] == "explained"
    assert data["freq"] == "week" and data["as_of"] == "2026-11-01"
    assert data["start"] == FIRST_WEEK.isoformat()
    assert data["coverage"]["level"] == "full"
    assert data["coverage"]["ratio"] == 1.0

    # 數字與 P6-T03 一致
    rows = await load_counts(session, "week", start=FIRST_WEEK, end=AS_OF)
    expected = count_series(rows, "week", FIRST_WEEK, AS_OF, event_type="facility")
    anomaly = detect_changes(expected)[-1]
    s1, s2 = data["series"][0], data["series"][1]
    assert (s1["event_type"], s1["location"]) == ("facility", None)
    assert (s2["event_type"], s2["location"]) == ("facility", "工學院")
    for s in (s1, s2):
        assert s["counts"] == [
            {"period_start": p.period_start.isoformat(), "count": p.count} for p in expected
        ]
        assert s["anomaly"] == {
            "period_start": anomaly.period_start.isoformat(),
            "observed": anomaly.observed,
            "expected": anomaly.expected,
            "p_value": anomaly.p_value,
        }
        assert sorted(s["evidence_ids"]) == sorted(ids)
    assert s1["anomaly"]["observed"] == 6  # 查證為不實的事件不列入
    weather = next(s for s in data["series"] if s["event_type"] == "weather")
    assert weather["anomaly"] is None and weather["evidence_ids"] == []

    # 模型拿到程式算好的數字；事件摘要以 untrusted_source 包裹並遮蔽個資
    assert len(harness.provider.calls) == 1
    user = harness.provider.calls[0].messages[1].content
    assert f'<untrusted_source id="{ids[0]}" source="unknown">' in user
    assert '"observed": 6' in user
    assert PHONE not in user
    assert PHONE not in r.text

    forecast = data["forecast"]
    assert forecast["status"] == "succeeded"
    assert forecast["output"]["explanations"][0]["signal_id"] == "s1"
    ai_run = await session.get(AIAnalysisRun, uuid.UUID(forecast["ai_run_id"]))
    assert ai_run is not None and ai_run.skill_name == "trend-forecaster"
    assert ai_run.event_id is None

    logs = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "trend_forecast.run"))
    ).all()
    assert len(logs) == 1
    assert logs[0].meta["outcome"] == "explained"
    assert logs[0].meta["ai_run_id"] == forecast["ai_run_id"]
    assert "停電" not in str(logs[0].meta)

    # 查詢最近一次：同樣的輸入數字與解讀
    async with harness.client() as c:
        latest = (await c.get(URL)).json()["data"]
    assert latest["outcome"] == "explained"
    assert latest["series"] == data["series"]
    assert latest["coverage"] == data["coverage"]
    assert latest["forecast"]["ai_run_id"] == forecast["ai_run_id"]


async def test_no_anomaly_skips_model(harness: Harness, session: AsyncSession) -> None:
    await seed_source(session)
    await seed_events(session, spike=False)
    async with harness.client() as c:
        r = await c.post(URL, json={"as_of": AS_OF.isoformat()})
        latest = (await c.get(URL)).json()["data"]
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["outcome"] == "no_anomaly"
    assert data["forecast"] is None
    assert all(s["anomaly"] is None for s in data["series"])
    assert data["series"]  # 仍列出件數序列供參考
    assert harness.provider.calls == []
    assert await session.scalar(select(func.count()).select_from(AIAnalysisRun)) == 0
    assert latest["outcome"] == "no_anomaly"


async def test_low_coverage_skips_model(harness: Harness, session: AsyncSession) -> None:
    await seed_source(session, ingest=False)
    await seed_events(session, spike=True)
    async with harness.client() as c:
        r = await c.post(URL, json={"as_of": AS_OF.isoformat()})
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["outcome"] == "insufficient_data"
    assert data["coverage"]["level"] == "low"
    assert data["coverage"]["note"]
    assert data["forecast"] is None
    assert harness.provider.calls == []


async def test_model_failure_is_recorded(harness: Harness, session: AsyncSession) -> None:
    await seed_source(session)
    await seed_events(session, spike=True)
    async with harness.client() as c:  # FakeProvider 沒有回應 → LLMError → failed
        r = await c.post(URL, json={"as_of": AS_OF.isoformat()})
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["outcome"] == "failed"
    assert data["forecast"]["status"] == "failed"
    assert data["forecast"]["output"] is None
    assert data["series"][0]["anomaly"] is not None


async def test_latest_is_null_before_any_run(harness: Harness) -> None:
    async with harness.client() as c:
        r = await c.get(URL)
    assert r.status_code == 200, r.text
    assert r.json()["data"] is None


async def test_rejects_future_as_of(harness: Harness) -> None:
    async with harness.client() as c:
        r = await c.post(URL, json={"as_of": "2999-01-01"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert harness.provider.calls == []


async def test_requires_login(harness: Harness) -> None:
    async with harness.client(logged_in=False) as c:
        posted = await c.post(URL, json={})
        got = await c.get(URL)
    assert posted.status_code == 401
    assert got.status_code == 401


async def test_not_exposed_publicly(harness: Harness) -> None:
    async with harness.client(logged_in=False) as c:
        for path in ("/api/v1/public/trends/forecast", "/api/v1/public/trends"):
            assert (await c.get(path)).status_code == 404
    spec = harness.app.openapi()["paths"]
    trend = [p for p in spec if "trend" in p]
    assert trend
    assert all(p.startswith("/api/v1/admin/") for p in trend)
