"""P1-T07：排程派工、擷取執行、重試與死信（使用測試資料庫與假 Adapter）。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import URL, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ingestion.base import AdapterError, LayoutChangedError, SourceUnavailableError
from app.ingestion.source_settings import SourceSettings
from app.ingestion.types import RawItem, SourceConfig
from app.models.audit import AuditLog
from app.models.source import (
    IngestionDeadLetter,
    IngestionRun,
    Source,
    SourceItem,
    SourceItemVersion,
)
from app.worker.tasks.ingest import (
    MAX_RETRIES,
    RunStatus,
    dispatch_due,
    run_attempt,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)
CODE = "ncu-announcements"
SETTINGS = {CODE: SourceSettings(type="fake", options={}, poll_interval=1800)}


def raw(post_id: int, text: str = "停水通知") -> RawItem:
    return RawItem(
        source_code=CODE,
        external_id=str(post_id),
        url=f"https://www.ncu.edu.tw/p/406-1000-{post_id},r15.php",
        title=f"公告 {post_id}",
        content=text,
        published_at=NOW - timedelta(days=1),
        fetched_at=NOW,
    )


class FakeAdapter:
    """依腳本回傳資料或拋出錯誤；cursor 為最大文章編號。"""

    def __init__(self, cfg: SourceConfig, script: list[Any], seen: list[str | None]) -> None:
        self.code = cfg.code
        self._script = script
        self._seen = seen
        self.last_skipped = 1

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        self._seen.append(cursor)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        items: list[RawItem] = step
        return items

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        ids = [int(i.external_id or 0) for i in items]
        return str(max(ids)) if ids else previous


class Harness:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self.maker = maker
        self.script: list[Any] = []
        self.cursors: list[str | None] = []

    def create_adapter(self, cfg: SourceConfig) -> FakeAdapter:
        return FakeAdapter(cfg, self.script, self.cursors)

    async def attempt(self, run_id: Any, attempt: int) -> Any:
        return await run_attempt(
            self.maker,
            run_id,
            attempt=attempt,
            settings=SETTINGS,
            create_adapter=self.create_adapter,
            clock=lambda: NOW,
        )

    async def scalar(self, stmt: Any) -> Any:
        async with self.maker() as s:
            return (await s.execute(stmt)).scalar_one()


@pytest.fixture()
async def harness(session: AsyncSession, test_db_url: URL) -> AsyncIterator[Harness]:
    # 擷取程式自己開關 session；與 conftest 的 session 共用測試資料庫，結束後由它清空
    engine = create_async_engine(test_db_url)
    yield Harness(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def add_source(h: Harness, **kw: Any) -> Source:
    fields: dict[str, Any] = {
        "code": CODE,
        "name": "校園公告",
        "type": "fake",
        "access_policy": "approved",
        "enabled": True,
        "poll_interval": 1800,
    }
    fields.update(kw)
    async with h.maker() as s, s.begin():
        src = Source(**fields)
        s.add(src)
    return src


async def queue_run(h: Harness) -> Any:
    queued = await dispatch_due(h.maker, settings=SETTINGS, now=NOW)
    assert [code for code, _ in queued] == [CODE]
    return queued[0][1]


async def get_run(h: Harness, run_id: Any) -> IngestionRun:
    async with h.maker() as s:
        run = await s.get(IngestionRun, run_id)
        assert run is not None
        return run


# --- 成功的擷取 ------------------------------------------------------------------------


async def test_successful_run_stores_items_and_records_counts(harness: Harness) -> None:
    await add_source(harness)
    run_id = await queue_run(harness)
    harness.script.append([raw(5001), raw(5002)])

    outcome = await harness.attempt(run_id, 0)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.retry is False
    run = await get_run(harness, run_id)
    assert run.status == "succeeded"
    assert run.finished_at == NOW
    assert run.counts == {
        "received": 2,
        "new_items": 2,
        "changed_items": 0,
        "unchanged": 0,
        "skipped": 1,
        "invalid": 0,
        "attempts": 1,
    }
    assert await harness.scalar(select(func.count()).select_from(SourceItem)) == 2
    assert await harness.scalar(select(Source.cursor)) == "5002"


async def test_next_run_continues_from_saved_cursor(harness: Harness) -> None:
    await add_source(harness)
    run_id = await queue_run(harness)
    harness.script.append([raw(5001), raw(5002)])
    await harness.attempt(run_id, 0)

    later = await dispatch_due(harness.maker, settings=SETTINGS, now=NOW + timedelta(hours=1))
    harness.script.append([raw(5003)])
    await harness.attempt(later[0][1], 0)

    assert harness.cursors == [None, "5002"]
    assert await harness.scalar(select(Source.cursor)) == "5003"


async def test_rerunning_same_items_does_not_duplicate(harness: Harness) -> None:
    await add_source(harness)
    for hours in (0, 1, 2):
        queued = await dispatch_due(
            harness.maker, settings=SETTINGS, now=NOW + timedelta(hours=hours)
        )
        harness.script.append([raw(5001), raw(5002)])
        await harness.attempt(queued[0][1], 0)
    assert await harness.scalar(select(func.count()).select_from(SourceItem)) == 2
    assert await harness.scalar(select(func.count()).select_from(SourceItemVersion)) == 2
    last = await harness.scalar(
        select(IngestionRun.counts).order_by(IngestionRun.started_at.desc()).limit(1)
    )
    assert last["unchanged"] == 2


async def test_items_that_fail_normalization_are_counted(harness: Harness) -> None:
    await add_source(harness)
    run_id = await queue_run(harness)
    empty_html = raw(5009).model_copy(
        update={"title": None, "content": "<p>&nbsp;</p>", "content_type": "text/html"}
    )
    harness.script.append([raw(5001), empty_html])
    await harness.attempt(run_id, 0)
    run = await get_run(harness, run_id)
    assert run.counts["received"] == 1
    assert run.counts["invalid"] == 1


# --- 重試與死信 ------------------------------------------------------------------------


async def test_retryable_failures_retry_then_dead_letter(harness: Harness) -> None:
    await add_source(harness)
    run_id = await queue_run(harness)
    harness.script.extend([SourceUnavailableError("來源 x 回應 HTTP 503")] * (MAX_RETRIES + 1))

    outcomes = [await harness.attempt(run_id, n) for n in range(MAX_RETRIES + 1)]

    # 前 3 次標記為重試中，第 4 次（重試 3 次後）進死信
    assert [o.retry for o in outcomes] == [True, True, True, False]
    assert [o.status for o in outcomes] == [RunStatus.RETRYING] * 3 + [RunStatus.FAILED]
    run = await get_run(harness, run_id)
    assert run.status == "failed"
    assert run.finished_at == NOW
    assert run.counts == {"attempts": 4, "error_type": "SourceUnavailableError"}

    async with harness.maker() as s:
        letters = (await s.execute(select(IngestionDeadLetter))).scalars().all()
    assert len(letters) == 1
    letter = letters[0]
    assert letter.run_id == run_id
    assert letter.error_type == "SourceUnavailableError"
    assert letter.error_message == "來源 x 回應 HTTP 503"
    assert letter.attempts == 4
    assert letter.cursor is None
    assert letter.resolved_at is None
    assert await harness.scalar(select(func.count()).select_from(SourceItem)) == 0


async def test_retry_succeeds_without_dead_letter(harness: Harness) -> None:
    await add_source(harness)
    run_id = await queue_run(harness)
    harness.script.extend([SourceUnavailableError("timeout"), [raw(5001)]])
    first = await harness.attempt(run_id, 0)
    second = await harness.attempt(run_id, 1)
    assert (first.retry, second.status) == (True, RunStatus.SUCCEEDED)
    run = await get_run(harness, run_id)
    assert run.counts["attempts"] == 2
    assert await harness.scalar(select(func.count()).select_from(IngestionDeadLetter)) == 0


@pytest.mark.parametrize(
    "error",
    [LayoutChangedError("內頁缺少標題"), AdapterError("回應 HTTP 403")],
)
async def test_non_retryable_failure_goes_straight_to_dead_letter(
    harness: Harness, error: AdapterError
) -> None:
    await add_source(harness)
    run_id = await queue_run(harness)
    harness.script.append(error)
    outcome = await harness.attempt(run_id, 0)
    assert (outcome.status, outcome.retry) == (RunStatus.FAILED, False)
    letter = await harness.scalar(select(IngestionDeadLetter))
    assert letter.error_type == type(error).__name__
    assert letter.attempts == 1


async def test_unexpected_errors_do_not_leak_messages(harness: Harness) -> None:
    # 非 Adapter 錯誤（例如解析套件的例外）訊息可能含有原文，只記錄類型
    await add_source(harness)
    run_id = await queue_run(harness)
    harness.script.append(RuntimeError("原文：某某同學的電話 0912-345-678"))
    outcome = await harness.attempt(run_id, 0)
    assert outcome.status is RunStatus.FAILED
    letter = await harness.scalar(select(IngestionDeadLetter))
    assert letter.error_type == "RuntimeError"
    assert letter.error_message is None


async def test_dead_letter_keeps_cursor_and_is_audited(harness: Harness) -> None:
    await add_source(harness, cursor="5002")
    run_id = await queue_run(harness)
    harness.script.append(LayoutChangedError("列表頁找不到 .module-rcglist"))
    await harness.attempt(run_id, 0)
    assert await harness.scalar(select(IngestionDeadLetter.cursor)) == "5002"
    assert await harness.scalar(select(Source.cursor)) == "5002"  # 失敗不推進 cursor

    async with harness.maker() as s:
        logs = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "ingestion.dead_letter")))
            .scalars()
            .all()
        )
    assert len(logs) == 1
    assert logs[0].actor == "service:ingestion"
    assert logs[0].target == f"sources/{CODE}"
    assert logs[0].request_id == str(run_id)
    assert logs[0].meta == {"error_type": "LayoutChangedError", "attempts": 1}


async def test_finished_run_is_not_executed_again(harness: Harness) -> None:
    # Celery 可能重送同一個任務；已結束的執行不再擷取
    await add_source(harness)
    run_id = await queue_run(harness)
    harness.script.append([raw(5001)])
    await harness.attempt(run_id, 0)
    again = await harness.attempt(run_id, 0)
    assert again.status is RunStatus.SUCCEEDED
    assert harness.cursors == [None]


@pytest.mark.parametrize(
    "update",
    [{"enabled": False}, {"access_policy": "deferred"}],
)
async def test_run_is_skipped_when_source_was_disabled_after_queueing(
    harness: Harness, update: dict[str, Any]
) -> None:
    await add_source(harness)
    run_id = await queue_run(harness)
    async with harness.maker() as s, s.begin():
        src = (await s.execute(select(Source))).scalar_one()
        for key, value in update.items():
            setattr(src, key, value)
    outcome = await harness.attempt(run_id, 0)
    assert outcome.status is RunStatus.SKIPPED
    assert harness.cursors == []
    run = await get_run(harness, run_id)
    assert run.status == "skipped"


# --- 派工 ------------------------------------------------------------------------------


async def test_dispatch_queues_due_sources_only(harness: Harness) -> None:
    await add_source(harness)
    first = await dispatch_due(harness.maker, settings=SETTINGS, now=NOW)
    assert len(first) == 1
    run = await get_run(harness, first[0][1])
    assert (run.status, run.started_at, run.counts) == ("queued", NOW, {})

    # 前一次還在排隊中：不重複派工
    assert await dispatch_due(harness.maker, settings=SETTINGS, now=NOW + timedelta(hours=1)) == []


async def test_dispatch_respects_poll_interval(harness: Harness) -> None:
    await add_source(harness)
    run_id = await queue_run(harness)
    harness.script.append([raw(5001)])
    await harness.attempt(run_id, 0)
    assert (
        await dispatch_due(harness.maker, settings=SETTINGS, now=NOW + timedelta(minutes=29)) == []
    )
    assert (
        len(await dispatch_due(harness.maker, settings=SETTINGS, now=NOW + timedelta(minutes=30)))
        == 1
    )


async def test_dispatch_requeues_stale_in_flight_runs(harness: Harness) -> None:
    # worker 當機時，執行會停在 running；超過 2 小時視為逾期，重新派工
    await add_source(harness)
    stale_id = await queue_run(harness)
    assert (
        await dispatch_due(harness.maker, settings=SETTINGS, now=NOW + timedelta(minutes=119)) == []
    )
    assert (
        len(await dispatch_due(harness.maker, settings=SETTINGS, now=NOW + timedelta(hours=2))) == 1
    )

    # 逾期的執行標記為 abandoned；之後若有 worker 遲到執行它，也不會再擷取
    stale = await get_run(harness, stale_id)
    assert (stale.status, stale.finished_at) == ("abandoned", NOW + timedelta(hours=2))
    outcome = await harness.attempt(stale_id, 0)
    assert outcome.status is RunStatus.ABANDONED
    assert harness.cursors == []


@pytest.mark.parametrize(
    "fields",
    [
        {"enabled": False},
        {"access_policy": "pending"},
        {"access_policy": "rejected"},
        {"code": "not-in-config"},  # 沒有連線設定的來源
    ],
)
async def test_dispatch_skips_ineligible_sources(harness: Harness, fields: dict[str, Any]) -> None:
    await add_source(harness, **fields)
    assert await dispatch_due(harness.maker, settings=SETTINGS, now=NOW) == []


async def test_dispatch_uses_config_interval_when_source_has_none(harness: Harness) -> None:
    await add_source(harness, poll_interval=None)
    run_id = await queue_run(harness)
    harness.script.append([raw(5001)])
    await harness.attempt(run_id, 0)
    # SETTINGS 的 poll_interval 是 1800 秒
    assert (
        await dispatch_due(harness.maker, settings=SETTINGS, now=NOW + timedelta(minutes=29)) == []
    )
    assert (
        len(await dispatch_due(harness.maker, settings=SETTINGS, now=NOW + timedelta(minutes=30)))
        == 1
    )
