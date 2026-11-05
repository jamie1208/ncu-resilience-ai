"""P8-T14：`config/locations.yaml` 的格式（`app/locations/seed.py`）。

- 只允許 name／code／aliases／parent 欄位；
- 名稱與別名全域不重複，不含「全校」「校外」「不明」；
- parent 必須是清單中的名稱。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.locations.seed import (
    DEFAULT_LIST,
    LocationListError,
    load_location_list,
    parse_location_list,
)


def doc(*locations: dict[str, Any], version: Any = "v1") -> dict[str, Any]:
    return {"list_version": version, "locations": list(locations)}


def test_repo_list_is_valid() -> None:
    listing = load_location_list(DEFAULT_LIST)
    assert listing.version
    names = [n for loc in listing.locations for n in (loc.name, *loc.aliases)]
    assert len(names) == len(set(names))
    by_name = {loc.name: loc for loc in listing.locations}
    # 使用者 2026-11-05 確認的決定
    assert "中正圖書館" in by_name["總圖書館"].aliases
    assert {"K書中心", "校史館", "校史館旁"} <= by_name.keys()
    assert by_name["工程五館"].code == "E6"


def test_parses_fields() -> None:
    listing = parse_location_list(
        doc(
            {"name": "宿舍區"},
            {"name": "學生男九舍", "code": "D9", "aliases": ["男九舍"], "parent": "宿舍區"},
        )
    )
    assert listing.version == "v1"
    dorms, d9 = listing.locations
    assert (dorms.name, dorms.code, dorms.aliases, dorms.parent) == ("宿舍區", None, (), None)
    assert (d9.name, d9.code, d9.aliases, d9.parent) == ("學生男九舍", "D9", ("男九舍",), "宿舍區")


@pytest.mark.parametrize(
    "data",
    [
        doc({"name": "工程五館"}, {"name": "工程五館"}),  # 名稱重複
        doc({"name": "工程五館", "aliases": ["工五"]}, {"name": "工五"}),  # 別名與名稱重複
        doc({"name": "工程五館", "aliases": ["工五"]}, {"name": "工程一館", "aliases": ["工五"]}),
        doc({"name": "工程五館", "aliases": ["工五", "工五"]}),  # 同一地點的別名重複
        doc({"name": "工程五館", "aliases": ["工程五館"]}),  # 別名等於自己的名稱
    ],
)
def test_names_and_aliases_are_globally_unique(data: dict[str, Any]) -> None:
    with pytest.raises(LocationListError):
        parse_location_list(data)


@pytest.mark.parametrize("special", ["全校", "校外", "不明"])
def test_special_names_are_rejected(special: str) -> None:
    with pytest.raises(LocationListError):
        parse_location_list(doc({"name": special}))
    with pytest.raises(LocationListError):
        parse_location_list(doc({"name": "工程五館", "aliases": [special]}))


def test_parent_must_be_in_list() -> None:
    with pytest.raises(LocationListError):
        parse_location_list(doc({"name": "學生男九舍", "parent": "宿舍區"}))
    with pytest.raises(LocationListError):  # 別名不能當上層地點
        parse_location_list(
            doc({"name": "宿舍區", "aliases": ["宿舍"]}, {"name": "學生男九舍", "parent": "宿舍"})
        )
    with pytest.raises(LocationListError):  # 不能是自己的上層
        parse_location_list(doc({"name": "宿舍區", "parent": "宿舍區"}))


@pytest.mark.parametrize(
    "data",
    [
        doc({"name": "工程五館", "lat": 24.9}),  # 不允許的欄位
        doc({"aliases": ["工五"]}),  # 缺 name
        doc({"name": ""}),
        doc({"name": "  "}),
        doc({"name": "工程五館", "aliases": "工五"}),  # aliases 必須是清單
        doc({"name": "工程五館", "aliases": [""]}),
        doc({"name": "工程五館", "code": 6}),
        doc({"name": "工程五館"}, version=None),  # 缺 list_version
        {"locations": [{"name": "工程五館"}]},
        {"list_version": "v1", "locations": [{"name": "工程五館"}], "extra": 1},
        {"list_version": "v1", "locations": "工程五館"},
        {"list_version": "v1", "locations": ["工程五館"]},
        ["工程五館"],
    ],
)
def test_malformed_entries_are_rejected(data: Any) -> None:
    with pytest.raises(LocationListError):
        parse_location_list(data)
