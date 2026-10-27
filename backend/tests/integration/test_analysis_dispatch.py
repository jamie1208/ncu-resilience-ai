"""P4-T07：擷取到分析管線的銜接（定期掃描未分析的文章）。

- `dispatch_pending_items`：挑出待分析的文章標為 queued 並回傳 id（新的先做、每次有上限、
  超過時間窗標為 skipped、今日費用已達上限不排、排隊過久視為遺失重排）。
- `analyze_item` 結束時更新文章的分析狀態（完成、可重試、失敗次數上限）。
- `store_items` 內容改變時重置為 pending。
- beat 排程包含掃描任務。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMAuthError
from app.ingestion.health import INGESTION_PRINCIPAL
from app.ingestion.normalize import NormalizedItem, content_hash
from app.ingestion.store import store_items
from app.models.ai import AIAnalysisRun
from app.models.source import Source, SourceItem
from app.pipeline.analyze_item import (
    ANALYSIS_WINDOW_DAYS,
    ANALYZE_DISPATCH_TASK,
    MAX_ANALYSIS_ATTEMPTS,
    AnalysisStatus,
    analyze_item,
    dispatch_pending_items,
)
from app.worker.celery_app import celery_app
from tests.integration.test_pipeline import (
    NOTICE,
    SkillScript,
    add_item,
    add_source,
    deps,
    eid,
    relevance,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)
DAILY_USD = 1.0


async def item_at(
    session: AsyncSession, source: Source, published: datetime | None, **fields: object
) -> SourceItem:
    item = await add_item(session, source, NOTICE)
    item.published_at = published
    item.fetched_at = published or NOW
    for key, value in fields.items():
        setattr(item, key, value)
    await session.flush()
    return item


async def status_of(session: AsyncSession, item: SourceItem) -> tuple[str, int]:
    await session.refresh(item)
    return item.analysis_status, item.analysis_attempts


async def dispatch(session: AsyncSession, **kwargs: object) -> list[uuid.UUID]:
    params: dict[str, object] = {"now": NOW, "daily_usd": DAILY_USD, **kwargs}
    return await dispatch_pending_items(session, **params)  # type: ignore[arg-type]


# ---------- 掃描 ----------


async def test_new_items_default_to_pending(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-news")
    item = await item_at(session, source, NOW)
    assert await status_of(session, item) == (AnalysisStatus.PENDING, 0)


async def test_dispatch_queues_newest_first_up_to_limit(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-news")
    old = await item_at(session, source, NOW - timedelta(days=3))
    mid = await item_at(session, source, NOW - timedelta(days=2))
    new = await item_at(session, source, NOW - timedelta(days=1))

    queued = await dispatch(session, limit=2)

    assert queued == [new.id, mid.id]
    assert (await status_of(session, new))[0] == AnalysisStatus.QUEUED
    assert new.analysis_queued_at == NOW
    assert (await status_of(session, old))[0] == AnalysisStatus.PENDING


async def test_dispatch_tops_up_only_to_limit_while_items_are_queued(
    session: AsyncSession,
) -> None:
    source = await add_source(session, "ncu-news")
    for days in (1, 2, 3, 4):
        await item_at(session, source, NOW - timedelta(days=days))

    assert len(await dispatch(session, limit=3)) == 3
    assert await dispatch(session, limit=3, now=NOW + timedelta(minutes=1)) == []


async def test_dispatch_skips_items_outside_window(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-sec")
    stale = await item_at(session, source, NOW - timedelta(days=ANALYSIS_WINDOW_DAYS, hours=1))
    recent = await item_at(session, source, NOW - timedelta(days=ANALYSIS_WINDOW_DAYS - 1))

    queued = await dispatch(session)

    assert queued == [recent.id]
    assert (await status_of(session, stale))[0] == AnalysisStatus.SKIPPED


async def test_dispatch_uses_fetched_at_when_published_at_is_missing(
    session: AsyncSession,
) -> None:
    source = await add_source(session, "facebook")
    item = await item_at(session, source, None)

    assert await dispatch(session) == [item.id]


async def test_dispatch_waits_when_daily_cost_limit_is_reached(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-news")
    item = await item_at(session, source, NOW)
    session.add(
        AIAnalysisRun(
            skill_name="relevance-filter",
            skill_version="1.0.0",
            schema_version="1",
            provider="fake",
            model_id="fake",
            status="succeeded",
            attempts=1,
            cost_usd=DAILY_USD,
            trace_id="t",
            created_at=NOW - timedelta(minutes=5),
        )
    )
    await session.flush()

    assert await dispatch(session) == []
    assert (await status_of(session, item))[0] == AnalysisStatus.PENDING


async def test_dispatch_requeues_items_queued_too_long(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-news")
    lost = await item_at(
        session,
        source,
        NOW,
        analysis_status=AnalysisStatus.QUEUED,
        analysis_queued_at=NOW - timedelta(hours=2),
    )

    assert await dispatch(session) == [lost.id]
    await session.refresh(lost)
    assert lost.analysis_queued_at == NOW


async def test_dispatch_ignores_finished_items(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-news")
    for status in (AnalysisStatus.DONE, AnalysisStatus.SKIPPED, AnalysisStatus.FAILED):
        await item_at(session, source, NOW, analysis_status=status)

    assert await dispatch(session) == []


COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "docker" / "docker-compose.yml"


@pytest.mark.parametrize("service", ["api", "worker"])
def test_containers_mount_product_skills(service: str) -> None:
    """容器內 `app/ai/runner/loader.py` 從 /skills 載入產品 Skills。"""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    volumes = compose["services"][service].get("volumes") or compose["x-backend"]["volumes"]
    assert "../../skills:/skills:ro" in volumes


def test_beat_schedules_dispatch_task() -> None:
    tasks = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert ANALYZE_DISPATCH_TASK in tasks
    assert ANALYZE_DISPATCH_TASK in celery_app.tasks


# ---------- 管線結束時的狀態 ----------


async def test_finished_pipeline_marks_item_done(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-news")
    item = await item_at(session, source, NOW, analysis_status=AnalysisStatus.QUEUED)
    await session.commit()
    provider = SkillScript({"relevance-filter": [relevance(eid(item), relevant=False)]})

    await analyze_item(session, item.id, deps(provider))

    assert (await status_of(session, item))[0] == AnalysisStatus.DONE


async def test_failed_step_keeps_item_pending_until_attempt_limit(session: AsyncSession) -> None:
    source = await add_source(session, "ncu-news")
    item = await item_at(session, source, NOW, analysis_status=AnalysisStatus.QUEUED)
    await session.commit()
    failures = [LLMAuthError("金鑰錯誤", status_code=401)] * MAX_ANALYSIS_ATTEMPTS
    provider = SkillScript({"relevance-filter": failures})

    for attempt in range(1, MAX_ANALYSIS_ATTEMPTS):
        await analyze_item(session, item.id, deps(provider))
        assert await status_of(session, item) == (AnalysisStatus.PENDING, attempt)

    await analyze_item(session, item.id, deps(provider))
    assert await status_of(session, item) == (AnalysisStatus.FAILED, MAX_ANALYSIS_ATTEMPTS)


async def test_cost_limit_keeps_item_pending_without_counting_attempt(
    session: AsyncSession,
) -> None:
    source = await add_source(session, "ncu-news")
    item = await item_at(session, source, NOW, analysis_status=AnalysisStatus.QUEUED)
    session.add(
        AIAnalysisRun(
            skill_name="relevance-filter",
            skill_version="1.0.0",
            schema_version="1",
            provider="fake",
            model_id="fake",
            status="succeeded",
            attempts=1,
            cost_usd=1000,
            trace_id="t",
        )
    )
    await session.commit()
    provider = SkillScript({})

    await analyze_item(session, item.id, deps(provider))

    assert provider.called == []
    assert await status_of(session, item) == (AnalysisStatus.PENDING, 0)


# ---------- 內容改變 ----------


def normalized(code: str, text: str) -> NormalizedItem:
    return NormalizedItem(
        source_code=code,
        external_id="post-1",
        url="https://example.test/post-1",
        title=None,
        language="zh",
        published_at=NOW,
        fetched_at=NOW,
        text=text,
        content_hash=content_hash(text),
    )


async def test_changed_content_resets_item_to_pending(session: AsyncSession) -> None:
    await add_source(session, "ncu-news")
    await store_items(session, INGESTION_PRINCIPAL, "ncu-news", [normalized("ncu-news", NOTICE)])
    item = await session.scalar(select(SourceItem))
    assert item is not None
    item.analysis_status = AnalysisStatus.DONE
    item.analysis_attempts = 2
    await session.flush()

    await store_items(session, INGESTION_PRINCIPAL, "ncu-news", [normalized("ncu-news", NOTICE)])
    assert await status_of(session, item) == (AnalysisStatus.DONE, 2)

    changed = NOTICE + "\n\n更新：改到 13:00 結束。"
    await store_items(session, INGESTION_PRINCIPAL, "ncu-news", [normalized("ncu-news", changed)])
    assert await status_of(session, item) == (AnalysisStatus.PENDING, 0)
