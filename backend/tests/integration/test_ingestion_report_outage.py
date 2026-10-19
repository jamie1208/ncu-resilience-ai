"""P1-T10：G1-2 只計網路正常時段（ADR-0012，使用者 2026-10-19 決定）。

全網中斷時段：連網來源（manual_import 以外）的有結果執行依時間排序後，連續的連線失敗
（SourceUnavailableError）且涉及 ≥ 2 個來源，範圍是第一筆到最後一筆失敗；中間只要有一次成功就斷開。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.report import IngestionReport, build_report, passes, render_markdown
from app.models.source import IngestionRun, Source

pytestmark = pytest.mark.anyio

END = datetime(2026, 10, 19, 4, 32, tzinfo=UTC)  # 台北 12:32
START = END - timedelta(hours=12)
NET = {"error_type": "SourceUnavailableError"}


async def add_source(
    session: AsyncSession, code: str, *, type_: str = "rpage", poll: int = 3600
) -> Source:
    s = Source(code=code, name=code, type=type_, access_policy="approved", poll_interval=poll)
    session.add(s)
    await session.flush()
    return s


def run(
    src: Source, minutes: int, status: str = "succeeded", counts: dict[str, str] | None = None
) -> IngestionRun:
    at = START + timedelta(minutes=minutes)
    return IngestionRun(
        source_id=src.id,
        started_at=at,
        finished_at=at + timedelta(seconds=30),
        status=status,
        counts=counts or {},
    )


def every(
    src: Source, step: int, *, fail_between: tuple[int, int] | None = None
) -> list[IngestionRun]:
    runs = []
    for m in range(5, 12 * 60, step):
        failing = fail_between is not None and fail_between[0] <= m <= fail_between[1]
        runs.append(run(src, m, "failed", NET) if failing else run(src, m))
    return runs


async def report(session: AsyncSession) -> IngestionReport:
    await session.commit()
    return await build_report(session, hours=12, now=END)


async def test_network_wide_outage_is_excluded_and_passes(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    b = await add_source(session, "ncu-b")
    fb = await add_source(session, "facebook", type_="manual_import")
    session.add_all(every(a, 60, fail_between=(200, 400)))
    session.add_all(every(b, 60, fail_between=(200, 400)))
    session.add_all(every(fb, 60))  # 只讀本機檔案，中斷時仍成功，不影響判定
    r = await report(session)
    assert len(r.outages) == 1
    start, end = r.outages[0]
    assert start == START + timedelta(minutes=245) and end == START + timedelta(minutes=365)
    assert r.excluded_runs == 6
    assert r.success_rate == 1.0
    assert r.raw_success_rate is not None and r.raw_success_rate < 1.0
    assert passes(r, 0.98) is True


async def test_single_site_failures_are_not_an_outage(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    b = await add_source(session, "ncu-b")
    session.add_all(every(a, 60, fail_between=(200, 400)))
    session.add_all(every(b, 60))
    r = await report(session)
    assert r.outages == []
    assert r.excluded_runs == 0
    assert r.success_rate is not None and r.success_rate < 0.98
    assert passes(r, 0.98) is False


async def test_success_in_between_splits_the_streak(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    b = await add_source(session, "ncu-b")
    session.add_all([run(a, 100, "failed", NET), run(b, 110), run(a, 120, "failed", NET)])
    session.add(run(b, 200))  # 成功：前後不是同一段
    session.add_all([run(b, 300, "failed", NET), run(a, 305, "failed", NET)])
    r = await report(session)
    assert r.outages == [(START + timedelta(minutes=300), START + timedelta(minutes=305))]


async def test_non_connection_failures_are_never_excluded(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    b = await add_source(session, "ncu-b")
    layout = {"error_type": "LayoutChangedError"}
    session.add_all([run(a, 100, "failed", layout), run(b, 101, "failed", layout)])
    r = await report(session)
    assert r.outages == []


async def test_gaps_during_outage_do_not_break_continuity(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    b = await add_source(session, "ncu-b")
    # a 在中斷期間完全沒有執行紀錄（例如被停用）；b 的連線失敗界定出中斷時段
    session.add_all(
        [
            r
            for r in every(a, 60)
            if not 150 <= int((r.started_at - START).total_seconds() // 60) <= 450
        ]
    )
    session.add_all(every(b, 60, fail_between=(140, 460)))
    c = await add_source(session, "ncu-c")
    session.add_all(every(c, 60, fail_between=(140, 460)))  # 全網中斷：≥ 2 個來源同時連線失敗
    r = await report(session)
    rows = {s.code: s for s in r.sources}
    assert r.outages
    assert rows["ncu-a"].continuous is True  # 4 小時的空檔幾乎都落在中斷時段內


async def test_markdown_lists_outages_and_raw_rate(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-a")
    b = await add_source(session, "ncu-b")
    session.add_all(every(a, 60, fail_between=(200, 400)))
    session.add_all(every(b, 60, fail_between=(200, 400)))
    text = render_markdown(await report(session))
    assert "全網中斷" in text
    assert "排除" in text
    assert "原始成功率" in text
