"""管理端儀表板（P3-T03）：`GET /api/v1/admin/dashboard`，需要 `events:read_all`。

個人版取代規格 §10 的公開 `/api/v1/dashboard/summary`（ADR-0008）。只回聚合數字與事件標題：
- events：依狀態、類型的數量，近 7 天新增數，以及最新風險等級屬於高風險（config/risk-policy.yaml
  的 high_risk_levels）且尚未結案的事件
- alerts：依狀態的數量
- ai_runs_last_7d：近 7 天各 Skill 的執行數、失敗與 abstain 數、失敗率、費用
來源健康狀態另見 `/api/v1/admin/ingestion-runs/health`。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.domain.states import EventStatus
from app.models.ai import AIAnalysisRun, RiskAssessment
from app.models.alert import Alert
from app.models.event import Event
from app.risk.engine import load_policy

router = APIRouter(prefix="/dashboard", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]

WINDOW = timedelta(days=7)
HIGH_RISK_LIMIT = 20
CLOSED_STATUSES = (EventStatus.RESOLVED.value, EventStatus.ARCHIVED.value, EventStatus.FALSE.value)


async def _counts(session: AsyncSession, column: Any) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).group_by(column))
    return {key: n for key, n in rows}


async def _high_risk_open(session: AsyncSession, levels: list[str]) -> list[dict[str, str]]:
    latest = (
        select(RiskAssessment.event_id, RiskAssessment.level)
        .ext(distinct_on(RiskAssessment.event_id))
        .order_by(
            RiskAssessment.event_id, RiskAssessment.created_at.desc(), RiskAssessment.id.desc()
        )
        .subquery()
    )
    rows = await session.execute(
        select(Event.id, Event.title, latest.c.level)
        .join(latest, latest.c.event_id == Event.id)
        .where(latest.c.level.in_(levels), Event.status.not_in(CLOSED_STATUSES))
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(HIGH_RISK_LIMIT)
    )
    return [{"id": str(i), "title": t, "level": lv} for i, t, lv in rows]


async def _ai_runs(session: AsyncSession, since: datetime) -> list[dict[str, Any]]:
    run = AIAnalysisRun
    rows = await session.execute(
        select(
            run.skill_name,
            func.count(),
            func.count().filter(run.status == "failed"),
            func.count().filter(run.status == "abstained"),
            func.coalesce(func.sum(run.cost_usd), 0),
        )
        .where(run.created_at >= since)
        .group_by(run.skill_name)
        .order_by(run.skill_name)
    )
    return [
        {
            "skill_name": name,
            "runs": runs,
            "failed": failed,
            "abstained": abstained,
            "failure_rate": round(failed / runs, 4) if runs else 0.0,
            "cost_usd": float(cost),
        }
        for name, runs, failed, abstained, cost in rows
    ]


@router.get("")
async def dashboard(session: Session, _: CanRead) -> dict[str, Any]:
    """儀表板聚合數字（近 7 天窗口以目前時間計算）。"""
    since = datetime.now(UTC) - WINDOW
    policy = load_policy()
    created_recent = await session.scalar(
        select(func.count()).select_from(Event).where(Event.created_at >= since)
    )
    data = {
        "events": {
            "by_status": await _counts(session, Event.status),
            "by_type": await _counts(session, Event.type),
            "created_last_7d": created_recent or 0,
            "high_risk_open": await _high_risk_open(session, policy.high_risk_levels),
        },
        "alerts": {"by_state": await _counts(session, Alert.state)},
        "ai_runs_last_7d": await _ai_runs(session, since),
    }
    return envelope(data, window_days=WINDOW.days)
