"""P1-T10：手動執行一輪擷取（`python -m app.worker.tasks.ingest --source <代碼|all> --once`）。

不經 beat、不受 poll_interval 限制、不重試；結果照常寫入 ingestion_runs 與死信。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import URL, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ingestion.base import SourceUnavailableError
from app.ingestion.source_settings import SourceSettings
from app.ingestion.types import RawItem, SourceConfig
from app.models.source import IngestionDeadLetter, IngestionRun, Source, SourceItem
from app.worker.tasks.ingest import RunStatus, build_parser, run_once

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 18, 14, 0, tzinfo=UTC)
A, B, C = "ncu-a", "ncu-b", "ncu-c"
SETTINGS = {code: SourceSettings(type="fake", options={}, poll_interval=3600) for code in (A, B)}


def raw(code: str, n: int) -> RawItem:
    return RawItem(
        source_code=code,
        external_id=str(n),
        url=f"https://demo.ncu.edu.tw/{code}/{n}",
        content=f"公告 {n}",
        fetched_at=NOW,
    )


class FakeAdapter:
    def __init__(self, cfg: SourceConfig, script: dict[str, list[Any]]) -> None:
        self.code = cfg.code
        self._script = script

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        step = self._script[self.code].pop(0)
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
        self.script: dict[str, list[Any]] = {A: [], B: [], C: []}

    async def once(self, codes: Sequence[str] | None) -> list[Any]:
        return await run_once(
            self.maker,
            codes,
            settings=SETTINGS,
            create_adapter=lambda cfg: FakeAdapter(cfg, self.script),
            clock=lambda: NOW,
        )

    async def count(self, model: type[Any], **where: Any) -> int:
        stmt = select(func.count()).select_from(model)
        for key, value in where.items():
            stmt = stmt.where(getattr(model, key) == value)
        async with self.maker() as s:
            return int((await s.execute(stmt)).scalar_one())


@pytest.fixture()
async def harness(session: AsyncSession, test_db_url: URL) -> AsyncIterator[Harness]:
    engine = create_async_engine(test_db_url)
    yield Harness(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def add_source(h: Harness, code: str, **kw: Any) -> None:
    fields: dict[str, Any] = {
        "code": code,
        "name": code,
        "type": "fake",
        "access_policy": "approved",
        "enabled": True,
        "poll_interval": 3600,
    }
    fields.update(kw)
    async with h.maker() as s, s.begin():
        s.add(Source(**fields))


async def test_runs_one_source_and_records_the_run(harness: Harness) -> None:
    await add_source(harness, A)
    harness.script[A] = [[raw(A, 1), raw(A, 2)]]
    [result] = await harness.once([A])
    assert (result.code, result.status) == (A, RunStatus.SUCCEEDED)
    assert result.counts["new_items"] == 2
    assert await harness.count(SourceItem) == 2
    assert await harness.count(IngestionRun, status=RunStatus.SUCCEEDED) == 1


async def test_running_twice_does_not_duplicate(harness: Harness) -> None:
    await add_source(harness, A)
    harness.script[A] = [[raw(A, 1)], [raw(A, 1)]]
    await harness.once([A])
    [again] = await harness.once([A])  # 剛跑過也照樣執行，不受 poll_interval 限制
    assert again.status is RunStatus.SUCCEEDED
    assert again.counts["new_items"] == 0
    assert again.counts["unchanged"] == 1
    assert await harness.count(SourceItem) == 1


async def test_all_runs_every_eligible_source(harness: Harness) -> None:
    await add_source(harness, A)
    await add_source(harness, B, enabled=False)
    await add_source(harness, C)  # 沒有連線設定
    harness.script[A] = [[raw(A, 1)]]
    results = {r.code: r for r in await harness.once(None)}
    assert results[A].status is RunStatus.SUCCEEDED
    assert results[B].status is RunStatus.SKIPPED
    assert results[C].status is RunStatus.SKIPPED
    assert "停用" in results[B].reason
    assert await harness.count(IngestionRun) == 1  # 略過的來源不建立執行紀錄


async def test_unknown_source_is_rejected(harness: Harness) -> None:
    with pytest.raises(ValueError, match="nope"):
        await harness.once(["nope"])


async def test_source_with_run_in_flight_is_skipped(harness: Harness) -> None:
    await add_source(harness, A)
    async with harness.maker() as s, s.begin():
        source = (await s.execute(select(Source).where(Source.code == A))).scalar_one()
        s.add(
            IngestionRun(
                source_id=source.id,
                started_at=NOW - timedelta(minutes=5),
                status=RunStatus.RUNNING,
                counts={},
            )
        )
    [result] = await harness.once([A])
    assert result.status is RunStatus.SKIPPED
    assert "執行中" in result.reason


async def test_failure_is_not_retried_and_goes_to_dead_letter(harness: Harness) -> None:
    await add_source(harness, A)
    harness.script[A] = [SourceUnavailableError("來源 ncu-a 回應 HTTP 503")]
    [result] = await harness.once([A])
    assert result.status is RunStatus.FAILED
    assert await harness.count(IngestionDeadLetter) == 1


def test_parser_requires_once_and_source() -> None:
    parser = build_parser()
    args = parser.parse_args(["--source", "all", "--once"])
    assert args.source == ["all"] and args.once is True
    args = parser.parse_args(["--source", A, "--source", B, "--once"])
    assert args.source == [A, B]
    with pytest.raises(SystemExit):
        parser.parse_args(["--source", "all"])  # 沒有 --once
    with pytest.raises(SystemExit):
        parser.parse_args(["--once"])  # 沒有 --source
