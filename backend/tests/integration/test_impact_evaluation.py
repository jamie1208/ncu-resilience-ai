"""P8-T15：依改善建議評估效益（S14 impact-evaluator，ADR-0019、ADR-0020 決策 6）。

- 輸入：系列最新一次成功分析（S12）的原因（推測／已確認）與全部改善建議（標示選了哪些）、
  子事件摘要、系列件數基準與改善追蹤進度；建議、原因與事件內容包在 `<untrusted_source>`；
- 沒有勾選時不呼叫模型；勾選不存在的建議被拒；
- 業務驗證（不通過就依 Skill Runner 規則重試，仍失敗記為 invalid「這次評估不出來」）：
  `0 ≤ low ≤ high ≤ improvement_cap`、引用的原因與建議存在、補強建議不得是已選的建議、
  推測原因不得寫成已確定；
- 件數由程式以 基準 ×（1 − 百分比）換算，件數少時不顯示；
- 同一次分析、同一組選擇沿用既有結果（不重複呼叫）；系列分析更新後舊評估標示過期；
- 只在 `/api/v1/admin/*`：`POST` 需要 `scenarios:create`，稽核 `scenario.impact`；未登入被拒。
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

from app.ai.llm.base import LLMError
from app.ai.llm.config import load_llm_config
from app.ai.llm.fake_provider import FakeProvider
from app.ai.runner.loader import SKILLS_DIR, load_skill
from app.analytics.timeseries import period_start
from app.api.deps import get_db_session, get_principal
from app.api.v1.admin.scenarios import get_evaluator
from app.core.security import Principal, Role
from app.main import create_app
from app.models import AuditLog
from app.models.ai import AIAnalysisRun
from app.models.event import Event
from app.models.impact import ImpactEvaluation
from app.models.response_action import ResponseAction
from app.models.series import EventSeries, EventSeriesMember, SeriesAnalysis, SeriesCauseDecision
from app.resilience.recurrence import TAIPEI
from app.simulations.impact import (
    S14,
    Evaluator,
    ImpactInputError,
    evaluate_impact,
    load_impact_config,
    series_baseline,
)

pytestmark = pytest.mark.anyio

URL = "/api/v1/admin/scenarios/impact"
ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
SKILL = load_skill(SKILLS_DIR / S14)
AS_OF = datetime.now(TAIPEI).date()
THIS_WEEK = period_start(AS_OF, "week")


# ---------- 資料 ----------


async def add_series(
    session: AsyncSession, days: list[date], *, category: str = "traffic"
) -> tuple[EventSeries, list[Event]]:
    series = EventSeries(category=category, title="停放機車遭碰撞")
    session.add(series)
    await session.flush()
    events = []
    for i, day in enumerate(days):
        event = Event(
            title=f"機車被撞 {i + 1}",
            type=category,
            status="unverified",
            summary="工程五館外路邊停放的機車晚間被碰撞。",
            occurrence_start_date=day,
        )
        session.add(event)
        await session.flush()
        session.add(
            EventSeriesMember(
                event_id=event.id,
                series_id=series.id,
                method="ai",
                joined_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(minutes=i),
            )
        )
        events.append(event)
    await session.flush()
    return series, events


def cause(cause_id: str, description: str, events: list[Event]) -> dict[str, Any]:
    return {
        "cause_id": cause_id,
        "description": description,
        "label": "推測，尚未查證",
        "to_verify": "向總務處確認。",
        "event_ids": [str(e.id) for e in events],
        "event_count": len(events),
        "total": len(events),
        "quotes": [],
        "evidence_ids": [],
        "mentions_party": False,
    }


def measure(action: str, cause_id: str | None, kind: str = "prevent") -> dict[str, Any]:
    return {
        "action": action,
        "audience": "school",
        "horizon": "mid",
        "priority": "high",
        "kind": kind,
        "cause_id": cause_id,
        "responsible_unit": "總務處",
        "evidence_ids": [],
    }


async def add_analysis(
    session: AsyncSession,
    series: EventSeries,
    events: list[Event],
    *,
    created_at: datetime | None = None,
) -> SeriesAnalysis:
    analysis = SeriesAnalysis(
        series_id=series.id,
        status="ok",
        pattern_status="abstained",
        title=series.title,
        summary="這個系列持續追蹤停放機車被碰撞的狀況。",
        events=[{"code": f"E{i + 1}", "event_id": str(e.id)} for i, e in enumerate(events)],
        causes=[
            cause("c1", "可能是路邊停車格太靠近車道。", events),
            cause("c2", "疑似夜間照明不足。", events[:1]),
        ],
        measures=[
            measure("把停車格往內移，和車道拉開距離。", "c1"),
            measure("查看停車區與車道的距離是否足夠。", "c1", "investigate"),
            measure("在路段加裝路燈。", "c2"),
        ],
        trace_id="t",
        created_at=created_at or datetime.now(UTC) - timedelta(hours=1),
    )
    session.add(analysis)
    await session.flush()
    return analysis


SIX_WEEKS = [THIS_WEEK - timedelta(weeks=w, days=-1) for w in range(1, 7)]  # 過去 6 個完整週各 1 件


def output(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "ok",
        "improvement": {"low": 20, "high": 40},
        "before": "工程五館外的機車晚上常被擦撞，可能跟停車格太靠近車道有關。",
        "after": "停車格往內移之後，如果原因真的是車道太窄，擦撞可望少一些。",
        "covered_causes": ["c1"],
        "uncovered_causes": ["c2"],
        "overlaps": [],
        "risks": ["原因還是推測，效果可能比估計低。"],
        "suggestion": {"measure_id": "m3", "reason": "照明不足沒有被選的建議處理到。"},
        "evidence_ids": ["E1", "c1", "m1"],
        "reason": "所選建議處理主要的推測原因，但需要施工時間。",
        "abstain_reason": None,
    }
    base.update(overrides)
    return base


ABSTAIN = output(
    status="abstain",
    improvement={"low": None, "high": None},
    before=None,
    after=None,
    covered_causes=[],
    uncovered_causes=[],
    overlaps=[],
    risks=["子事件看不出是什麼問題。"],
    suggestion={"measure_id": None, "reason": None},
    evidence_ids=[],
    abstain_reason="insufficient_evidence",
)


class Harness:
    def __init__(self, session: AsyncSession) -> None:
        self.provider = FakeProvider([])
        self.app = create_app()

        async def test_session() -> AsyncIterator[AsyncSession]:
            yield session

        self.app.dependency_overrides[get_db_session] = test_session
        self.app.dependency_overrides[get_evaluator] = lambda: self.evaluator()

    def evaluator(self) -> Evaluator:
        return Evaluator(skill=SKILL, provider=self.provider, config=load_llm_config())

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


async def _post(harness: Harness, series: EventSeries, ids: list[str], **extra: Any) -> Any:
    async with harness.client() as c:
        return await c.post(URL, json={"series_id": str(series.id), "measure_ids": ids, **extra})


# ---------- 設定與基準 ----------


def test_config_defaults() -> None:
    config = load_impact_config()
    assert config.improvement_cap == 80
    assert config.window_days >= 1
    assert config.min_observed >= 1


def test_series_baseline_uses_complete_weeks_only() -> None:
    # 過去 6 個完整週各 1 件、本週 1 件（進行中的週不列入）
    days = [*SIX_WEEKS, AS_OF]
    result = series_baseline(days, as_of=AS_OF, window_days=70)
    assert result.weeks == 6
    assert result.observed == 6
    assert result.baseline == pytest.approx(10.0)  # 6 ÷ 6 週 × 70 ÷ 7
    # 只有本週的資料：沒有基準
    assert series_baseline([AS_OF], as_of=AS_OF, window_days=70).baseline is None


# ---------- 評估 ----------


async def test_evaluate_ok_saves_result_and_converts_counts(
    harness: Harness, session: AsyncSession
) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    analysis = await add_analysis(session, series, events)
    session.add(
        SeriesCauseDecision(
            analysis_id=analysis.id,
            cause_id="c1",
            decision="confirmed",
            basis="現場確認",
            decided_by="u-1",
        )
    )
    session.add(
        ResponseAction(
            series_id=series.id,
            analysis_id=analysis.id,
            origin="series_measure",
            measure_index=0,
            title="把停車格往內移",
            state="handling",
        )
    )
    await session.commit()
    overlap = {"measure_ids": ["m2", "m1"], "note": "兩項都處理停車格與車道的距離。"}
    harness.script(output(overlaps=[overlap]))

    r = await _post(harness, series, ["m1", "m2"])
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["status"] == "ok"
    assert data["selected"] == ["m1", "m2"]
    assert data["analysis_id"] == str(analysis.id)
    assert data["improvement"] == {"low": 20, "high": 40}
    assert data["cap"] == 80
    assert data["stale"] is False and data["reused"] is False
    result = data["result"]
    assert result["before"].startswith("工程五館外")
    assert [c["cause_id"] for c in result["covered_causes"]] == ["c1"]
    assert result["covered_causes"][0]["decision"] == "confirmed"
    assert result["uncovered_causes"][0]["description"] == "疑似夜間照明不足。"
    assert result["suggestion"]["measure_id"] == "m3"
    assert result["suggestion"]["action"] == "在路段加裝路燈。"
    assert result["overlaps"][0]["measure_ids"] == ["m1", "m2"]

    # 件數由程式換算：基準 ×（1 − 百分比）
    counts = data["counts"]
    window = load_impact_config().window_days
    baseline = round(6 / 6 * window / 7, 1)
    assert counts["baseline"] == pytest.approx(baseline)
    assert counts["shown"] is True
    assert counts["after_low"] == pytest.approx(round(baseline * 0.6, 1))
    assert counts["after_high"] == pytest.approx(round(baseline * 0.8, 1))

    # S14 的輸入：選擇、原因狀態、追蹤進度；建議與事件內容包在 untrusted_source
    assert len(harness.provider.calls) == 1
    user = harness.provider.calls[0].messages[1].content
    assert '<untrusted_source id="m1" source="series-measure">' in user
    assert '<untrusted_source id="c2" source="series-cause">' in user
    assert '<untrusted_source id="E1" source="series-event">' in user
    assert '"selected": true' in user and '"tracking_state": "handling"' in user
    assert '"status": "confirmed"' in user

    row = await session.get(ImpactEvaluation, uuid.UUID(data["id"]))
    assert row is not None and row.ai_run_id is not None
    ai_run = await session.get(AIAnalysisRun, row.ai_run_id)
    assert ai_run is not None and ai_run.skill_name == S14
    logs = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "scenario.impact"))
    ).all()
    assert len(logs) == 1
    assert logs[0].target == f"impact_evaluation:{row.id}"
    assert "工程五館" not in str(logs[0].meta)


async def test_no_selection_does_not_call_model(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    harness.script(output())
    r = await _post(harness, series, [])
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert harness.provider.calls == []
    # 服務層同樣拒絕，不呼叫模型
    with pytest.raises(ImpactInputError):
        await evaluate_impact(session, ADMIN, series.id, [], harness.evaluator())
    assert harness.provider.calls == []
    assert await session.scalar(select(func.count()).select_from(ImpactEvaluation)) == 0


async def test_unknown_measure_is_rejected(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    r = await _post(harness, series, ["m1", "m9"])
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["field"] == "body.measure_ids"
    assert harness.provider.calls == []


async def test_series_without_analysis(harness: Harness, session: AsyncSession) -> None:
    series, _ = await add_series(session, SIX_WEEKS)
    await session.commit()
    r = await _post(harness, series, ["m1"])
    assert r.status_code == 409
    async with harness.client() as c:
        missing = await c.post(URL, json={"series_id": str(uuid.uuid4()), "measure_ids": ["m1"]})
    assert missing.status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(
            {"covered_causes": ["c1", "c4"], "uncovered_causes": ["c2"]}, id="unknown_cause"
        ),
        pytest.param({"covered_causes": ["c1"], "uncovered_causes": []}, id="cause_missing"),
        pytest.param(
            {"suggestion": {"measure_id": "m1", "reason": "再做一次"}}, id="selected_suggestion"
        ),
        pytest.param(
            {"suggestion": {"measure_id": "m6", "reason": "不存在"}}, id="unknown_suggestion"
        ),
        pytest.param(
            {"overlaps": [{"measure_ids": ["m1", "m3"], "note": "x"}]}, id="overlap_unselected"
        ),
        pytest.param({"improvement": {"low": 30, "high": 90}}, id="over_cap"),
        pytest.param({"improvement": {"low": 50, "high": 20}}, id="low_gt_high"),
        pytest.param({"improvement": {"low": None, "high": None}}, id="ok_without_range"),
        pytest.param({"risks": []}, id="no_risks"),
        pytest.param({"after": "原因就是停車格太靠近車道，改了就好。"}, id="hypothesis_asserted"),
    ],
)
async def test_invalid_output_is_retried_then_rejected(
    harness: Harness, session: AsyncSession, bad: dict[str, Any]
) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    harness.script(output(**bad), output(**bad), output(**bad))
    r = await _post(harness, series, ["m1", "m2"])
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["status"] == "invalid"
    assert data["improvement"] is None and data["result"] is None
    assert data["counts"]["after_low"] is None
    assert len(harness.provider.calls) == 3  # 依 Skill Runner 規則重試


async def test_retry_then_valid(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    harness.script(output(improvement={"low": 10, "high": 95}), output())
    r = await _post(harness, series, ["m1", "m2"])
    assert r.json()["data"]["status"] == "ok"
    assert len(harness.provider.calls) == 2


async def test_cap_comes_from_config(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    over = output(improvement={"low": 60, "high": 70})
    harness.script(over, over, over)
    config = load_impact_config().model_copy(update={"improvement_cap": 50})
    row = (
        await evaluate_impact(session, ADMIN, series.id, ["m1"], harness.evaluator(), config=config)
    ).evaluation
    assert row.status == "invalid"
    assert row.cap == 50


async def test_counts_hidden_when_history_is_short(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS[:2])
    await add_analysis(session, series, events)
    await session.commit()
    harness.script(output())
    data = (await _post(harness, series, ["m1"])).json()["data"]
    assert data["status"] == "ok"
    assert data["counts"]["shown"] is False
    assert data["counts"]["after_low"] is None and data["counts"]["after_high"] is None


async def test_model_abstain(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    harness.script(ABSTAIN)
    data = (await _post(harness, series, ["m1"])).json()["data"]
    assert data["status"] == "abstained"
    assert data["abstain_reason"] == "insufficient_evidence"
    assert data["improvement"] is None
    assert data["counts"]["after_low"] is None


async def test_provider_failure(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    harness.script(LLMError("bad request"))
    data = (await _post(harness, series, ["m1"])).json()["data"]
    assert data["status"] == "failed"
    assert data["result"] is None


async def test_same_selection_is_reused(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    harness.script(output())
    first = (await _post(harness, series, ["m2", "m1"])).json()["data"]
    second = (await _post(harness, series, ["m1", "m2", "m1"])).json()["data"]
    assert second["id"] == first["id"]
    assert second["reused"] is True
    assert len(harness.provider.calls) == 1
    assert await session.scalar(select(func.count()).select_from(ImpactEvaluation)) == 1
    logs = await session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "scenario.impact")
    )
    assert logs == 1


async def test_failed_evaluation_is_not_reused(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    harness.script(LLMError("bad request"), output())
    assert (await _post(harness, series, ["m1"])).json()["data"]["status"] == "failed"
    again = (await _post(harness, series, ["m1"])).json()["data"]
    assert again["status"] == "ok" and again["reused"] is False


async def test_new_analysis_marks_old_evaluations_stale(
    harness: Harness, session: AsyncSession
) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    old = await add_analysis(session, series, events)
    await session.commit()
    harness.script(output(), output())
    first = (await _post(harness, series, ["m1"])).json()["data"]
    assert first["stale"] is False

    await add_analysis(session, series, events, created_at=datetime.now(UTC))
    await session.commit()
    async with harness.client() as c:
        listed = (await c.get(URL, params={"series_id": str(series.id)})).json()["data"]
        detail = (await c.get(f"{URL}/{first['id']}")).json()["data"]
        stale_post = await c.post(
            URL,
            json={"series_id": str(series.id), "measure_ids": ["m1"], "analysis_id": str(old.id)},
        )
    assert [e["id"] for e in listed] == [first["id"]]
    assert listed[0]["stale"] is True and detail["stale"] is True
    assert stale_post.status_code == 409

    # 同一組選擇在新分析下重新評估，不沿用過期的結果
    fresh = (await _post(harness, series, ["m1"])).json()["data"]
    assert fresh["id"] != first["id"] and fresh["stale"] is False
    assert len(harness.provider.calls) == 2


async def test_list_and_detail(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    other, other_events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, other, other_events)
    await session.commit()
    harness.script(output(), output())
    mine = (await _post(harness, series, ["m1"])).json()["data"]
    await _post(harness, other, ["m1"])
    async with harness.client() as c:
        listed = (await c.get(URL, params={"series_id": str(series.id)})).json()["data"]
        missing = await c.get(f"{URL}/{uuid.uuid4()}")
    assert [e["id"] for e in listed] == [mine["id"]]
    assert missing.status_code == 404


async def test_requires_login(harness: Harness, session: AsyncSession) -> None:
    series, events = await add_series(session, SIX_WEEKS)
    await add_analysis(session, series, events)
    await session.commit()
    async with harness.client(logged_in=False) as c:
        post = await c.post(URL, json={"series_id": str(series.id), "measure_ids": ["m1"]})
        get = await c.get(URL)
    assert post.status_code == 401 and get.status_code == 401
    assert harness.provider.calls == []
