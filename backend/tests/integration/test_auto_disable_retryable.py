"""P1-T10：可重試的失敗（連線失敗、逾時、5xx）不計入自動停用（使用者 2026-10-19 決定）。

2026-10-19 一晚運行時本機網路中斷，8 個來源各連續 5 次 ConnectError 而被停用，網路恢復後也不會
自動回來。改為只有不可重試的失敗（版型改變、4xx、設定錯誤）才計入；可重試的失敗仍然寫入死信、
計入失敗率，在連續失敗的判斷中略過（不計入也不中斷）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ingestion.base import LayoutChangedError, SourceUnavailableError
from app.ingestion.health import auto_disable_failing
from app.ingestion.source_settings import SourceSettings
from app.ingestion.types import RawItem, SourceConfig
from app.models.source import IngestionRun, Source
from app.worker.tasks.ingest import RunStatus, dispatch_due, run_once

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
NETWORK = {"error_type": "SourceUnavailableError"}
LAYOUT = {"error_type": "LayoutChangedError"}
OTHER = {"error_type": "RuntimeError"}


async def add_source(session: AsyncSession, code: str = "ncu-a") -> Source:
    src = Source(code=code, name=code, type="fake", access_policy="approved", poll_interval=3600)
    session.add(src)
    await session.flush()
    return src


async def add_failures(
    session: AsyncSession, src: Source, counts: Sequence[dict[str, Any]]
) -> None:
    for i, c in enumerate(counts):
        started = NOW - timedelta(hours=len(counts) - i)
        session.add(
            IngestionRun(
                source_id=src.id,
                started_at=started,
                finished_at=started + timedelta(minutes=1),
                status=RunStatus.FAILED,
                counts={"attempts": 1, **c},
            )
        )
    await session.flush()


async def test_retryable_failures_do_not_disable(session: AsyncSession) -> None:
    src = await add_source(session)
    await add_failures(session, src, [NETWORK] * 8)
    assert await auto_disable_failing(session) == []
    await session.refresh(src)
    assert src.enabled is True


@pytest.mark.parametrize("counts", [LAYOUT, OTHER, {}])  # {}：沒有錯誤類型的舊紀錄照舊計入
async def test_non_retryable_failures_still_disable(
    session: AsyncSession, counts: dict[str, Any]
) -> None:
    src = await add_source(session)
    await add_failures(session, src, [counts] * 5)
    assert await auto_disable_failing(session) == ["ncu-a"]


async def test_retryable_failures_neither_count_nor_break_the_streak(session: AsyncSession) -> None:
    src = await add_source(session)
    await add_failures(
        session, src, [LAYOUT, NETWORK, LAYOUT, LAYOUT, NETWORK, NETWORK, LAYOUT, LAYOUT]
    )
    assert await auto_disable_failing(session) == ["ncu-a"]  # 5 次不可重試的失敗


async def test_four_non_retryable_among_network_failures_keep_enabled(
    session: AsyncSession,
) -> None:
    src = await add_source(session)
    await add_failures(session, src, [NETWORK, LAYOUT, LAYOUT, NETWORK, LAYOUT, LAYOUT, NETWORK])
    assert await auto_disable_failing(session) == []


# --- 失敗時記錄 retryable ----------------------------------------------------------------


class FailingAdapter:
    def __init__(self, cfg: SourceConfig, exc: Exception) -> None:
        self.code = cfg.code
        self._exc = exc

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        raise self._exc

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        return previous


@pytest.fixture()
async def maker(
    session: AsyncSession, test_db_url: URL
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(test_db_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize(
    ("exc", "disabled"),
    [
        (SourceUnavailableError("來源 ncu-a 連線失敗（ConnectError）"), False),
        (LayoutChangedError("來源 ncu-a 版型改變"), True),
        (RuntimeError("其他錯誤"), True),
    ],
)
async def test_five_real_failures_disable_only_when_not_retryable(
    session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    exc: Exception,
    disabled: bool,
) -> None:
    await add_source(session)
    await session.commit()
    settings = {"ncu-a": SourceSettings(type="fake", options={})}
    for _ in range(5):
        [result] = await run_once(
            maker,
            ["ncu-a"],
            settings=settings,
            create_adapter=lambda cfg: FailingAdapter(cfg, exc),
            clock=lambda: NOW,
        )
        assert result.status is RunStatus.FAILED
    async with maker() as s, s.begin():
        assert await auto_disable_failing(s) == (["ncu-a"] if disabled else [])


async def test_dispatch_keeps_queueing_after_network_outage(
    session: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    src = await add_source(session)
    await add_failures(session, src, [NETWORK] * 6)
    await session.commit()
    settings = {"ncu-a": SourceSettings(type="fake", options={}, poll_interval=3600)}
    queued = await dispatch_due(maker, settings=settings, now=NOW + timedelta(hours=1))
    assert [code for code, _ in queued] == ["ncu-a"]
    async with maker() as s:
        assert (await s.execute(select(Source.enabled))).scalar_one() is True
