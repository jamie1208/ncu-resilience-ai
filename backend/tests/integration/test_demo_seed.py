"""示範模式的種子資料（P9-T01，ADR-0022）。

在整合測試資料庫跑一次完整種子：以真實的寫入、管線、系列、措施、模擬與公開同步建立虛構資料，
模型以示範用的假供應商代替（不呼叫真實 API）。檢查所有功能頁都有資料、CRI 四構面齊全、
公開快照不含匿名投稿與 Facebook 原文，且重跑結果一致。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.forecast import Outcome as ForecastOutcome
from app.analytics.forecast import latest_forecast
from app.core.config import Settings
from app.demo.seed import DemoTargetError, check_target, seed_demo
from app.models import (
    AIAnalysisRun,
    Alert,
    AnonymousReport,
    Event,
    EventEvidence,
    EventRelation,
    EventSeries,
    EventSeriesMember,
    ImpactEvaluation,
    IngestionRun,
    LocationResolution,
    Publication,
    PublicationVersion,
    ResponseAction,
    ResponseActionTransition,
    RiskAssessment,
    ScenarioRun,
    SeriesAnalysis,
    SeriesCauseDecision,
    Source,
    SourceItem,
    SourceItemVersion,
)
from app.publishing.sync import action_markers, series_input
from app.resilience.cri import build_report
from app.resilience.recurrence import find_recurrences
from app.series.analysis import event_date, latest_analysis, series_events
from app.series.timeline import before_after

pytestmark = pytest.mark.anyio

TODAY = date(2026, 11, 13)


# ---------- 目標資料庫的防呆 ----------


def _settings(environment: str, db_name: str | None) -> Settings:
    # 以 model_copy 覆寫，避免 .env 的 POSTGRES_DB 蓋掉測試值
    return Settings().model_copy(update={"environment": environment, "db_name": db_name})


@pytest.mark.parametrize(
    ("environment", "db_name"),
    [
        ("development", "ncu_resilience_demo"),  # 不是示範環境
        ("production", "ncu_resilience_demo"),
        ("demo", "ncu_resilience"),  # 正式資料庫名稱
        ("demo", "ncu_resilience_test"),
        ("demo", None),
    ],
)
def test_check_target_refuses_non_demo(environment: str, db_name: str | None) -> None:
    settings = _settings(environment, db_name)
    with pytest.raises(DemoTargetError):
        check_target(settings)


def test_check_target_accepts_demo_database() -> None:
    assert check_target(_settings("demo", "ncu_resilience_demo")).database == "ncu_resilience_demo"


# ---------- 種子內容 ----------


async def _count(session: AsyncSession, model: Any, *where: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def _snapshot(session: AsyncSession) -> dict[str, int]:
    models = (
        Event,
        EventEvidence,
        EventRelation,
        EventSeries,
        SeriesAnalysis,
        ImpactEvaluation,
        ResponseAction,
        ResponseActionTransition,
        ScenarioRun,
        Publication,
        AnonymousReport,
        IngestionRun,
        Alert,
    )
    return {m.__tablename__: await _count(session, m) for m in models}


async def test_seed_covers_every_feature(session: AsyncSession) -> None:
    await seed_demo(session, today=TODAY)

    # 事件工作台：多種查證狀態、多來源證據、風險分級
    assert await _count(session, Event) >= 25
    for status in ("verified", "unverified", "false"):
        assert await _count(session, Event, Event.status == status) >= 1, status
    most_evidence = await session.scalar(
        select(func.count()).select_from(EventEvidence).group_by(EventEvidence.event_id)
        .order_by(func.count().desc()).limit(1)
    )  # fmt: skip
    assert (most_evidence or 0) >= 3
    assert await _count(session, RiskAssessment) >= 25

    # 通知中心、事件關聯、地點（S13 別名）
    assert await _count(session, Alert) >= 3
    assert await _count(session, EventRelation, EventRelation.type == "related_event") >= 1
    assert await _count(session, LocationResolution, LocationResolution.decision == "alias_of") >= 1

    # 系列：至少兩個 5 件以上的系列，主系列有規律、推測原因與改善建議，且有一項原因已人工確認
    sizes = (
        await session.scalars(
            select(func.count()).select_from(EventSeriesMember)
            .group_by(EventSeriesMember.series_id)
        )
    ).all()  # fmt: skip
    assert sum(1 for n in sizes if n >= 5) >= 2
    analyses = (
        await session.scalars(select(SeriesAnalysis).where(SeriesAnalysis.status == "ok"))
    ).all()
    assert any(a.patterns and a.causes and a.measures for a in analyses)
    assert await _count(session, SeriesCauseDecision) >= 1
    assert await _count(session, ImpactEvaluation, ImpactEvaluation.status == "ok") >= 1

    # 改善追蹤與再發生提醒
    assert await _count(session, ResponseAction) >= 10
    improved = ResponseActionTransition.to_state == "improved"
    assert await _count(session, ResponseActionTransition, improved) >= 5
    assert await find_recurrences(session, today=TODAY)

    # 韌性成績單：四個構面都有分數，總分可計算
    report = await build_report(session, as_of=TODAY)
    assert all(d.score is not None for d in report.dimensions), report.dimensions
    assert report.score is not None

    # What-if 與趨勢解讀都有 AI 解釋
    scenario = await session.scalar(select(ScenarioRun))
    assert scenario is not None and scenario.ai_run_id is not None
    forecast = await latest_forecast(session)
    assert forecast is not None and forecast.outcome is ForecastOutcome.EXPLAINED

    # 公開網站：事件與系列都有公開內容
    published = Publication.state == "published"
    assert await _count(session, Publication, published, Publication.event_id.is_not(None)) >= 10
    assert await _count(session, Publication, published, Publication.series_id.is_not(None)) >= 1

    # 匿名回報：一則已進入事件、一則還在收件匣
    assert await _count(session, AnonymousReport) >= 2
    anonymous = (Source.code == "anonymous-report",)
    reported = await session.scalar(
        select(func.count()).select_from(EventEvidence)
        .join(SourceItem, SourceItem.id == EventEvidence.source_item_id)
        .join(Source, Source.id == SourceItem.source_id).where(*anonymous)
    )  # fmt: skip
    assert (reported or 0) >= 1

    # 運維與 Skills：每個示範來源都有擷取紀錄，AI 執行紀錄涵蓋多個 Skill
    sources = (await session.scalars(select(Source))).all()
    assert sources
    for source in sources:
        assert await _count(session, IngestionRun, IngestionRun.source_id == source.id) >= 1
    skills = (await session.scalars(select(AIAnalysisRun.skill_name).distinct())).all()
    assert len(skills) >= 10, skills


async def test_seed_never_ingests_real_sources(session: AsyncSession) -> None:
    await seed_demo(session, today=TODAY)
    enabled = (await session.scalars(select(Source.code).where(Source.enabled))).all()
    # 只有內部的匿名回報收件匣保持啟用，讓示範站當場送出的投稿能進入管線
    assert enabled == ["anonymous-report"]


async def test_public_snapshots_hide_raw_text(session: AsyncSession) -> None:
    await seed_demo(session, today=TODAY)
    contents = [
        json.dumps(v, ensure_ascii=False)
        for v in (await session.scalars(select(PublicationVersion.content))).all()
    ]
    assert contents
    raw = [r for r in (await session.scalars(select(AnonymousReport.content))).all()]
    raw += (
        await session.scalars(
            select(SourceItemVersion.normalized_text)
            .join(SourceItem, SourceItem.id == SourceItemVersion.item_id)
            .join(Source, Source.id == SourceItem.source_id)
            .where(Source.code == "facebook")
        )
    ).all()
    assert raw
    for text in raw:
        assert text is not None
        assert all(text not in c for c in contents)


async def test_seed_is_repeatable(session: AsyncSession) -> None:
    await seed_demo(session, today=TODAY)
    first = await _snapshot(session)
    await seed_demo(session, today=TODAY)
    assert await _snapshot(session) == first


async def _main_series(session: AsyncSession) -> EventSeries:
    series_id = await session.scalar(
        select(EventSeriesMember.series_id)
        .group_by(EventSeriesMember.series_id)
        .order_by(func.count().desc())
        .limit(1)
    )
    series = await session.get(EventSeries, series_id)
    assert series is not None
    return series


async def test_bicycle_series_is_main_axis_with_before_after_drop(session: AsyncSession) -> None:
    """主軸是腳踏車失竊：件數最多，改善措施開始前後 8 週的比較算得出來且明顯下降。"""
    await seed_demo(session, today=TODAY)
    series = await _main_series(session)
    events = await series_events(session, series.id)
    assert len(events) >= 30
    assert all("腳踏車" in e.title for e in events)
    analysis = await latest_analysis(session, series.id)
    assert analysis is not None and analysis.title == "腳踏車失竊"

    actions = list(
        await session.scalars(select(ResponseAction).where(ResponseAction.series_id == series.id))
    )
    markers = await action_markers(session, actions)
    dates = [event_date(e) for e in events]
    comparisons = [before_after(dates, m.on, today=TODAY) for m in markers if m.kind == "started"]
    ok = [c for c in comparisons if c.status == "ok"]
    assert ok, comparisons
    assert ok[0].change_ratio is not None and ok[0].change_ratio <= -0.3
    assert any(m.kind == "completed" for m in markers)
    # 公開的系列頁也帶著同一份前後比較
    public = await series_input(session, series, today=TODAY)
    assert any(c.status == "ok" for _, c in public.comparisons)
