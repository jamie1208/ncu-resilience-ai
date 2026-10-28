"""P6-T03：analytics materialized views 與 Python 分桶一致（0016_analytics_views）。

- 事件日期以 S02 的發生日期為準，沒有時用建立時間的台北日期；
- 查證為不實（false）、類別為 none 的事件不列入；
- views 只在 `refresh_views` 後更新；`load_counts` 讀出的列等於 `bucket()` 的結果。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.timeseries import (
    CountRow,
    EventPoint,
    bucket,
    load_counts,
    refresh_views,
)
from app.models.event import Event, Location

pytestmark = pytest.mark.anyio


async def _seed(session: AsyncSession) -> None:
    lib = Location(name="總圖書館")
    session.add(lib)
    await session.flush()
    session.add_all(
        [
            Event(title="a", type="theft", status="unverified", location_id=lib.id,
                  occurrence_start_date=date(2026, 1, 6)),
            Event(title="b", type="theft", status="verified", location_id=lib.id,
                  occurrence_start_date=date(2026, 1, 11)),
            Event(title="c", type="theft", status="unverified", location_id=None,
                  occurrence_start_date=date(2026, 2, 3)),
            # 沒有發生日期：UTC 1/11 16:30 = 台北 1/12 00:30 → 下一週
            Event(title="d", type="facility", status="unverified", location_id=lib.id,
                  created_at=datetime(2026, 1, 11, 16, 30, tzinfo=UTC)),
            # 不列入
            Event(title="e", type="theft", status="false", location_id=lib.id,
                  occurrence_start_date=date(2026, 1, 6)),
            Event(title="f", type="none", status="unverified", location_id=lib.id,
                  occurrence_start_date=date(2026, 1, 6)),
        ]
    )  # fmt: skip
    await session.flush()


EXPECTED_POINTS = [
    EventPoint(date(2026, 1, 6), "theft", "總圖書館"),
    EventPoint(date(2026, 1, 11), "theft", "總圖書館"),
    EventPoint(date(2026, 2, 3), "theft", None),
    EventPoint(date(2026, 1, 12), "facility", "總圖書館"),
]


async def test_views_change_only_after_refresh(session: AsyncSession) -> None:
    await _seed(session)
    await refresh_views(session)
    before = await load_counts(session, "week")
    session.add(Event(title="g", type="theft", status="unverified",
                      occurrence_start_date=date(2026, 3, 3)))  # fmt: skip
    await session.flush()
    assert await load_counts(session, "week") == before
    await refresh_views(session)
    after = await load_counts(session, "week")
    assert CountRow(date(2026, 3, 2), "theft", None, 1) in after
    assert len(after) == len(before) + 1


@pytest.mark.parametrize("freq", ["week", "month"])
async def test_views_match_python_bucketing(session: AsyncSession, freq: str) -> None:
    await _seed(session)
    await refresh_views(session)
    rows = await load_counts(session, freq)  # type: ignore[arg-type]
    assert rows == bucket(EXPECTED_POINTS, freq)  # type: ignore[arg-type]


async def test_load_counts_date_range(session: AsyncSession) -> None:
    await _seed(session)
    await refresh_views(session)
    rows = await load_counts(session, "month", start=date(2026, 2, 1), end=date(2026, 2, 28))
    assert rows == [CountRow(date(2026, 2, 1), "theft", None, 1)]
