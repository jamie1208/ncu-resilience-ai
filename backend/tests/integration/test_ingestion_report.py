"""P1-T10：一晚穩定運行報告（成功率、連續性、各來源筆數、重複寫入數）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.normalize import content_hash
from app.ingestion.report import build_report, passes, render_markdown
from app.models.source import IngestionRun, Source, SourceItem, SourceItemVersion

pytestmark = pytest.mark.anyio

END = datetime(2026, 10, 19, 2, 0, tzinfo=UTC)  # 台北 10:00
START = END - timedelta(hours=12)


async def add_source(session: AsyncSession, code: str, *, poll: int = 3600, **kw: Any) -> Source:
    source = Source(
        code=code, name=code, type="fake", access_policy="approved", poll_interval=poll, **kw
    )
    session.add(source)
    await session.flush()
    return source


def run(
    source: Source, at: datetime, status: str = "succeeded", counts: dict[str, int] | None = None
) -> IngestionRun:
    return IngestionRun(
        source_id=source.id,
        started_at=at,
        finished_at=at + timedelta(seconds=30),
        status=status,
        counts={"new_items": 0, "changed_items": 0, "unchanged": 0, **(counts or {})},
    )


def hourly(
    source: Source, *, every: timedelta = timedelta(hours=1), **counts: int
) -> list[IngestionRun]:
    runs = []
    at = START + timedelta(minutes=5)
    while at < END:
        runs.append(run(source, at, counts=counts))
        at += every
    return runs


async def report(session: AsyncSession) -> Any:
    await session.commit()
    return await build_report(session, hours=12, now=END)


async def test_success_rate_counts_only_finished_runs(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    runs = hourly(a, new_items=1)
    runs[0].status = "failed"
    session.add_all([*runs, run(a, END - timedelta(minutes=1), "retrying")])
    result = await report(session)
    [row] = result.sources
    assert row.succeeded == len(runs) - 1
    assert row.failed == 1
    assert row.in_flight == 1
    assert row.success_rate == pytest.approx((len(runs) - 1) / len(runs))
    assert row.new_items == len(runs) - 1
    assert result.success_rate == row.success_rate


async def test_runs_outside_window_are_ignored(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    session.add_all([*hourly(a), run(a, START - timedelta(hours=1), "failed")])
    result = await report(session)
    assert result.sources[0].failed == 0
    assert result.success_rate == 1.0


async def test_gap_longer_than_twice_the_interval_breaks_continuity(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    b = await add_source(session, "ncu-b")
    runs = hourly(a)
    session.add_all(
        [
            r
            for r in runs
            if not START + timedelta(hours=4) < r.started_at < START + timedelta(hours=8)
        ]
    )
    session.add_all(hourly(b))
    result = await report(session)
    rows = {r.code: r for r in result.sources}
    assert rows["ncu-a"].continuous is False
    assert rows["ncu-a"].max_gap >= timedelta(hours=4)
    assert rows["ncu-b"].continuous is True
    assert result.continuous is False
    assert passes(result, 0.98) is False


async def test_source_with_long_interval_is_judged_by_its_own_interval(
    session: AsyncSession,
) -> None:
    news = await add_source(session, "ncu-news", poll=21600)
    session.add_all(hourly(news, every=timedelta(hours=6)))
    result = await report(session)
    assert result.sources[0].continuous is True


async def test_enabled_source_without_runs_is_reported_and_not_continuous(
    session: AsyncSession,
) -> None:
    await add_source(session, "ncu-quiet")
    await add_source(session, "ncu-off", enabled=False)
    result = await report(session)
    rows = {r.code: r for r in result.sources}
    assert rows["ncu-quiet"].runs == 0
    assert rows["ncu-quiet"].continuous is False
    assert "ncu-off" not in rows  # 停用且區間內沒有執行的來源不列入


async def test_passes_requires_rate_and_continuity(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    session.add_all(hourly(a))
    result = await report(session)
    assert passes(result, 0.98) is True
    assert passes(result, 1.01) is False


async def test_duplicate_checks_are_zero_with_unique_constraints(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    session.add_all(hourly(a))
    for n, text in ((1, "停水通知"), (2, "停水通知"), (3, "停電通知")):  # 1、2 內文相同
        item = SourceItem(source_id=a.id, external_id=str(n), url=f"https://x/{n}", fetched_at=END)
        session.add(item)
        await session.flush()
        session.add(
            SourceItemVersion(
                item_id=item.id, content_hash=content_hash(text), normalized_text=text
            )
        )
    result = await report(session)
    assert result.duplicate_items == 0
    assert result.duplicate_versions == 0
    assert result.content_duplicates == 1  # 只是參考：不同文章、內文相同


async def test_markdown_has_summary_and_per_source_table(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    session.add_all(hourly(a, new_items=2))
    text = render_markdown(await report(session))
    assert "2026-10-18 22:00" in text and "2026-10-19 10:00" in text  # 台北時間
    assert "| ncu-a |" in text
    assert "100.0%" in text
    assert "重複寫入" in text
