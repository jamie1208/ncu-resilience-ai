"""建立與查詢 What-if 模擬（P7-T02，規格 §8.3、F09）。

流程：引擎計算（`engine.simulate`）→ S10 scenario-simulator 解釋假設與限制 → 寫入
`scenario_runs`（輸入、引擎結果、引擎版本、隨機種子；S10 的執行紀錄在 `ai_analysis_runs`，
以 `ai_run_id` 連結）→ 稽核 `scenario.create`。

- 措施代號依輸入順序指定為 m1…m10，S10 只能引用這些代號；
- 引擎回傳 not_estimable（效果全部未知）時不呼叫模型，直接記錄「無法估計」；
- 未指定種子時隨機產生一個並存下，之後以同參數同種子重跑可得到相同數值（G7-1）；
- 未填基準件數時依時間窗、類別、地點自動計算歷史基準（P8-T09，`baseline.load_baseline`），
  手填值優先；輸入紀錄的 `baseline_source` 記 manual／auto，auto 時 `baseline_basis` 記依據；
- 只在管理端使用，不經公開端（ADR-0015）。
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.ai.llm.config import LLMConfig
from app.ai.runner.loader import Skill
from app.ai.runner.run import SkillRunResult, SqlRunStore, run_skill
from app.core.security import Principal
from app.models.ai import AIAnalysisRun
from app.models.event import Event
from app.models.scenario import ScenarioRun
from app.resilience.recurrence import TAIPEI
from app.services.audit import record_audit
from app.simulations.baseline import Baseline, load_baseline
from app.simulations.engine import (
    MAX_SEED,
    EffectRange,
    Measure,
    ScenarioInput,
    ScenarioInputError,
    SimulationResult,
    SimulationStatus,
    simulate,
)

SKILL_NAME = "scenario-simulator"
SOURCE_CODE = "scenario-assumption"
NO_RATIONALE = "未提供假設依據"
LIST_LIMIT = 50


class ScenarioNotFoundError(LookupError):
    """模擬或指定的事件不存在。"""


@dataclass(frozen=True)
class MeasureSpec:
    """使用者輸入的一項措施；效果上下限任一為 None 代表效果未知。"""

    name: str
    effect_low: float | None
    effect_high: float | None
    cost: int | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class ScenarioSpec:
    """使用者輸入的模擬參數；`baseline` 為 None 時自動計算歷史基準。"""

    baseline: float | None
    window_days: int
    measures: Sequence[MeasureSpec]
    seed: int | None = None
    event_id: uuid.UUID | None = None
    location: str | None = None
    event_type: str | None = None


@dataclass(frozen=True)
class Explainer:
    """呼叫 S10 需要的相依。"""

    skill: Skill
    provider: LLMProvider
    config: LLMConfig


def measure_id(index: int) -> str:
    """第 index 項（從 0 起）措施的代號。"""
    return f"m{index + 1}"


def to_engine_input(spec: ScenarioSpec, seed: int) -> ScenarioInput:
    """把使用者輸入轉成引擎輸入；`spec.baseline` 必須已有值。"""
    if spec.baseline is None:
        raise ScenarioInputError("baseline", "基準件數尚未決定")
    measures = tuple(
        Measure(
            measure_id=measure_id(i),
            name=m.name,
            effect=None
            if m.effect_low is None or m.effect_high is None
            else EffectRange(m.effect_low, m.effect_high),
            cost=m.cost,
        )
        for i, m in enumerate(spec.measures)
    )
    return ScenarioInput(
        baseline=spec.baseline,
        window_days=spec.window_days,
        measures=measures,
        seed=seed,
        location=spec.location,
        event_type=spec.event_type,
    )


def skill_payload(
    scenario: ScenarioInput, result: SimulationResult, rationales: Sequence[str | None]
) -> dict[str, Any]:
    """S10 的輸入：情境與引擎結果在 `<skill_input>`，措施名稱與依據為不可信的 evidence。"""
    engine = result.to_dict()
    by_id = {row["measure_id"]: row for row in engine["measures"]}
    engine["measures"] = [
        {
            **by_id[m.measure_id],
            "effect_low": None if m.effect is None else m.effect.low,
            "effect_high": None if m.effect is None else m.effect.high,
        }
        for m in scenario.measures
    ]
    del engine["baseline"], engine["total_cost"]
    return {
        "evidence": [
            {
                "evidence_id": m.measure_id,
                "source_code": SOURCE_CODE,
                "title": m.name,
                "text": (rationales[i] or "").strip() or NO_RATIONALE,
            }
            for i, m in enumerate(scenario.measures)
        ],
        "scenario": {
            "baseline": scenario.baseline,
            "window_days": scenario.window_days,
            "event_type": scenario.event_type,
            "location": scenario.location,
        },
        "engine": engine,
    }


def _input_record(spec: ScenarioSpec, seed: int, auto: Baseline | None) -> dict[str, Any]:
    return {
        "baseline": spec.baseline,
        "baseline_source": "manual" if auto is None else "auto",
        "baseline_basis": None if auto is None else auto.basis(),
        "window_days": spec.window_days,
        "event_type": spec.event_type,
        "location": spec.location,
        "seed": seed,
        "measures": [
            {
                "measure_id": measure_id(i),
                "name": m.name,
                "effect_low": m.effect_low,
                "effect_high": m.effect_high,
                "cost": m.cost,
                "rationale": m.rationale,
            }
            for i, m in enumerate(spec.measures)
        ],
    }


async def create_scenario(
    session: AsyncSession,
    principal: Principal,
    spec: ScenarioSpec,
    explainer: Explainer,
    *,
    trace_id: str | None = None,
    as_of: date | None = None,
) -> ScenarioRun:
    """計算、解釋並記錄一次模擬（flush，不 commit）。

    未填基準時以 `as_of`（預設今天，台北時間）之前的歷史自動計算；沒有歷史資料時拋出
    `engine.ScenarioInputError`（field baseline）。輸入不合法時拋出 `engine.ScenarioInputError`；
    事件不存在拋出 `ScenarioNotFoundError`。
    """
    if spec.event_id is not None and await session.get(Event, spec.event_id) is None:
        raise ScenarioNotFoundError("事件不存在")
    auto: Baseline | None = None
    if spec.baseline is None:
        auto = await load_baseline(
            session,
            as_of=as_of or datetime.now(TAIPEI).date(),
            window_days=spec.window_days,
            event_type=spec.event_type,
            location=spec.location,
        )
        if auto.baseline is None:
            raise ScenarioInputError("baseline", "沒有歷史資料可以推算基準件數，請手動填寫")
        spec = replace(spec, baseline=auto.baseline)
    seed = spec.seed if spec.seed is not None else secrets.randbelow(MAX_SEED + 1)
    scenario = to_engine_input(spec, seed)
    result = simulate(scenario)

    explained: SkillRunResult | None = None
    if result.status is SimulationStatus.ESTIMATED:
        explained = await run_skill(
            explainer.skill,
            skill_payload(scenario, result, [m.rationale for m in spec.measures]),
            provider=explainer.provider,
            config=explainer.config,
            store=SqlRunStore(session),
            event_id=spec.event_id,
            trace_id=trace_id,
        )

    run = ScenarioRun(
        event_id=spec.event_id,
        input=_input_record(spec, seed, auto),
        output=result.to_dict(),
        engine_version=result.engine_version,
        seed=seed,
        ai_run_id=None if explained is None else explained.run_id,
        created_by=principal.actor,
    )
    session.add(run)
    await session.flush()
    await record_audit(
        session,
        principal,
        "scenario.create",
        target=f"scenario_run:{run.id}",
        metadata={
            "engine_version": result.engine_version,
            "status": result.status.value,
            "measures": len(spec.measures),
            "explanation": None if explained is None else explained.status.value,
            "baseline_source": "manual" if auto is None else "auto",
        },
    )
    return run


async def get_scenario(
    session: AsyncSession, run_id: uuid.UUID
) -> tuple[ScenarioRun, AIAnalysisRun | None]:
    """一次模擬與它的 S10 執行紀錄；不存在拋出 `ScenarioNotFoundError`。"""
    run = await session.get(ScenarioRun, run_id)
    if run is None:
        raise ScenarioNotFoundError("模擬不存在")
    ai_run = None if run.ai_run_id is None else await session.get(AIAnalysisRun, run.ai_run_id)
    return run, ai_run


async def list_scenarios(
    session: AsyncSession, *, event_id: uuid.UUID | None = None
) -> list[tuple[ScenarioRun, AIAnalysisRun | None]]:
    """最近的模擬（最多 50 筆，新到舊）與各自的 S10 執行紀錄。"""
    stmt = select(ScenarioRun).order_by(ScenarioRun.created_at.desc(), ScenarioRun.id)
    if event_id is not None:
        stmt = stmt.where(ScenarioRun.event_id == event_id)
    runs = list((await session.scalars(stmt.limit(LIST_LIMIT))).all())
    ai_ids = [r.ai_run_id for r in runs if r.ai_run_id is not None]
    ai_runs: dict[uuid.UUID, AIAnalysisRun] = {}
    if ai_ids:
        found = await session.scalars(select(AIAnalysisRun).where(AIAnalysisRun.id.in_(ai_ids)))
        ai_runs = {a.id: a for a in found}
    return [(r, ai_runs.get(r.ai_run_id) if r.ai_run_id else None) for r in runs]
