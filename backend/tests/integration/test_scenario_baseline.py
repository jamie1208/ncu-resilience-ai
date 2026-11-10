"""P8-T09：What-if 依時間窗、類別、代表地點自動計算歷史基準件數（v1.0 D29）。

- 基準 = 歷史期間（最多 52 個完整週，資料較短時從最早一週起算）每週件數 × 時間窗天數 / 7；
  件數來自 P6-T03 的週 materialized view，和 `count_series` 的結果一致（不另算一套）；
- 地點依代表地點（別名算在代表地點底下）；不實事件、被合併的事件不列入（沿用 views）；
- 進行中的這一週不列入；沒有任何歷史資料時沒有基準，送出時必須手填；
- 手改值優先：送出 `baseline` 時用手填值，沒送時才自動計算，並記錄來源與依據；
- `GET /api/v1/admin/scenarios/baseline` 只在管理端（events:read_all），未登入回 UNAUTHORIZED。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.timeseries import count_series, load_counts, period_start
from app.models.event import Event, Location
from app.models.scenario import ScenarioRun
from app.resilience.recurrence import TAIPEI
from app.simulations.baseline import HISTORY_WEEKS, history_range, load_baseline
from tests.integration.test_scenarios_api import EXPLANATION, URL, Harness, body

pytestmark = pytest.mark.anyio

AS_OF = date(2026, 6, 17)  # 週三
THIS_WEEK = period_start(AS_OF, "week")  # 2026-06-15
LAST_SUNDAY = THIS_WEEK - timedelta(days=1)


@pytest.fixture()
def harness(session: AsyncSession) -> Harness:
    return Harness(session)


async def _locations(session: AsyncSession) -> dict[str, Location]:
    lib = Location(name="總圖書館")
    session.add(lib)
    await session.flush()
    alias = Location(name="總圖", canonical_id=lib.id)
    other = Location(name="工程二館")
    session.add_all([alias, other])
    await session.flush()
    return {"lib": lib, "alias": alias, "other": other}


async def _event(
    session: AsyncSession,
    day: date,
    *,
    loc: Location | None = None,
    type_: str = "facility",
    status: str = "unverified",
) -> Event:
    e = Event(
        title="事件",
        type=type_,
        status=status,
        location_id=loc.id if loc else None,
        occurrence_start_date=day,
    )
    session.add(e)
    await session.flush()
    return e


def test_history_range_full_and_short() -> None:
    start, end = history_range(AS_OF, date(2020, 1, 1)) or (None, None)
    assert end == LAST_SUNDAY
    assert start == THIS_WEEK - timedelta(weeks=HISTORY_WEEKS)
    # 資料較短：從最早一筆所在的週一起算
    assert history_range(AS_OF, date(2026, 5, 6)) == (date(2026, 5, 4), LAST_SUNDAY)
    # 只有這一週的資料、沒有資料：沒有歷史期間
    assert history_range(AS_OF, AS_OF) is None
    assert history_range(AS_OF, None) is None


async def test_baseline_matches_p6_t03_counts(session: AsyncSession) -> None:
    locs = await _locations(session)
    first = THIS_WEEK - timedelta(weeks=10)
    for week in range(10):
        day = first + timedelta(weeks=week, days=2)
        await _event(session, day, loc=locs["lib"])
        if week % 2 == 0:
            await _event(session, day, loc=locs["alias"])  # 別名算在代表地點
        await _event(session, day, loc=locs["other"])
        await _event(session, day, loc=locs["lib"], type_="security")
    await _event(session, first, loc=locs["lib"], status="false")  # 不實不列入
    await _event(session, AS_OF, loc=locs["lib"])  # 進行中的週不列入

    result = await load_baseline(
        session, as_of=AS_OF, window_days=28, event_type="facility", location="總圖"
    )

    rows = await load_counts(session, "week")
    series = count_series(
        rows, "week", first, LAST_SUNDAY, event_type="facility", location="總圖書館"
    )
    assert result.location == "總圖書館"
    assert result.weeks == len(series) == 10
    assert result.observed == sum(p.count for p in series) == 15
    assert result.history_start == first
    assert result.history_end == LAST_SUNDAY
    assert result.baseline == pytest.approx(15 / 10 * 4)

    everything = await load_baseline(session, as_of=AS_OF, window_days=7)
    assert everything.observed == sum(r.count for r in rows if r.period_start < THIS_WEEK) == 35
    assert everything.baseline == pytest.approx(3.5)


async def test_baseline_without_history(session: AsyncSession) -> None:
    result = await load_baseline(session, as_of=AS_OF, window_days=90)
    assert result.baseline is None
    assert result.observed == 0
    assert result.weeks == 0


async def test_unknown_location_counts_zero(session: AsyncSession) -> None:
    await _event(session, THIS_WEEK - timedelta(weeks=2))
    result = await load_baseline(session, as_of=AS_OF, window_days=14, location="不存在的地點")
    assert result.location == "不存在的地點"
    assert result.observed == 0
    assert result.baseline == 0


def _today() -> date:
    return datetime.now(TAIPEI).date()


async def _history(session: AsyncSession) -> None:
    """過去 4 週每週 2 件 facility（不含本週）。"""
    this_week = period_start(_today(), "week")
    for week in range(1, 5):
        for _ in range(2):
            await _event(session, this_week - timedelta(weeks=week))


async def test_api_baseline(harness: Harness, session: AsyncSession) -> None:
    await _history(session)
    async with harness.client() as c:
        r = await c.get(f"{URL}/baseline", params={"window_days": 14, "event_type": "facility"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["baseline"] == pytest.approx(4.0)
    assert data["observed"] == 8
    assert data["weeks"] == 4
    assert data["window_days"] == 14
    assert data["event_type"] == "facility"
    assert data["location"] is None


async def test_api_baseline_validation_and_auth(harness: Harness) -> None:
    async with harness.client() as c:
        r = await c.get(f"{URL}/baseline", params={"window_days": 0})
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    async with harness.client(logged_in=False) as c:
        r = await c.get(f"{URL}/baseline", params={"window_days": 30})
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_create_without_baseline_uses_auto(harness: Harness, session: AsyncSession) -> None:
    await _history(session)
    harness.script(EXPLANATION)
    request = body(window_days=14, location=None)
    del request["baseline"]
    async with harness.client() as c:
        r = await c.post(URL, json=request)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["output"]["baseline"] == pytest.approx(4.0)
    assert data["input"]["baseline"] == pytest.approx(4.0)
    assert data["input"]["baseline_source"] == "auto"
    assert data["input"]["baseline_basis"]["observed"] == 8
    assert data["input"]["baseline_basis"]["weeks"] == 4


async def test_manual_baseline_wins(harness: Harness, session: AsyncSession) -> None:
    await _history(session)
    harness.script(EXPLANATION)
    async with harness.client() as c:
        r = await c.post(URL, json=body(baseline=20, window_days=14, location=None))
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["output"]["baseline"] == 20
    assert data["input"]["baseline_source"] == "manual"
    assert data["input"].get("baseline_basis") is None


async def test_create_without_baseline_and_history_is_rejected(
    harness: Harness, session: AsyncSession
) -> None:
    request = body()
    del request["baseline"]
    async with harness.client() as c:
        r = await c.post(URL, json=request)
    error = r.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "body.baseline"
    assert (await session.scalars(select(ScenarioRun))).first() is None
