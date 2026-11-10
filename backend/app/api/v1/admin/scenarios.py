"""管理端 What-if 模擬 API（P7-T02，規格 §10、F09）。

- `POST /api/v1/admin/scenarios`：建立模擬。引擎依 baseline × Π(1 − effect) 計算數值，
  S10 scenario-simulator 解釋假設與限制，結果寫入 scenario_runs；回 201。需要 `scenarios:create`，
  寫入稽核 `scenario.create`（metadata 只有引擎版本、狀態、措施數與解釋狀態）。
  效果全部未知時回傳 not_estimable（無法估計），不呼叫模型；S10 失敗時仍保存引擎結果。
  未填 `baseline` 時依時間窗、類別、代表地點自動計算歷史基準（P8-T09），手填值優先；
  沒有歷史資料又未填時回 VALIDATION_ERROR（field body.baseline）。
- `GET /api/v1/admin/scenarios/baseline`：依 `window_days`、`event_type`、`location` 計算歷史
  基準件數與依據，供前端自動帶入。需要 `events:read_all`。
- `GET /api/v1/admin/scenarios`：最近 50 次模擬，可依事件篩選。需要 `events:read_all`。
- `GET /api/v1/admin/scenarios/{id}`：一次模擬。需要 `events:read_all`。

S10 在請求內同步執行（只呼叫一次 Skill，不經 Celery）。只在管理端，不經公開端（ADR-0015）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Self

from fastapi import APIRouter, Depends, Query, status
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.config import create_provider, load_llm_config
from app.ai.runner.loader import SKILLS_DIR, load_skill
from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.config import get_settings
from app.core.security import Permission, Principal
from app.models.ai import AIAnalysisRun
from app.models.scenario import ScenarioRun
from app.resilience.recurrence import TAIPEI
from app.simulations.baseline import load_baseline
from app.simulations.engine import MAX_MEASURES, MAX_SEED, ScenarioInputError
from app.simulations.service import (
    SKILL_NAME,
    Explainer,
    MeasureSpec,
    ScenarioNotFoundError,
    ScenarioSpec,
    create_scenario,
    get_scenario,
    list_scenarios,
)

router = APIRouter(prefix="/scenarios", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]
CanCreate = Annotated[Principal, Depends(require_permission(Permission.SCENARIOS_CREATE))]

Effect = Annotated[float | None, Field(ge=0, le=1)]
WindowDays = Annotated[int, Field(ge=1, le=730)]


def get_explainer() -> Explainer:
    """S10 的相依（依設定檔與環境變數；OpenAI 需要 OPENAI_API_KEY）；測試以 fake provider 替換。"""
    config = load_llm_config()
    return Explainer(
        skill=load_skill(SKILLS_DIR / SKILL_NAME),
        provider=create_provider(config, get_settings()),
        config=config,
    )


class MeasureIn(BaseModel):
    """一項措施；效果上下限（件數降低比例，0–1）都留空代表效果未知。依據不得含個資。"""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    effect_low: Effect = None
    effect_high: Effect = None
    cost: Annotated[int | None, Field(ge=0)] = None
    rationale: Annotated[str | None, Field(max_length=1000)] = None

    @model_validator(mode="after")
    def _effect_pair(self) -> Self:
        if (self.effect_low is None) != (self.effect_high is None):
            raise ValueError("效果上下限要同時填寫或同時留空")
        if (
            self.effect_low is not None
            and self.effect_high is not None
            and self.effect_low > self.effect_high
        ):
            raise ValueError("效果下限不可大於上限")
        return self


class ScenarioIn(BaseModel):
    """模擬參數；未給 seed 時自動產生，未給 baseline 時自動計算歷史基準。"""

    baseline: Annotated[float | None, Field(ge=0, allow_inf_nan=False)] = None
    window_days: WindowDays
    measures: Annotated[list[MeasureIn], Field(min_length=1, max_length=MAX_MEASURES)]
    seed: Annotated[int | None, Field(ge=0, le=MAX_SEED)] = None
    event_id: uuid.UUID | None = None
    event_type: Annotated[str | None, Field(max_length=32)] = None
    location: Annotated[str | None, Field(max_length=100)] = None


class ExplanationOut(BaseModel):
    """S10 的執行結果；`output` 只在通過驗證時有值。"""

    ai_run_id: uuid.UUID
    status: str
    output: dict[str, Any] | None
    abstain_reason: str | None
    error_type: str | None
    skill_version: str


class ScenarioOut(BaseModel):
    """一次模擬。"""

    id: uuid.UUID
    event_id: uuid.UUID | None
    input: dict[str, Any]
    output: dict[str, Any]
    engine_version: str
    seed: int
    explanation: ExplanationOut | None
    created_by: str
    created_at: AwareDatetime


def _out(run: ScenarioRun, ai_run: AIAnalysisRun | None) -> dict[str, Any]:
    explanation = (
        None
        if ai_run is None
        else ExplanationOut(
            ai_run_id=ai_run.id,
            status=ai_run.status,
            output=ai_run.result,
            abstain_reason=ai_run.abstain_reason,
            error_type=ai_run.error_type,
            skill_version=ai_run.skill_version,
        )
    )
    return ScenarioOut(
        id=run.id,
        event_id=run.event_id,
        input=run.input,
        output=run.output,
        engine_version=run.engine_version,
        seed=run.seed,
        explanation=explanation,
        created_by=run.created_by,
        created_at=run.created_at,
    ).model_dump(mode="json")


def _spec(body: ScenarioIn) -> ScenarioSpec:
    return ScenarioSpec(
        baseline=body.baseline,
        window_days=body.window_days,
        measures=[
            MeasureSpec(
                name=m.name,
                effect_low=m.effect_low,
                effect_high=m.effect_high,
                cost=m.cost,
                rationale=m.rationale,
            )
            for m in body.measures
        ],
        seed=body.seed,
        event_id=body.event_id,
        location=body.location,
        event_type=body.event_type,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_scenario_run(
    body: ScenarioIn,
    session: Session,
    principal: CanCreate,
    explainer: Annotated[Explainer, Depends(get_explainer)],
) -> dict[str, Any]:
    """建立模擬；事件不存在回 NOT_FOUND。"""
    try:
        run = await create_scenario(session, principal, _spec(body), explainer)
    except ScenarioNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    except ScenarioInputError as e:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(e),
            details=[{"field": f"body.{e.field}", "message": str(e)}],
        ) from None
    await session.commit()
    run, ai_run = await get_scenario(session, run.id)
    return envelope(_out(run, ai_run))


class BaselineOut(BaseModel):
    """自動計算的基準件數；`baseline` 為 None 代表沒有歷史資料。"""

    baseline: float | None
    observed: int
    weeks: int
    history_start: date | None
    history_end: date | None
    window_days: int
    event_type: str | None
    location: str | None


@router.get("/baseline")
async def get_baseline(
    session: Session,
    _: CanRead,
    window_days: Annotated[int, Query(ge=1, le=730)],
    event_type: Annotated[str | None, Query(max_length=32)] = None,
    location: Annotated[str | None, Query(max_length=100)] = None,
) -> dict[str, Any]:
    """依時間窗、類別、地點計算歷史基準件數（以今天、台北時間為準，進行中的週不列入）。"""
    result = await load_baseline(
        session,
        as_of=datetime.now(TAIPEI).date(),
        window_days=window_days,
        event_type=event_type or None,
        location=location or None,
    )
    out = BaselineOut.model_validate(result, from_attributes=True)
    return envelope(out.model_dump(mode="json"))


@router.get("")
async def list_scenario_runs(
    session: Session,
    _: CanRead,
    event_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    """最近的模擬（新到舊）。"""
    rows = await list_scenarios(session, event_id=event_id)
    return envelope([_out(run, ai_run) for run, ai_run in rows])


@router.get("/{run_id}")
async def get_scenario_run(run_id: uuid.UUID, session: Session, _: CanRead) -> dict[str, Any]:
    """一次模擬；不存在回 NOT_FOUND。"""
    try:
        run, ai_run = await get_scenario(session, run_id)
    except ScenarioNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    return envelope(_out(run, ai_run))
