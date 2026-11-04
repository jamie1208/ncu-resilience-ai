"""管理端趨勢解讀 API（P7-T06，S09 trend-forecaster）。

- `GET /api/v1/admin/trends/forecast`：最近一次趨勢解讀（輸入的序列、覆蓋率、事件與 S09 結果）；
  從未執行時 data 為 null。需要 `events:read_all`。
- `POST /api/v1/admin/trends/forecast`：手動觸發一次，回 201。需要 `events:analyze`，
  寫入稽核 `trend_forecast.run`（metadata 只有頻率、截止日、結果、訊號數與覆蓋率）。
  截止日預設台北昨日，不得晚於今日。沒有異常或覆蓋率 low 時不呼叫模型。

只手動觸發（使用者 2026-11-04 決定不排程），S09 在請求內同步執行。只在管理端，
不經公開端（ADR-0015）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import AwareDatetime, BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.config import create_provider, load_llm_config
from app.ai.runner.loader import SKILLS_DIR, load_skill
from app.analytics.coverage import TAIPEI
from app.analytics.forecast import (
    SKILL_NAME,
    Forecaster,
    TrendForecast,
    default_as_of,
    latest_forecast,
    run_forecast,
)
from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.config import get_settings
from app.core.security import Permission, Principal

router = APIRouter(prefix="/trends", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]
CanRun = Annotated[Principal, Depends(require_permission(Permission.EVENTS_ANALYZE))]


def get_forecaster() -> Forecaster:
    """S09 的相依（依設定檔與環境變數；OpenAI 需要 OPENAI_API_KEY）；測試以 fake provider 替換。"""
    config = load_llm_config()
    return Forecaster(
        skill=load_skill(SKILLS_DIR / SKILL_NAME),
        provider=create_provider(config, get_settings()),
        config=config,
    )


class ForecastIn(BaseModel):
    """觸發參數；截止日預設台北昨日。"""

    freq: Literal["week", "month"] = "week"
    as_of: date | None = None


class SourceCoverageOut(BaseModel):
    """單一來源的覆蓋情形。"""

    code: str
    expected_days: int
    ingested_days: int
    missing_days: int
    added_on: date | None


class CoverageOut(BaseModel):
    """統計期間的資料覆蓋率。"""

    start: date
    end: date
    level: str
    ratio: float | None
    note: str | None
    sources: list[SourceCoverageOut]


class EvidenceOut(BaseModel):
    """異常週期中的事件（只列標題，經個資遮蔽）。"""

    evidence_id: str
    event_id: str
    title: str
    source_code: str


class ExplanationOut(BaseModel):
    """S09 的執行結果；`output` 只在通過驗證時有值。"""

    ai_run_id: str
    status: str
    output: dict[str, Any] | None
    abstain_reason: str | None
    error_type: str | None
    skill_version: str


class ForecastOut(BaseModel):
    """一次趨勢解讀。"""

    outcome: str
    freq: str
    as_of: date
    start: date
    series: list[dict[str, Any]]
    coverage: CoverageOut
    evidence: list[EvidenceOut]
    forecast: ExplanationOut | None
    created_by: str
    created_at: AwareDatetime


def _out(f: TrendForecast) -> dict[str, Any]:
    data, cov, run = f.input, f.input.coverage, f.ai_run
    return ForecastOut(
        outcome=f.outcome.value,
        freq=data.freq,
        as_of=data.as_of,
        start=data.start,
        series=[s.to_input() for s in data.signals],
        coverage=CoverageOut(
            start=cov.start,
            end=cov.end,
            level=cov.level,
            ratio=cov.ratio,
            note=cov.note,
            sources=[
                SourceCoverageOut(
                    code=s.code,
                    expected_days=s.expected_days,
                    ingested_days=s.ingested_days,
                    missing_days=len(s.missing),
                    added_on=s.added_on,
                )
                for s in cov.sources
            ],
        ),
        evidence=[
            EvidenceOut(
                evidence_id=e.evidence_id,
                event_id=str(e.event_id),
                title=e.title,
                source_code=e.source_code,
            )
            for e in data.evidence
        ],
        forecast=None
        if run is None
        else ExplanationOut(
            ai_run_id=str(run.id),
            status=run.status,
            output=run.result,
            abstain_reason=run.abstain_reason,
            error_type=run.error_type,
            skill_version=run.skill_version,
        ),
        created_by=f.created_by,
        created_at=f.created_at,
    ).model_dump(mode="json")


@router.get("/forecast")
async def get_latest_forecast(session: Session, _: CanRead) -> dict[str, Any]:
    """最近一次趨勢解讀；從未執行時 data 為 null。"""
    latest = await latest_forecast(session)
    return envelope(None if latest is None else _out(latest))


@router.post("/forecast", status_code=status.HTTP_201_CREATED)
async def trigger_forecast(
    session: Session,
    principal: CanRun,
    forecaster: Annotated[Forecaster, Depends(get_forecaster)],
    body: ForecastIn | None = None,
) -> dict[str, Any]:
    """手動觸發一次趨勢解讀；截止日晚於今日回 VALIDATION_ERROR。"""
    body = body or ForecastIn()
    now = datetime.now(UTC)
    as_of = body.as_of or default_as_of(now)
    if as_of > now.astimezone(TAIPEI).date():
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "截止日不得晚於今日",
            details=[{"field": "body.as_of", "message": "截止日不得晚於今日"}],
        )
    result = await run_forecast(session, principal, forecaster, freq=body.freq, as_of=as_of)
    await session.commit()
    return envelope(_out(result))
