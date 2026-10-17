"""擷取排程、重試與死信（P1-T07；規格 F01「定時擷取、失敗重試、重試不重複寫入」）。

流程：
1. beat 每 60 秒執行 `dispatch_due_sources`：找出到期的來源（核准、啟用、有連線設定，
   距上次派工已超過 poll_interval，且沒有執行中的工作），各建立一筆 queued 的 ingestion_run，
   再把 `ingest_source(run_id)` 丟進佇列。
2. `ingest_source` 執行一次嘗試（`run_attempt`）：
   - 成功：標準化、冪等寫入、推進 cursor、標記 succeeded，三者在同一個交易；
   - 可重試的錯誤（逾時、5xx）：標記 retrying，依 60、120、240 秒指數退避重試，最多 3 次；
   - 重試用盡或不可重試（版型改變、4xx、其他例外）：標記 failed，寫入死信與稽核紀錄。
3. 執行中超過 2 小時（例如 worker 當機）視為逾期：標記 abandoned，並重新派工。

ingestion_runs 是擷取的作業紀錄：狀態變更不另寫稽核；寫入資料（source_items.ingest）與
死信（ingestion.dead_letter）各寫一筆稽核紀錄，request_id 都是 run_id。
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from celery import Task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.ingestion.base import AdapterError, SourceAdapter
from app.ingestion.health import AUTO_DISABLE_AFTER, INGESTION_PRINCIPAL, auto_disable_failing
from app.ingestion.normalize import NormalizedItem, normalize_item
from app.ingestion.registry import registry
from app.ingestion.source_settings import SourceSettings, build_config, load_source_settings
from app.ingestion.store import store_items
from app.ingestion.types import AccessPolicy, RawItem, SourceConfig
from app.ingestion.types import RunStatus as RunStatus  # P1-T07 起由本模組公開
from app.models.source import IngestionDeadLetter, IngestionRun, Source
from app.services.audit import record_audit
from app.worker.celery_app import DISPATCH_TASK, INGEST_TASK, celery_app

importlib.import_module("app.ingestion.adapters")  # 匯入即註冊全部 Adapter 類型

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 60
DEFAULT_POLL_INTERVAL = 3600
STALE_AFTER = timedelta(hours=2)
_ERROR_MESSAGE_LIMIT = 500

Maker = async_sessionmaker[AsyncSession]
AdapterFactory = Callable[[SourceConfig], SourceAdapter]


IN_FLIGHT = frozenset({RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.RETRYING})


@dataclass(frozen=True)
class AttemptOutcome:
    """一次嘗試的結果；`retry` 為 True 時由 Celery 任務排定下一次重試。"""

    status: RunStatus
    retry: bool


def backoff_seconds(retries: int) -> int:
    """第 `retries` 次重試前的等待秒數：60、120、240…"""
    return int(BACKOFF_BASE_SECONDS * 2**retries)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_approved(source: Source) -> bool:
    try:
        return AccessPolicy(source.access_policy) is AccessPolicy.APPROVED and source.enabled
    except ValueError:
        return False


# --- 派工 ------------------------------------------------------------------------------


async def dispatch_due(
    maker: Maker, *, settings: Mapping[str, SourceSettings], now: datetime
) -> list[tuple[str, uuid.UUID]]:
    """為到期的來源建立 queued 的執行紀錄，回傳（來源代碼, run_id）。"""
    queued: list[tuple[str, uuid.UUID]] = []
    async with maker() as session, session.begin():
        # 連續失敗的來源先停用，不再派工（P1-T08）
        await auto_disable_failing(session, threshold=AUTO_DISABLE_AFTER)
        sources = (await session.execute(select(Source).order_by(Source.code))).scalars().all()
        for source in sources:
            config = settings.get(source.code)
            if config is None or not _is_approved(source):
                continue
            latest = (
                await session.execute(
                    select(IngestionRun)
                    .where(IngestionRun.source_id == source.id)
                    .order_by(IngestionRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest is not None:
                if latest.status in IN_FLIGHT:
                    if now - latest.started_at < STALE_AFTER:
                        continue
                    latest.status = RunStatus.ABANDONED
                    latest.finished_at = now
                interval = source.poll_interval or config.poll_interval or DEFAULT_POLL_INTERVAL
                if now - latest.started_at < timedelta(seconds=interval):
                    continue
            run = IngestionRun(
                source_id=source.id, started_at=now, status=RunStatus.QUEUED, counts={}
            )
            session.add(run)
            await session.flush()
            queued.append((source.code, run.id))
    return queued


# --- 執行一次嘗試 ----------------------------------------------------------------------


async def run_attempt(
    maker: Maker,
    run_id: uuid.UUID,
    *,
    attempt: int,
    settings: Mapping[str, SourceSettings],
    create_adapter: AdapterFactory = registry.create,
    clock: Callable[[], datetime] = _utc_now,
    max_retries: int = MAX_RETRIES,
) -> AttemptOutcome:
    """執行一次擷取嘗試（attempt 從 0 起算）。"""
    async with maker() as session, session.begin():
        run = await session.get(IngestionRun, run_id)
        if run is None:
            raise LookupError(f"找不到 ingestion_run {run_id}")
        if run.status not in IN_FLIGHT:
            return AttemptOutcome(RunStatus(run.status), retry=False)  # 重複投遞
        source = await session.get(Source, run.source_id)
        if source is None:
            raise LookupError(f"ingestion_run {run_id} 的來源不存在")
        code, cursor = source.code, source.cursor
        config = settings.get(code)
        if config is None or not _is_approved(source):
            run.status = RunStatus.SKIPPED
            run.finished_at = clock()
            run.counts = {"reason": "來源未核准、已停用或沒有連線設定"}
            return AttemptOutcome(RunStatus.SKIPPED, retry=False)
        source_config = build_config(
            code, config, access_policy=source.access_policy, enabled=source.enabled
        )
        run.status = RunStatus.RUNNING
        run.counts = {"attempts": attempt + 1}

    try:
        adapter = create_adapter(source_config)
        raw_items = await adapter.fetch_since(cursor)
        normalized, invalid = _normalize(raw_items)
        next_cursor = adapter.next_cursor(raw_items, cursor)
        async with maker() as session, session.begin():
            result = await store_items(
                session, INGESTION_PRINCIPAL, code, normalized, request_id=str(run_id)
            )
            source_row = await session.get(Source, source.id, with_for_update=True)
            run_row = await session.get(IngestionRun, run_id)
            if source_row is None or run_row is None:
                raise LookupError(f"ingestion_run {run_id} 或其來源已被刪除")
            source_row.cursor = next_cursor
            run_row.status = RunStatus.SUCCEEDED
            run_row.finished_at = clock()
            run_row.counts = {
                **asdict(result),
                "skipped": int(getattr(adapter, "last_skipped", 0)),
                "invalid": invalid,
                "attempts": attempt + 1,
            }
        return AttemptOutcome(RunStatus.SUCCEEDED, retry=False)
    except Exception as exc:
        return await _record_failure(
            maker, run_id, exc, attempt=attempt, max_retries=max_retries, clock=clock
        )


def _normalize(raw_items: list[RawItem]) -> tuple[list[NormalizedItem], int]:
    normalized: list[NormalizedItem] = []
    invalid = 0
    for raw in raw_items:
        try:
            normalized.append(normalize_item(raw))
        except ValueError:
            invalid += 1
    return normalized, invalid


def _safe_message(exc: Exception) -> str | None:
    # Adapter 錯誤訊息只含來源代碼與狀態碼；其他例外可能夾帶原文，只記類型
    if isinstance(exc, AdapterError):
        return str(exc)[:_ERROR_MESSAGE_LIMIT] or None
    return None


async def _record_failure(
    maker: Maker,
    run_id: uuid.UUID,
    exc: Exception,
    *,
    attempt: int,
    max_retries: int,
    clock: Callable[[], datetime],
) -> AttemptOutcome:
    error_type = type(exc).__name__
    retryable = isinstance(exc, AdapterError) and exc.retryable
    async with maker() as session, session.begin():
        run = await session.get(IngestionRun, run_id)
        if run is None:
            raise LookupError(f"找不到 ingestion_run {run_id}") from exc
        run.counts = {"attempts": attempt + 1, "error_type": error_type}
        if retryable and attempt < max_retries:
            run.status = RunStatus.RETRYING
            return AttemptOutcome(RunStatus.RETRYING, retry=True)

        source = await session.get(Source, run.source_id)
        if source is None:
            raise LookupError(f"ingestion_run {run_id} 的來源不存在") from exc
        run.status = RunStatus.FAILED
        run.finished_at = clock()
        session.add(
            IngestionDeadLetter(
                source_id=source.id,
                run_id=run.id,
                error_type=error_type,
                error_message=_safe_message(exc),
                attempts=attempt + 1,
                cursor=source.cursor,
            )
        )
        await session.flush()
        await record_audit(
            session,
            INGESTION_PRINCIPAL,
            "ingestion.dead_letter",
            target=f"sources/{source.code}",
            request_id=str(run_id),
            metadata={"error_type": error_type, "attempts": attempt + 1},
        )
    return AttemptOutcome(RunStatus.FAILED, retry=False)


# --- Celery 任務 -----------------------------------------------------------------------


@asynccontextmanager
async def open_sessionmaker() -> AsyncIterator[Maker]:
    """每個任務一個 engine（NullPool）：每次 asyncio.run 都是新的事件迴圈，連線不能跨迴圈共用。"""
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _run[T](work: Callable[[Maker], Awaitable[T]]) -> T:
    async def main() -> T:
        async with open_sessionmaker() as maker:
            return await work(maker)

    return asyncio.run(main())


@celery_app.task(name=DISPATCH_TASK)
def dispatch_due_sources() -> int:
    """beat 每 60 秒呼叫：派工給到期的來源，回傳派工數。"""
    settings = load_source_settings()
    queued = _run(lambda maker: dispatch_due(maker, settings=settings, now=_utc_now()))
    for _, run_id in queued:
        ingest_source.delay(str(run_id))
    return len(queued)


@celery_app.task(bind=True, name=INGEST_TASK, max_retries=MAX_RETRIES)
def ingest_source(self: Task[Any, Any], run_id: str) -> str:
    """執行一次擷取；可重試的失敗依指數退避重新排入佇列。"""
    settings = load_source_settings()
    attempt = int(self.request.retries)
    outcome = _run(
        lambda maker: run_attempt(maker, uuid.UUID(run_id), attempt=attempt, settings=settings)
    )
    if outcome.retry:
        raise self.retry(countdown=backoff_seconds(attempt))
    return outcome.status.value
