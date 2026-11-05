"""P8-T03：計數依代表地點分組、被手動合併的事件不計數（0021_analytics_views_canonical）。

- views：別名的事件算在代表地點名稱底下，上層地點不併入，被併入的事件不計數；
  `load_counts` 仍等於 `bucket()` 的結果；
- 再發生：別名與代表地點的事件湊成一組，被併入的事件不列入（合併後只算 1 件）；
- 韌性成績單：被併入的事件不讀出，重複事件依代表地點判斷同地點；
- 趨勢解讀：異常週期的事件依代表地點名稱比對訊號，被併入的事件不當證據。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.forecast import Signal, _period_events
from app.analytics.timeseries import (
    Anomaly,
    EventPoint,
    bucket,
    load_counts,
    load_locations,
    refresh_views,
)
from app.models.event import Event, EventMerge, Location
from app.resilience.cri import load_records
from app.resilience.recurrence import find_recurrences

pytestmark = pytest.mark.anyio

MONDAY = date(2026, 1, 5)


async def _locations(session: AsyncSession) -> dict[str, Location]:
    lib = Location(name="總圖書館")
    area = Location(name="宿舍區")
    session.add_all([lib, area])
    await session.flush()
    alias = Location(name="總圖", canonical_id=lib.id)
    dorm = Location(name="女十四舍", parent_id=area.id)
    session.add_all([alias, dorm])
    await session.flush()
    return {"lib": lib, "alias": alias, "area": area, "dorm": dorm}


async def _event(session: AsyncSession, title: str, loc: Location | None, day: date) -> Event:
    e = Event(
        title=title,
        type="facility",
        status="unverified",
        location_id=loc.id if loc else None,
        occurrence_start_date=day,
    )
    session.add(e)
    await session.flush()
    return e


async def _merge(session: AsyncSession, merged: Event, target: Event) -> None:
    merged.status = "archived"
    session.add(
        EventMerge(merged_event_id=merged.id, target_event_id=target.id, decided_by="admin")
    )
    await session.flush()


async def test_load_locations(session: AsyncSession) -> None:
    locs = await _locations(session)
    refs = await load_locations(session)
    assert refs[locs["alias"].id].canonical_id == locs["lib"].id
    assert refs[locs["dorm"].id].parent_id == locs["area"].id


@pytest.mark.parametrize("freq", ["week", "month"])
async def test_views_group_by_representative(session: AsyncSession, freq: str) -> None:
    locs = await _locations(session)
    target = await _event(session, "a", locs["lib"], MONDAY)
    await _event(session, "b", locs["alias"], MONDAY + timedelta(days=1))
    await _event(session, "c", locs["dorm"], MONDAY)
    await _event(session, "d", locs["area"], MONDAY)
    merged = await _event(session, "e", locs["alias"], MONDAY + timedelta(days=2))
    await _merge(session, merged, target)

    await refresh_views(session)
    rows = await load_counts(session, freq)  # type: ignore[arg-type]
    expected = [
        EventPoint(MONDAY, "facility", "總圖書館"),
        EventPoint(MONDAY + timedelta(days=1), "facility", "總圖書館"),
        EventPoint(MONDAY, "facility", "女十四舍"),
        EventPoint(MONDAY, "facility", "宿舍區"),
    ]
    assert rows == bucket(expected, freq)  # type: ignore[arg-type]


async def test_recurrence_groups_alias_with_representative(session: AsyncSession) -> None:
    locs = await _locations(session)
    today = date(2026, 11, 5)
    a = await _event(session, "停電一", locs["lib"], today - timedelta(days=3))
    b = await _event(session, "停電二", locs["alias"], today - timedelta(days=1))
    await _event(session, "停電三", locs["dorm"], today - timedelta(days=1))

    found = await find_recurrences(session, today=today)
    assert [(r.location_id, r.location_name) for r in found] == [(locs["lib"].id, "總圖書館")]
    assert {e.event_id for e in found[0].events} == {a.id, b.id}


async def test_recurrence_merged_event_counts_once(session: AsyncSession) -> None:
    locs = await _locations(session)
    today = date(2026, 11, 5)
    target = await _event(session, "停電", locs["lib"], today - timedelta(days=3))
    merged = await _event(session, "停電（重複回報）", locs["alias"], today - timedelta(days=1))
    assert len(await find_recurrences(session, today=today)) == 1

    await _merge(session, merged, target)
    assert await find_recurrences(session, today=today) == []


async def test_cri_skips_merged_events(session: AsyncSession) -> None:
    locs = await _locations(session)
    target = await _event(session, "a", locs["lib"], MONDAY)
    merged = await _event(session, "b", locs["lib"], MONDAY)
    await _merge(session, merged, target)

    events, _ = await load_records(session, start=MONDAY - timedelta(days=1))
    assert [e.event_id for e in events] == [target.id]


async def test_forecast_evidence_uses_representative(session: AsyncSession) -> None:
    locs = await _locations(session)
    target = await _event(session, "a", locs["lib"], MONDAY)
    alias = await _event(session, "b", locs["alias"], MONDAY + timedelta(days=1))
    merged = await _event(session, "c", locs["alias"], MONDAY + timedelta(days=2))
    await _event(session, "d", locs["dorm"], MONDAY)
    await _merge(session, merged, target)

    anomaly = Anomaly(MONDAY, 2, 0.5, 0.0001)
    signal = Signal("s1", "facility", "總圖書館", (), anomaly)
    picked = await _period_events(session, signal, "week")
    assert [(e.id, name) for e, name in picked] == [
        (target.id, "總圖書館"),
        (alias.id, "總圖書館"),
    ]
    overall = Signal("s2", "facility", None, (), anomaly)
    assert merged.id not in {e.id for e, _ in await _period_events(session, overall, "week")}


async def test_cri_repeat_uses_representative(session: AsyncSession) -> None:
    locs = await _locations(session)
    a = await _event(session, "a", locs["lib"], MONDAY)
    b = await _event(session, "b", locs["alias"], MONDAY)
    c = await _event(session, "c", locs["dorm"], MONDAY)

    events, _ = await load_records(session, start=MONDAY - timedelta(days=1))
    by_id = {e.event_id: e.location_id for e in events}
    assert by_id[a.id] == by_id[b.id] == locs["lib"].id
    assert by_id[c.id] == locs["dorm"].id
