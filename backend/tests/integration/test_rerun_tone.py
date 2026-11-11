"""P8-T17：既有內容以新語氣重跑（`app/pipeline/rerun_tone.py`）。

- 事件：摘要來自舊版 S02 的事件，以摘要的出處文章重跑 S02，只更新摘要與（AI 產生的）標題；
  類別、地點、查證、出處文章不變；不實與封存的事件略過；失敗或不是事件時保留舊內容；
- 標題只在等於這個事件某次 S02 輸出的標題時才換（使用者填的標題不動）；
- 系列：最新成功分析來自舊版 S12 的系列，略過「每天最多一次」重跑；人工名稱不覆蓋；
- 依版本判斷對象，所以重跑第二次不呼叫模型；超過每日費用上限即停止並回報剩下的數量；
- `--dry-run`（`plan_rerun`）只列對象數量與預估費用，不呼叫模型。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMError
from app.ai.llm.config import load_llm_config
from app.ai.llm.cost import CostLimitExceededError
from app.ai.runner.run import SkillRunResult, SqlRunStore, run_skill
from app.models import AuditLog
from app.models.ai import AIAnalysisRun
from app.models.event import Event, EventEvidence, Location
from app.models.series import EventSeries, SeriesAnalysis
from app.pipeline.analyze_item import S02
from app.pipeline.rerun_tone import plan_rerun, rerun_events, rerun_series
from app.series.analysis import S12
from tests.integration.test_pipeline import SKILLS, SkillScript, add_item, add_source, extraction
from tests.integration.test_series_analysis import NOW, THREE, Analyst, add_series, analyze, output

pytestmark = pytest.mark.anyio

OLD_S02 = "1.0.0"
OLD_S12 = "1.0.0"
NEW_TITLE = "科學一館停電囉，台電來檢修"
NEW_SUMMARY = "提醒你：科學一館 10/22 上午停電檢修，記得先存好檔案。"


# ---------- 資料 ----------


async def add_run(
    session: AsyncSession,
    skill: str,
    version: str,
    *,
    event_id: uuid.UUID | None = None,
    result: Mapping[str, Any] | None = None,
    cost: str = "0.002",
) -> AIAnalysisRun:
    run = AIAnalysisRun(
        event_id=event_id,
        skill_name=skill,
        skill_version=version,
        schema_version="1",
        provider="fake",
        model_id="fake",
        status="succeeded",
        result=dict(result) if result is not None else None,
        attempts=1,
        cost_usd=Decimal(cost),
        trace_id="old",
    )
    session.add(run)
    await session.flush()
    return run


async def add_event(
    session: AsyncSession,
    *,
    title: str = "科學一館停電檢修",
    status: str = "verified",
    version: str = OLD_S02,
    run_title: str | None = "科學一館停電檢修",
) -> Event:
    """建立一個事件：摘要出處為一篇文章，摘要來自 `version` 版的 S02（輸出標題 `run_title`）。"""
    location = Location(name=f"科學一館-{uuid.uuid4().hex[:6]}")
    session.add(location)
    await session.flush()
    source = await add_source(session, f"src-{uuid.uuid4().hex[:8]}")
    item = await add_item(session, source, "科學一館 10/22 上午 9 點停電檢修，請提早存檔。")
    event = Event(
        title=title,
        type="facility",
        status=status,
        location_id=location.id,
        summary="科學一館 10/22 上午停電檢修。",
        summary_evidence_ids=[item.id.hex],
        summary_source_item_id=item.id,
    )
    session.add(event)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    old = await add_run(
        session,
        S02,
        version,
        event_id=event.id,
        result={**extraction(item.id.hex), "title": run_title},
    )
    event.summary_ai_run_id = old.id
    await session.commit()
    return event


def new_extraction(payload: Mapping[str, Any], **override: Any) -> dict[str, Any]:
    eid = str(payload["evidence"][0]["evidence_id"])
    base = extraction(eid)
    base["title"] = NEW_TITLE
    base["summary"] = {"text": NEW_SUMMARY, "evidence_ids": [eid]}
    return {**base, **override}


class Extractor:
    """以 fake provider 跑真的 Skill Runner 執行 S02，記下每次的輸入。"""

    def __init__(self, session: AsyncSession, *builders: Any) -> None:
        self.session = session
        self.builders = list(builders)
        self.payloads: list[Mapping[str, Any]] = []

    async def __call__(
        self, payload: Mapping[str, Any], *, event_id: uuid.UUID | None = None
    ) -> SkillRunResult:
        self.payloads.append(payload)
        builder = self.builders.pop(0) if self.builders else new_extraction
        script = builder if isinstance(builder, LLMError) else builder(payload)
        result = await run_skill(
            SKILLS[S02],
            payload,
            provider=SkillScript({S02: [script] * 3}),
            config=load_llm_config(),
            store=SqlRunStore(self.session),
            event_id=event_id,
            trace_id="trace-tone",
        )
        await self.session.commit()
        return result

    @property
    def calls(self) -> int:
        return len(self.payloads)


class NoCall:
    """不應被呼叫的 Skill。"""

    async def __call__(self, *args: Any, **kwargs: Any) -> SkillRunResult:
        raise AssertionError("不應呼叫模型")


async def audits(session: AsyncSession, action: str) -> Sequence[AuditLog]:
    return list(await session.scalars(select(AuditLog).where(AuditLog.action == action)))


async def reload(session: AsyncSession, event: Event) -> Event:
    await session.refresh(event)
    return event


# ---------- 事件 ----------


async def test_event_summary_and_ai_title_rewritten(session: AsyncSession) -> None:
    event = await add_event(session)
    before = (event.type, event.location_id, event.status, event.summary_source_item_id)
    run = Extractor(session)

    report = await rerun_events(session, run=run, version=SKILLS[S02].version, now=NOW)

    assert run.calls == 1
    assert run.payloads[0]["evidence"][0]["evidence_id"] == event.summary_source_item_id.hex
    event = await reload(session, event)
    assert event.summary == NEW_SUMMARY
    assert event.title == NEW_TITLE
    assert (event.type, event.location_id, event.status, event.summary_source_item_id) == before
    new_run = await session.get(AIAnalysisRun, event.summary_ai_run_id)
    assert new_run is not None and new_run.skill_version == SKILLS[S02].version
    assert new_run.event_id == event.id
    assert (report.updated, report.titled, report.failed, report.remaining) == (1, 1, 0, 0)
    [audit] = await audits(session, "event.resummarize")
    assert audit.target == f"event:{event.id}"
    assert audit.meta["from_version"] == OLD_S02
    assert audit.meta["to_version"] == SKILLS[S02].version
    assert audit.meta["title_changed"] is True
    assert NEW_SUMMARY not in str(audit.meta)  # 稽核不記文字


async def test_user_title_is_kept(session: AsyncSession) -> None:
    event = await add_event(session, title="使用者拆分時填的標題")
    report = await rerun_events(
        session, run=Extractor(session), version=SKILLS[S02].version, now=NOW
    )
    event = await reload(session, event)
    assert event.summary == NEW_SUMMARY
    assert event.title == "使用者拆分時填的標題"
    assert (report.updated, report.titled) == (1, 0)


async def test_title_from_any_earlier_s02_run_counts_as_ai(session: AsyncSession) -> None:
    """標題來自建立事件的那篇文章（不是摘要出處）時，同一事件較早的 S02 輸出仍算 AI 標題。"""
    event = await add_event(session, title="第一篇的標題", run_title="摘要出處的標題")
    await add_run(session, S02, OLD_S02, event_id=event.id, result={"title": "第一篇的標題"})
    await session.commit()
    await rerun_events(session, run=Extractor(session), version=SKILLS[S02].version, now=NOW)
    assert (await reload(session, event)).title == NEW_TITLE


async def test_false_and_archived_events_are_skipped(session: AsyncSession) -> None:
    await add_event(session, status="false")
    await add_event(session, status="archived")
    report = await rerun_events(session, run=NoCall(), version=SKILLS[S02].version, now=NOW)
    assert (report.updated, report.remaining) == (0, 0)


async def test_current_version_is_skipped_and_rerun_is_idempotent(session: AsyncSession) -> None:
    await add_event(session, version=SKILLS[S02].version)
    old = await add_event(session)
    await rerun_events(session, run=Extractor(session), version=SKILLS[S02].version, now=NOW)
    assert (await reload(session, old)).summary == NEW_SUMMARY
    second = await rerun_events(session, run=NoCall(), version=SKILLS[S02].version, now=NOW)
    assert (second.updated, second.remaining) == (0, 0)


async def test_failure_keeps_old_summary_and_title(session: AsyncSession) -> None:
    event = await add_event(session)
    run = Extractor(session, LLMError("timeout"))
    report = await rerun_events(session, run=run, version=SKILLS[S02].version, now=NOW)
    event = await reload(session, event)
    assert event.summary == "科學一館 10/22 上午停電檢修。"
    assert event.title == "科學一館停電檢修"
    assert (report.updated, report.failed) == (0, 1)


async def test_not_event_or_empty_summary_keeps_old(session: AsyncSession) -> None:
    first = await add_event(session)
    second = await add_event(session)
    run = Extractor(
        session,
        lambda p: extraction(str(p["evidence"][0]["evidence_id"]), is_event=False),
        lambda p: new_extraction(p, summary=None),
    )
    report = await rerun_events(session, run=run, version=SKILLS[S02].version, now=NOW)
    for event in (first, second):
        event = await reload(session, event)
        assert event.summary == "科學一館 10/22 上午停電檢修。"
        assert event.title == "科學一館停電檢修"
    assert (report.updated, report.failed) == (0, 2)


async def test_cost_limit_stops_and_reports_remaining(session: AsyncSession) -> None:
    first = await add_event(session)
    second = await add_event(session)
    run = Extractor(session, CostLimitExceededError("超過每日上限"))
    report = await rerun_events(session, run=run, version=SKILLS[S02].version, now=NOW)
    assert run.calls == 1
    assert report.stopped_by_cost is True
    assert (report.updated, report.remaining) == (0, 2)
    for event in (first, second):
        assert (await reload(session, event)).summary == "科學一館 10/22 上午停電檢修。"


# ---------- 系列 ----------


async def old_analysis(session: AsyncSession, series: EventSeries) -> SeriesAnalysis:
    """今天已以「舊版」S12 分析過的系列（成功）。"""
    row = await analyze(session, series, Analyst(session, output))
    assert row is not None and row.status == "ok" and row.ai_run_id is not None
    run = await session.get(AIAnalysisRun, row.ai_run_id)
    assert run is not None
    run.skill_version = OLD_S12
    await session.commit()
    return row


def renamed(payload: Mapping[str, Any]) -> dict[str, Any]:
    return output(payload, title="機車停一停就被撞，大家要小心")


async def test_series_rerun_ignores_daily_limit_and_updates_name(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    first = await old_analysis(session, series)
    run = Analyst(session, renamed)

    report = await rerun_series(session, run=run, version=SKILLS[S12].version, now=NOW)

    assert run.calls == 1
    assert (report.updated, report.failed, report.remaining) == (1, 0, 0)
    latest = await session.scalar(
        select(SeriesAnalysis).where(
            SeriesAnalysis.series_id == series.id, SeriesAnalysis.id != first.id
        )
    )
    assert latest is not None and latest.status == "ok"
    await session.refresh(series)
    assert series.title == "機車停一停就被撞，大家要小心"
    # 第二次：最新成功分析已是現行版本，不再呼叫
    again = await rerun_series(session, run=NoCall(), version=SKILLS[S12].version, now=NOW)
    assert (again.updated, again.remaining) == (0, 0)


async def test_series_manual_name_is_kept(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    await old_analysis(session, series)
    series.title, series.title_source = "我取的名字", "manual"
    await session.commit()
    await rerun_series(session, run=Analyst(session, renamed), version=SKILLS[S12].version, now=NOW)
    await session.refresh(series)
    assert series.title == "我取的名字"


async def test_series_without_old_analysis_is_skipped(session: AsyncSession) -> None:
    current, _ = await add_series(session, THREE)
    await analyze(session, current, Analyst(session, output))  # 現行版本
    await add_series(session, THREE)  # 從沒分析過：交給既有的補做排程
    report = await rerun_series(session, run=NoCall(), version=SKILLS[S12].version, now=NOW)
    assert (report.updated, report.remaining) == (0, 0)


async def test_series_cost_limit_stops(session: AsyncSession) -> None:
    for _ in range(2):
        series, _ = await add_series(session, THREE)
        await old_analysis(session, series)
    run = Analyst(session, CostLimitExceededError("超過"))
    report = await rerun_series(session, run=run, version=SKILLS[S12].version, now=NOW)
    assert run.calls == 1
    assert report.stopped_by_cost is True
    assert (report.updated, report.remaining) == (0, 2)


# ---------- dry run ----------


async def test_plan_counts_targets_and_estimates_cost_without_calling(
    session: AsyncSession,
) -> None:
    await add_event(session)
    await add_event(session)
    await add_event(session, version=SKILLS[S02].version)
    await add_event(session, status="false")
    series, _ = await add_series(session, THREE)
    await old_analysis(session, series)

    plan = await plan_rerun(
        session, s02_version=SKILLS[S02].version, s12_version=SKILLS[S12].version
    )

    assert plan.events == 2
    assert plan.series == 1
    assert plan.event_cost_usd > 0
    assert plan.estimated_usd == pytest.approx(
        plan.events * plan.event_cost_usd + plan.series * plan.series_cost_usd
    )
