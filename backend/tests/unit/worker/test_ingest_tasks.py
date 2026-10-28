"""P1-T07：Celery 設定、指數退避與任務包裝（不連資料庫、不連 Redis）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.core.config import get_settings
from app.worker import celery_app as app_module
from app.worker.celery_app import celery_app
from app.worker.tasks import ingest
from app.worker.tasks.ingest import AttemptOutcome, RunStatus, backoff_seconds


@asynccontextmanager
async def no_db() -> AsyncIterator[Any]:
    yield None


@pytest.fixture()
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"attempts": [], "countdowns": [], "delayed": []}
    monkeypatch.setattr(ingest, "open_sessionmaker", no_db)
    monkeypatch.setattr(ingest, "load_source_settings", dict)
    original_retry = ingest.ingest_source.retry

    def spy_retry(*args: Any, **kwargs: Any) -> Any:
        calls["countdowns"].append(kwargs.get("countdown"))
        return original_retry(*args, **kwargs)

    monkeypatch.setattr(ingest.ingest_source, "retry", spy_retry)
    return calls


# --- 設定 ------------------------------------------------------------------------------


def test_celery_uses_redis_url_from_settings() -> None:
    assert celery_app.conf.broker_url == get_settings().redis_url


def test_celery_runs_in_utc_and_acks_late() -> None:
    conf = celery_app.conf
    assert str(conf.timezone) == "UTC"
    assert conf.enable_utc is True
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_ignore_result is True


def test_beat_dispatches_due_sources_every_minute() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["dispatch-due-sources"] == {
        "task": "ingest.dispatch_due_sources",
        "schedule": 60.0,
    }
    # P4-T07：分析掃描（使用者 2026-10-27 同意改為逐項斷言）
    assert schedule["dispatch-pending-items"] == {
        "task": "pipeline.dispatch_pending_items",
        "schedule": 60.0,
    }
    # P5-T11：公開狀態同步（使用者 2026-10-28 同意加入）
    assert schedule["sync-publications"] == {
        "task": "publishing.sync_publications",
        "schedule": 60.0,
    }
    assert set(schedule) == {"dispatch-due-sources", "dispatch-pending-items", "sync-publications"}


def test_tasks_are_registered() -> None:
    assert app_module.celery_app is celery_app
    assert {"ingest.dispatch_due_sources", "ingest.ingest_source"} <= set(celery_app.tasks)


# --- 指數退避 --------------------------------------------------------------------------


def test_backoff_doubles_from_one_minute() -> None:
    assert [backoff_seconds(n) for n in range(3)] == [60, 120, 240]


# --- 任務包裝 --------------------------------------------------------------------------


def test_ingest_task_retries_three_times_with_backoff(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, list[Any]]
) -> None:
    async def fake_attempt(maker: Any, run_id: uuid.UUID, *, attempt: int, **kw: Any) -> Any:
        patched["attempts"].append(attempt)
        if attempt < ingest.MAX_RETRIES:
            return AttemptOutcome(RunStatus.RETRYING, retry=True)
        return AttemptOutcome(RunStatus.FAILED, retry=False)

    monkeypatch.setattr(ingest, "run_attempt", fake_attempt)
    ingest.ingest_source.apply(args=(str(uuid.uuid4()),))

    assert patched["attempts"] == [0, 1, 2, 3]
    assert patched["countdowns"] == [60, 120, 240]


def test_ingest_task_does_not_retry_on_success(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, list[Any]]
) -> None:
    async def fake_attempt(maker: Any, run_id: uuid.UUID, *, attempt: int, **kw: Any) -> Any:
        patched["attempts"].append(attempt)
        return AttemptOutcome(RunStatus.SUCCEEDED, retry=False)

    monkeypatch.setattr(ingest, "run_attempt", fake_attempt)
    result = ingest.ingest_source.apply(args=(str(uuid.uuid4()),))
    assert result.get() == "succeeded"
    assert patched["attempts"] == [0]
    assert patched["countdowns"] == []


def test_dispatch_task_enqueues_each_due_run(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, list[Any]]
) -> None:
    run_ids = [uuid.uuid4(), uuid.uuid4()]

    async def fake_dispatch(maker: Any, **kw: Any) -> list[tuple[str, uuid.UUID]]:
        return [("ncu-announcements", run_ids[0]), ("ncu-sec", run_ids[1])]

    monkeypatch.setattr(ingest, "dispatch_due", fake_dispatch)
    monkeypatch.setattr(
        ingest.ingest_source, "delay", lambda run_id: patched["delayed"].append(run_id)
    )
    result = ingest.dispatch_due_sources.apply()
    assert result.get() == 2
    assert patched["delayed"] == [str(r) for r in run_ids]
