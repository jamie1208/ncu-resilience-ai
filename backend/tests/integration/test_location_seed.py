"""P8-T14：校園地點清單匯入（`app/locations/seed.py`，接續 P8-T02）。

- 清單名稱建立或沿用為代表地點，別名建立或改指向它，`parent` 設 `parent_id`；
- 匯入後同名稱直接沿用、不呼叫 S13；清單外的新名稱仍由 S13 從代表地點（含清單）判斷；
- 既有代表地點成為別名時，其別名與下層地點攤平到新的代表地點；
- 使用者拆開過（最新一筆為 split）的地點不覆蓋；清單外的地點保留；
- 有變動的地點各寫一筆 location_resolutions（manual、decided_by `seed`）；重跑結果相同；
- 補判跳過匯入過的地點；稽核 `location.seed` 只記數量與 list_version。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.locations.resolver import backfill_locations, resolve_location, split_location
from app.locations.seed import (
    DEFAULT_LIST,
    SEED_ACTOR,
    LocationList,
    load_location_list,
    parse_location_list,
    seed_locations,
)
from app.models import AuditLog
from app.models.event import Location, LocationResolution
from tests.integration.test_location_resolver import (
    ADMIN,
    SERVICE,
    Resolver,
    add_location,
    answer,
    article,
    resolutions,
)

pytestmark = pytest.mark.anyio

LISTING = parse_location_list(
    {
        "list_version": "test-1",
        "locations": [
            {"name": "宿舍區"},
            {"name": "工程五館", "code": "E6", "aliases": ["工五", "工五館"]},
            {"name": "學生男九舍", "aliases": ["男九舍"], "parent": "宿舍區"},
        ],
    }
)


async def by_name(session: AsyncSession, name: str) -> Location:
    loc = await session.scalar(select(Location).where(Location.name == name))
    assert loc is not None, name
    await session.refresh(loc)
    return loc


async def seed(session: AsyncSession, listing: LocationList = LISTING) -> Any:
    result = await seed_locations(session, listing)
    await session.commit()
    return result


async def snapshot(session: AsyncSession) -> dict[str, tuple[str | None, str | None]]:
    """名稱 → (代表地點名稱, 上層地點名稱)。"""
    locs = {loc.id: loc for loc in await session.scalars(select(Location))}
    for loc in locs.values():
        await session.refresh(loc)
    return {
        loc.name: (
            locs[loc.canonical_id].name if loc.canonical_id else None,
            locs[loc.parent_id].name if loc.parent_id else None,
        )
        for loc in locs.values()
    }


async def count(session: AsyncSession, model: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def record(loc: Location, decision: str, target: Location | None, method: str = "ai") -> Any:
    return LocationResolution(
        location_id=loc.id,
        decision=decision,
        target_id=target.id if target else None,
        method=method,
        decided_by="pipeline",
        created_at=datetime.now(UTC),
    )


# ---------- 匯入 ----------


async def test_seed_creates_canonical_aliases_and_parents(session: AsyncSession) -> None:
    result = await seed(session)

    assert await snapshot(session) == {
        "宿舍區": (None, None),
        "工程五館": (None, None),
        "工五": ("工程五館", None),
        "工五館": ("工程五館", None),
        "學生男九舍": (None, "宿舍區"),
        "男九舍": ("學生男九舍", None),
    }
    assert result.created == 6
    e6 = await by_name(session, "工程五館")
    d9 = await by_name(session, "學生男九舍")
    dorms = await by_name(session, "宿舍區")
    alias = await by_name(session, "工五")
    for loc, decision, target in (
        (e6, "new", None),
        (d9, "inside_of", dorms.id),
        (alias, "alias_of", e6.id),
    ):
        (r,) = await resolutions(session, loc)
        assert (r.decision, r.target_id, r.method, r.decided_by, r.ai_run_id) == (
            decision,
            target,
            "manual",
            SEED_ACTOR,
            None,
        )
    audit = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "location.seed"))
    ).one()
    assert audit.meta == {
        "list_version": "test-1",
        "created": 6,
        "updated": 0,
        "recorded": 6,
        "skipped_split": 0,
    }


async def test_seeded_names_are_reused_without_calling_s13(session: AsyncSession) -> None:
    await seed(session)
    run = Resolver(session)

    for name in ("工程五館", "工五", "男九舍"):
        location_id = await resolve_location(
            session, name, evidence=[article()], run=run, principal=SERVICE
        )
        assert location_id == (await by_name(session, name)).id

    assert run.calls == 0
    assert await count(session, Location) == 6


async def test_new_name_outside_list_is_judged_against_seeded_locations(
    session: AsyncSession,
) -> None:
    await seed(session)
    run = Resolver(
        session,
        lambda: answer(
            "alias_of",
            next(
                c["location_id"] for c in run.payloads[0]["candidates"] if c["name"] == "工程五館"
            ),
            quote="工五二樓走廊",
        ),
    )

    location_id = await resolve_location(
        session, "工學院館", evidence=[article()], run=run, principal=SERVICE
    )
    await session.commit()

    assert run.calls == 1
    candidates = {c["name"]: c for c in run.payloads[0]["candidates"]}
    assert candidates.keys() == {"宿舍區", "工程五館", "學生男九舍"}  # 只列代表地點
    assert candidates["工程五館"]["aliases"] == ["工五", "工五館"]
    assert candidates["學生男九舍"]["parent"] == "宿舍區"
    new = await session.get(Location, location_id)
    assert new is not None
    assert new.canonical_id == (await by_name(session, "工程五館")).id
    # 之後同名稱直接沿用
    rerun = Resolver(session)
    assert (
        await resolve_location(
            session, "工學院館", evidence=[article()], run=rerun, principal=SERVICE
        )
        == location_id
    )
    assert rerun.calls == 0


async def test_rerun_is_stable(session: AsyncSession) -> None:
    await seed(session)
    state = await snapshot(session)
    records = await count(session, LocationResolution)

    result = await seed(session)

    assert await snapshot(session) == state
    assert await count(session, LocationResolution) == records
    assert (result.created, result.updated, result.recorded) == (0, 0, 0)


async def test_repo_list_seeds_and_reruns(session: AsyncSession) -> None:
    listing = load_location_list(DEFAULT_LIST)
    first = await seed(session, listing)
    names = sum(1 + len(loc.aliases) for loc in listing.locations)
    assert first.created == names
    assert await count(session, Location) == names

    second = await seed(session, listing)
    assert (second.created, second.updated, second.recorded) == (0, 0, 0)
    assert (await by_name(session, "中正圖書館")).canonical_id == (
        await by_name(session, "總圖書館")
    ).id


# ---------- 既有地點 ----------


async def test_existing_names_are_reused_and_corrected(session: AsyncSession) -> None:
    # 匯入前 S13 把「工程五館」判成「工五」的別名；「男九舍」是沒有判斷紀錄的代表地點
    middle = await add_location(session, "工五")
    e6 = await add_location(session, "工程五館", canonical_id=middle.id)
    d9 = await add_location(session, "男九舍")
    session.add_all([record(middle, "new", None), record(e6, "alias_of", middle)])
    await session.commit()

    result = await seed(session)

    assert await snapshot(session) == {
        "宿舍區": (None, None),
        "工程五館": (None, None),
        "工五": ("工程五館", None),
        "工五館": ("工程五館", None),
        "學生男九舍": (None, "宿舍區"),
        "男九舍": ("學生男九舍", None),
    }
    # 沿用既有的 id，不重複建立
    assert (await by_name(session, "工程五館")).id == e6.id
    assert (await by_name(session, "工五")).id == middle.id
    assert (await by_name(session, "男九舍")).id == d9.id
    assert (result.created, result.updated) == (3, 3)
    latest = (await resolutions(session, e6))[-1]
    assert (latest.decision, latest.method, latest.decided_by) == ("new", "manual", SEED_ACTOR)


async def test_existing_canonical_becoming_alias_is_flattened(session: AsyncSession) -> None:
    # 「工五」原本是代表地點，有別名與下層地點；匯入後成為「工程五館」的別名
    middle = await add_location(session, "工五")
    other_alias = await add_location(session, "E6館", canonical_id=middle.id)
    child = await add_location(session, "工五二樓", parent_id=middle.id)
    session.add_all([record(other_alias, "alias_of", middle), record(child, "inside_of", middle)])
    await session.commit()

    await seed(session)

    state = await snapshot(session)
    assert state["工五"] == ("工程五館", None)
    assert state["E6館"] == ("工程五館", None)  # 不留下別名的別名
    assert state["工五二樓"] == (None, "工程五館")


async def test_split_locations_are_not_overwritten(session: AsyncSession) -> None:
    e6 = await add_location(session, "工程五館")
    alias = await add_location(session, "工五", canonical_id=e6.id)
    session.add(record(alias, "alias_of", e6))
    await session.flush()
    await split_location(session, ADMIN, alias.id)
    await session.commit()

    result = await seed(session)

    assert (await snapshot(session))["工五"] == (None, None)
    assert result.skipped_split == 1
    assert (await resolutions(session, alias))[-1].decision == "split"
    # 重跑仍不覆蓋
    assert (await seed(session)).skipped_split == 1
    assert (await snapshot(session))["工五"] == (None, None)


async def test_locations_outside_list_are_kept(session: AsyncSession) -> None:
    outside = await add_location(session, "游藝館前廣場")
    alias = await add_location(session, "游藝館廣場", canonical_id=outside.id)
    whole = await add_location(session, "全校")
    session.add(record(alias, "alias_of", outside))
    await session.commit()

    await seed(session)

    state = await snapshot(session)
    assert state["游藝館前廣場"] == (None, None)
    assert state["游藝館廣場"] == ("游藝館前廣場", None)
    assert state["全校"] == (None, None)
    assert await resolutions(session, outside) == []  # 清單外不寫紀錄
    assert await resolutions(session, whole) == []


async def test_backfill_skips_seeded_locations(session: AsyncSession) -> None:
    # 匯入前就存在、沒有判斷紀錄的清單地點，匯入後也有紀錄，補判不再判斷
    await add_location(session, "工程五館")
    outside = await add_location(session, "游藝館前廣場")
    await session.commit()

    result = await seed(session)
    assert result.recorded == 6
    run = Resolver(session)
    assert await backfill_locations(session, run=run, principal=SERVICE) == 1  # 只剩清單外的
    assert run.calls == 0  # 沒有事件、沒有證據，不呼叫
    assert [r.method for r in await resolutions(session, outside)] == ["fallback"]
