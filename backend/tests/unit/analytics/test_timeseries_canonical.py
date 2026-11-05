"""P8-T03：時序統計依代表地點分組（ADR-0018 決策 1-5）。

`representative()` 與 migration 0021 的 views 規則相同：沿著 `canonical_id` 找到最上層的代表
地點（循環時維持自己），`parent_id` 不併入。別名的事件以代表地點名稱分桶後，件數與地點分布
合併在代表地點底下。
"""

from __future__ import annotations

import uuid
from datetime import date

from app.analytics.timeseries import (
    EventPoint,
    LocationRef,
    bucket,
    count_series,
    location_distribution,
    representative,
)

LIB = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
LIB_ALIAS = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
LIB_ALIAS2 = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
DORM_AREA = uuid.UUID("00000000-0000-0000-0000-0000000000c4")
DORM_7 = uuid.UUID("00000000-0000-0000-0000-0000000000c5")
LOOP_A = uuid.UUID("00000000-0000-0000-0000-0000000000c6")
LOOP_B = uuid.UUID("00000000-0000-0000-0000-0000000000c7")

LOCATIONS = {
    LIB: LocationRef(LIB, "總圖書館"),
    LIB_ALIAS: LocationRef(LIB_ALIAS, "總圖", canonical_id=LIB),
    LIB_ALIAS2: LocationRef(LIB_ALIAS2, "圖書館", canonical_id=LIB_ALIAS),
    DORM_AREA: LocationRef(DORM_AREA, "宿舍區"),
    DORM_7: LocationRef(DORM_7, "女十四舍", parent_id=DORM_AREA),
    LOOP_A: LocationRef(LOOP_A, "甲", canonical_id=LOOP_B),
    LOOP_B: LocationRef(LOOP_B, "乙", canonical_id=LOOP_A),
}
MONDAY = date(2026, 1, 5)


def _name(location_id: uuid.UUID | None) -> str | None:
    if location_id is None:
        return None
    rep = representative(location_id, LOCATIONS)
    return rep.name if rep is not None else None


def test_representative_follows_aliases() -> None:
    assert representative(LIB, LOCATIONS) == LOCATIONS[LIB]
    assert representative(LIB_ALIAS, LOCATIONS) == LOCATIONS[LIB]
    assert representative(LIB_ALIAS2, LOCATIONS) == LOCATIONS[LIB]


def test_representative_ignores_parent() -> None:
    assert representative(DORM_7, LOCATIONS) == LOCATIONS[DORM_7]


def test_representative_cycle_and_unknown() -> None:
    assert representative(LOOP_A, LOCATIONS) == LOCATIONS[LOOP_A]
    assert representative(uuid.UUID(int=1), LOCATIONS) is None


def test_alias_counts_merge_into_representative() -> None:
    points = [
        EventPoint(MONDAY, "theft", _name(LIB)),
        EventPoint(MONDAY, "theft", _name(LIB_ALIAS)),
        EventPoint(date(2026, 1, 7), "theft", _name(LIB_ALIAS2)),
        EventPoint(MONDAY, "theft", _name(DORM_7)),
        EventPoint(MONDAY, "theft", _name(DORM_AREA)),
    ]
    rows = bucket(points, "week")
    assert {(r.location_name, r.count) for r in rows} == {
        ("總圖書館", 3),
        ("女十四舍", 1),
        ("宿舍區", 1),
    }
    series = count_series(rows, "week", MONDAY, MONDAY, location="總圖書館")
    assert [s.count for s in series] == [3]
    shares = location_distribution(rows, MONDAY, MONDAY)
    assert [(s.location, s.count) for s in shares] == [
        ("總圖書館", 3),
        ("女十四舍", 1),
        ("宿舍區", 1),
    ]
