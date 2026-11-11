"""P8-T15（ADR-0019 決策 1-4）：S14 的效益評估結果只在管理端，不進入公開快照與公開端點。

以 G5-5 的附錄 A 與系列資料為底，在系列上建立一筆效益評估（改善百分比、改善前／後情境、風險、
補強建議），同步公開狀態後：

- 每一筆 publication 的每個版本快照都不含評估結果的任何文字、百分比標示與評估 ID；
- 爬過 `/api/v1/public/*` 的每個路由，回應同樣不含；
- 管理端的評估 API 不在公開路由下。
"""

from __future__ import annotations

import json

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models.event import Event
from app.models.impact import ImpactEvaluation
from app.models.publication import PublicationVersion
from app.models.series import EventSeries, EventSeriesMember, SeriesAnalysis
from app.publishing.sync import sync_all
from tests.integration.conftest import (  # noqa: F401 — 共用整合測試的資料庫 fixture
    alembic_cfg,
    anyio_backend,
    migrated,
    session,
    test_db_url,
)
from tests.security.test_public_exposure import PUBLIC, client, crawl, seed  # noqa: F401

pytestmark = pytest.mark.anyio

BEFORE = "改善前情境獨有字串：總圖書館的水桶排排站"
AFTER = "改善後情境獨有字串：雨季過後書架保持乾爽"
RISK = "風險獨有字串：排水孔清完還是可能再堵"
SUGGESTION = "補強建議獨有字串：同步檢查外牆"
REASON = "評估理由獨有字串：處理了推測的排水問題"
LEAKS = (BEFORE, AFTER, RISK, SUGGESTION, REASON, "AI 估計，非實證", "improvement")


async def add_evaluation(session: AsyncSession) -> ImpactEvaluation:  # noqa: F811
    """在 seed 建立的系列（有成功分析的那一個）上加一筆效益評估。"""
    series_id, analysis_id = (
        await session.execute(
            select(SeriesAnalysis.series_id, SeriesAnalysis.id)
            .where(SeriesAnalysis.status == "ok")
            .order_by(SeriesAnalysis.created_at.desc())
            .limit(1)
        )
    ).one()
    row = ImpactEvaluation(
        series_id=series_id,
        analysis_id=analysis_id,
        selection_key="m1",
        selected=["m1"],
        status="ok",
        improvement_low=35,
        improvement_high=55,
        cap=80,
        result={
            "before": BEFORE,
            "after": AFTER,
            "covered_causes": [{"cause_id": "c1", "description": "排水管線老舊"}],
            "uncovered_causes": [],
            "overlaps": [],
            "risks": [RISK],
            "suggestion": {"measure_id": "m2", "action": SUGGESTION, "reason": SUGGESTION},
            "reason": REASON,
            "evidence_ids": ["E1", "c1", "m1"],
        },
        counts={"window_days": 90, "baseline": 4.0, "observed": 6, "history_weeks": 6},
        created_by="u-1",
    )
    session.add(row)
    await session.commit()
    return row


async def test_snapshots_and_public_routes_exclude_impact_evaluations(
    client: httpx2.AsyncClient,  # noqa: F811
    session: AsyncSession,  # noqa: F811
) -> None:
    await seed(session)
    evaluation = await add_evaluation(session)
    member = await session.scalar(
        select(EventSeriesMember.event_id).where(
            EventSeriesMember.series_id == evaluation.series_id
        )
    )
    assert member is not None and await session.get(Event, member) is not None
    await sync_all(session)

    snapshots = [
        json.dumps(v.content, ensure_ascii=False)
        for v in await session.scalars(select(PublicationVersion))
        if v.content is not None
    ]
    assert snapshots, "沒有任何公開快照，測試資料沒有發揮作用"
    assert any("總圖書館漏水" in s for s in snapshots), "系列分析頁沒有公開"
    bodies = await crawl(client, session)
    joined = "\n".join(
        [*snapshots, *(json.dumps(json.loads(b), ensure_ascii=False) for b in bodies)]
    )
    for leak in LEAKS:
        assert leak not in joined, f"公開內容出現效益評估：{leak}"
    assert str(evaluation.id) not in joined
    for series_id in await session.scalars(select(EventSeries.id)):
        assert str(series_id) not in joined


def test_impact_api_is_admin_only() -> None:
    paths = create_app().openapi()["paths"]
    impact = [p for p in paths if "impact" in p]
    assert impact, "找不到效益評估的路由"
    assert all(p.startswith("/api/v1/admin/") for p in impact)
    assert not any(p.startswith(PUBLIC) and "impact" in p for p in paths)
