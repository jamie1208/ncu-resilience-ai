"""P1-T08：來源健康狀態與連續失敗自動停用。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ingestion.health import AUTO_DISABLE_AFTER, auto_disable_failing, compute_health
from app.ingestion.source_settings import SourceSettings
from app.models.audit import AuditLog
from app.models.source import IngestionDeadLetter, IngestionRun, Source
from app.worker.tasks.ingest import dispatch_due

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 24, 4, 0, tzinfo=UTC)


async def add_source(session: AsyncSession, code: str = "ncu-announcements", **kw: Any) -> Source:
    fields: dict[str, Any] = {
        "code": code,
        "name": code,
        "type": "rpage",
        "access_policy": "approved",
        "enabled": True,
        "poll_interval": 3600,
    }
    fields.update(kw)
    src = Source(**fields)
    session.add(src)
    await session.flush()
    return src


async def add_runs(
    session: AsyncSession, source: Source, statuses: list[str], *, start: datetime, step: timedelta
) -> list[IngestionRun]:
    """依時間先後新增執行紀錄（statuses[0] 最舊）。"""
    runs = []
    for i, status in enumerate(statuses):
        started = start + step * i
        finished = (
            None if status in ("queued", "running", "retrying") else started + timedelta(minutes=2)
        )
        run = IngestionRun(
            source_id=source.id, started_at=started, finished_at=finished, status=status, counts={}
        )
        session.add(run)
        runs.append(run)
    await session.flush()
    return runs


async def health_of(session: AsyncSession, code: str = "ncu-announcements") -> Any:
    report = {h.code: h for h in await compute_health(session, now=NOW)}
    return report[code]


# --- 健康狀態 --------------------------------------------------------------------------


async def test_healthy_source(session: AsyncSession) -> None:
    src = await add_source(session)
    await add_runs(
        session, src, ["succeeded"] * 5, start=NOW - timedelta(hours=5), step=timedelta(hours=1)
    )
    h = await health_of(session)
    assert h.status == "healthy"
    assert h.success_rate == 1.0
    assert h.runs_in_window == 5
    assert h.consecutive_failures == 0
    assert h.last_run_status == "succeeded"
    assert h.last_run_at == NOW - timedelta(hours=1)
    assert h.last_success_at == NOW - timedelta(hours=1) + timedelta(minutes=2)
    assert h.open_dead_letters == 0


async def test_success_rate_counts_only_finished_runs_in_window(session: AsyncSession) -> None:
    src = await add_source(session)
    # 窗口外的失敗不算
    await add_runs(
        session, src, ["failed"] * 3, start=NOW - timedelta(days=9), step=timedelta(hours=1)
    )
    # 窗口內：49 成功、1 abandoned（計為失敗）、1 skipped（不計）、最後 1 筆執行中（不計）
    statuses = ["succeeded"] * 24 + ["abandoned"] + ["succeeded"] * 25 + ["skipped", "running"]
    await add_runs(session, src, statuses, start=NOW - timedelta(hours=52), step=timedelta(hours=1))
    h = await health_of(session)
    assert h.runs_in_window == 50
    assert h.success_rate == pytest.approx(0.98)
    assert h.status == "healthy"  # 98% 剛好達到 G1-2 門檻


async def test_success_rate_below_threshold_is_degraded(session: AsyncSession) -> None:
    src = await add_source(session)
    statuses = ["failed"] + ["succeeded"] * 9
    await add_runs(session, src, statuses, start=NOW - timedelta(hours=10), step=timedelta(hours=1))
    h = await health_of(session)
    assert h.success_rate == pytest.approx(0.9)
    assert h.status == "degraded"


async def test_open_dead_letters_make_source_degraded(session: AsyncSession) -> None:
    src = await add_source(session)
    runs = await add_runs(
        session,
        src,
        ["failed"] + ["succeeded"] * 60,
        start=NOW - timedelta(hours=62),
        step=timedelta(hours=1),
    )
    session.add(
        IngestionDeadLetter(source_id=src.id, run_id=runs[0].id, error_type="X", attempts=1)
    )
    resolved = IngestionDeadLetter(
        source_id=src.id, run_id=runs[0].id, error_type="X", attempts=1, resolved_at=NOW
    )
    session.add(resolved)
    await session.flush()
    h = await health_of(session)
    assert h.open_dead_letters == 1
    assert h.status == "degraded"


async def test_latest_failure_is_failing(session: AsyncSession) -> None:
    src = await add_source(session)
    await add_runs(
        session,
        src,
        ["succeeded", "succeeded", "failed", "abandoned"],
        start=NOW - timedelta(hours=4),
        step=timedelta(hours=1),
    )
    h = await health_of(session)
    assert h.status == "failing"
    assert h.consecutive_failures == 2
    assert h.last_run_status == "abandoned"


async def test_no_recent_success_is_stale(session: AsyncSession) -> None:
    # 最後一次成功在 3 個擷取間隔之前，之後也沒有失敗紀錄（例如 worker 沒有在跑）
    src = await add_source(session)
    await add_runs(
        session, src, ["succeeded"], start=NOW - timedelta(hours=4), step=timedelta(hours=1)
    )
    h = await health_of(session)
    assert h.status == "stale"


async def test_source_without_runs_is_unknown(session: AsyncSession) -> None:
    await add_source(session)
    h = await health_of(session)
    assert h.status == "unknown"
    assert h.success_rate is None
    assert h.last_run_at is None


@pytest.mark.parametrize("fields", [{"enabled": False}, {"access_policy": "deferred"}])
async def test_disabled_source(session: AsyncSession, fields: dict[str, Any]) -> None:
    src = await add_source(session, **fields)
    await add_runs(
        session, src, ["succeeded"], start=NOW - timedelta(hours=1), step=timedelta(hours=1)
    )
    h = await health_of(session)
    assert h.status == "disabled"


async def test_health_lists_every_source_sorted_by_code(session: AsyncSession) -> None:
    for code in ("ncu-sec", "ncu-aa", "ncu-oia"):
        await add_source(session, code)
    assert [h.code for h in await compute_health(session, now=NOW)] == [
        "ncu-aa",
        "ncu-oia",
        "ncu-sec",
    ]


# --- 自動停用 --------------------------------------------------------------------------


async def test_five_consecutive_failures_disable_the_source(session: AsyncSession) -> None:
    assert AUTO_DISABLE_AFTER == 5
    src = await add_source(session)
    await add_runs(
        session,
        src,
        ["succeeded"] + ["failed"] * 5,
        start=NOW - timedelta(hours=6),
        step=timedelta(hours=1),
    )
    disabled = await auto_disable_failing(session)
    assert disabled == ["ncu-announcements"]
    await session.refresh(src)
    assert src.enabled is False

    log = (await session.execute(select(AuditLog))).scalar_one()
    assert (log.actor, log.action, log.target) == (
        "service:ingestion",
        "sources.auto_disable",
        "sources/ncu-announcements",
    )
    assert log.meta == {"consecutive_failures": 5}


@pytest.mark.parametrize(
    "statuses",
    [
        ["failed"] * 4,  # 未達 5 次
        ["failed"] * 4 + ["succeeded"],  # 最後成功
        ["failed"] * 2 + ["succeeded"] + ["failed"] * 3,  # 中間有成功
    ],
)
async def test_fewer_consecutive_failures_keep_source_enabled(
    session: AsyncSession, statuses: list[str]
) -> None:
    src = await add_source(session)
    await add_runs(session, src, statuses, start=NOW - timedelta(hours=8), step=timedelta(hours=1))
    assert await auto_disable_failing(session) == []
    await session.refresh(src)
    assert src.enabled is True


async def test_skipped_and_in_flight_runs_do_not_break_the_streak(session: AsyncSession) -> None:
    src = await add_source(session)
    statuses = ["failed", "failed", "skipped", "failed", "failed", "failed", "running"]
    await add_runs(session, src, statuses, start=NOW - timedelta(hours=8), step=timedelta(hours=1))
    assert await auto_disable_failing(session) == ["ncu-announcements"]


async def test_already_disabled_source_is_not_disabled_again(session: AsyncSession) -> None:
    src = await add_source(session, enabled=False)
    await add_runs(
        session, src, ["failed"] * 6, start=NOW - timedelta(hours=8), step=timedelta(hours=1)
    )
    assert await auto_disable_failing(session) == []
    assert (await session.execute(select(AuditLog))).scalars().all() == []


# --- 派工時自動停用 --------------------------------------------------------------------


@pytest.fixture()
async def maker(
    session: AsyncSession, test_db_url: URL
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(test_db_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_dispatch_disables_failing_source_instead_of_queueing(
    session: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    src = await add_source(session)
    await add_runs(
        session, src, ["failed"] * 5, start=NOW - timedelta(hours=8), step=timedelta(hours=1)
    )
    await session.commit()

    settings = {"ncu-announcements": SourceSettings(type="rpage", options={})}
    assert await dispatch_due(maker, settings=settings, now=NOW) == []
    async with maker() as s:
        enabled = (await s.execute(select(Source.enabled))).scalar_one()
    assert enabled is False
