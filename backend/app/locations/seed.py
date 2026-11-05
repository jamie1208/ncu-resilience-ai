"""校園地點清單匯入（P8-T14，接續 P8-T02）：`config/locations.yaml` → locations。

- 清單名稱建立或沿用為代表地點（原本是別名時改回代表地點），別名建立或改指向該代表地點，
  `parent` 設 `parent_id`（清單沒寫 parent 時不清除既有的上層關係）。
- 既有代表地點成為別名時，原本指向它的別名與下層地點依 P8-T02 的攤平規則改指向新的代表地點。
- 使用者拆開過（最新一筆紀錄為 split）的地點不覆蓋；清單外的地點不刪除、不改動。
- 有變動的地點各寫一筆 location_resolutions（method manual、decided_by `seed`）；清單內沒有
  任何判斷紀錄的既有地點也補一筆，讓補判跳過匯入過的地點。可重跑，第二次不再寫入紀錄。
- 稽核 `location.seed` 只記數量與 list_version。
- 平面圖代號 `code` 只保存在清單中（locations 沒有對應欄位）。

匯入後清單內的名稱由 `resolve_location` 同名稱直接沿用、不呼叫 S13；清單外的新名稱照 P8-T02
由 S13 從代表地點（含清單）判斷。命令列：`uv run python -m app.locations.seed [清單路徑]`。

本模組只 flush、不 commit（命令列除外）。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal, Role
from app.locations.resolver import MAX_NAME_CHARS, SPECIAL_NAMES, UNKNOWN, _repoint
from app.models.event import Location, LocationResolution
from app.services.audit import record_audit

DEFAULT_LIST = Path(__file__).resolve().parents[3] / "config" / "locations.yaml"
SEED_ACTOR = "seed"
SEED_PRINCIPAL = Principal(kind="service", subject=SEED_ACTOR, roles=frozenset({Role.SERVICE_AI}))
FIELDS = frozenset({"name", "code", "aliases", "parent"})
RESERVED = SPECIAL_NAMES | {UNKNOWN}


class LocationListError(ValueError):
    """地點清單格式錯誤。"""


@dataclass(frozen=True)
class ListedLocation:
    """清單中的一個代表地點。"""

    name: str
    code: str | None
    aliases: tuple[str, ...]
    parent: str | None


@dataclass(frozen=True)
class LocationList:
    """`config/locations.yaml` 的內容。"""

    version: str
    locations: tuple[ListedLocation, ...]


@dataclass(frozen=True)
class SeedResult:
    """一次匯入的數量。"""

    created: int
    updated: int
    recorded: int
    skipped_split: int


def _name(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocationListError(f"{where} 必須是非空字串")
    name = value.strip()
    if len(name) > MAX_NAME_CHARS:
        raise LocationListError(f"{where} 超過 {MAX_NAME_CHARS} 字：{name}")
    if name in RESERVED:
        raise LocationListError(f"{where} 不能是特殊值「{name}」")
    return name


def parse_location_list(data: Any) -> LocationList:
    """驗證並轉換清單；格式錯誤時拋出 `LocationListError`。"""
    if not isinstance(data, dict) or set(data) != {"list_version", "locations"}:
        raise LocationListError("清單只能有 list_version 與 locations 兩個欄位")
    version = data["list_version"]
    if not isinstance(version, str) or not version.strip():
        raise LocationListError("list_version 必須是非空字串")
    entries = data["locations"]
    if not isinstance(entries, list):
        raise LocationListError("locations 必須是清單")
    seen: set[str] = set()
    parsed: list[ListedLocation] = []
    for i, entry in enumerate(entries):
        where = f"locations[{i}]"
        if not isinstance(entry, dict):
            raise LocationListError(f"{where} 必須是物件")
        extra = set(entry) - FIELDS
        if extra:
            raise LocationListError(f"{where} 有不允許的欄位：{sorted(extra)}")
        name = _name(entry.get("name"), f"{where}.name")
        code = entry.get("code")
        if code is not None and (not isinstance(code, str) or not code.strip()):
            raise LocationListError(f"{where}.code 必須是非空字串")
        raw_aliases = entry.get("aliases", [])
        if not isinstance(raw_aliases, list):
            raise LocationListError(f"{where}.aliases 必須是清單")
        aliases = tuple(_name(a, f"{where}.aliases") for a in raw_aliases)
        parent = entry.get("parent")
        if parent is not None:
            parent = _name(parent, f"{where}.parent")
        for n in (name, *aliases):
            if n in seen:
                raise LocationListError(f"名稱或別名重複：{n}")
            seen.add(n)
        parsed.append(ListedLocation(name, code.strip() if code else None, aliases, parent))
    canonical = {loc.name for loc in parsed}
    for loc in parsed:
        if loc.parent is not None and (loc.parent not in canonical or loc.parent == loc.name):
            raise LocationListError(f"{loc.name} 的 parent 必須是清單中其他地點的名稱")
    return LocationList(version.strip(), tuple(parsed))


def load_location_list(path: Path = DEFAULT_LIST) -> LocationList:
    """讀取並驗證地點清單。"""
    with path.open(encoding="utf-8") as f:
        return parse_location_list(yaml.safe_load(f))


class _Seeder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.created: set[uuid.UUID] = set()
        self.changed: set[uuid.UUID] = set()
        self.recorded = 0
        self.skipped: set[uuid.UUID] = set()

    async def find_or_create(self, name: str) -> Location:
        # 與 resolve_location 相同：同名稱取 id 最小的一筆
        loc = await self.session.scalar(
            select(Location).where(Location.name == name).order_by(Location.id).limit(1)
        )
        if loc is None:
            loc = Location(name=name)
            self.session.add(loc)
            await self.session.flush()
            self.created.add(loc.id)
        else:
            await self.session.refresh(loc)
        return loc

    async def is_split(self, loc: Location) -> bool:
        latest = await self.session.scalar(
            select(LocationResolution.decision)
            .where(LocationResolution.location_id == loc.id)
            .order_by(LocationResolution.created_at.desc(), LocationResolution.id.desc())
            .limit(1)
        )
        if latest == "split":
            self.skipped.add(loc.id)
            return True
        return False

    async def has_record(self, loc: Location) -> bool:
        return (
            await self.session.scalar(
                select(LocationResolution.id)
                .where(LocationResolution.location_id == loc.id)
                .limit(1)
            )
        ) is not None

    async def record(
        self, loc: Location, changed: bool, decision: str, target: uuid.UUID | None
    ) -> None:
        if changed and loc.id not in self.created:
            self.changed.add(loc.id)
        if not changed and await self.has_record(loc):
            return
        self.session.add(
            LocationResolution(
                location_id=loc.id,
                decision=decision,
                target_id=target,
                method="manual",
                decided_by=SEED_ACTOR,
                created_at=datetime.now(UTC),
            )
        )
        self.recorded += 1
        await self.session.flush()


async def seed_locations(
    session: AsyncSession,
    listing: LocationList,
    *,
    principal: Principal = SEED_PRINCIPAL,
    request_id: str | None = None,
) -> SeedResult:
    """匯入地點清單（flush，不 commit）；可重跑且結果相同。"""
    seeder = _Seeder(session)
    anchors: dict[str, Location] = {}
    # 先確定所有代表地點，別名與上層地點才有對象可以指向
    for entry in listing.locations:
        anchors[entry.name] = await seeder.find_or_create(entry.name)
    for entry in listing.locations:
        loc = anchors[entry.name]
        if await seeder.is_split(loc):
            continue
        changed = loc.canonical_id is not None
        loc.canonical_id = None
        parent = anchors[entry.parent].id if entry.parent else None
        if parent is not None and loc.parent_id != parent:
            loc.parent_id = parent
            changed = True
        await session.flush()
        if loc.parent_id is not None and parent is not None:
            await seeder.record(loc, changed, "inside_of", parent)
        else:
            await seeder.record(loc, changed, "new", None)
    for entry in listing.locations:
        anchor = anchors[entry.name].id
        for alias in entry.aliases:
            loc = await seeder.find_or_create(alias)
            if await seeder.is_split(loc):
                continue
            changed = loc.canonical_id != anchor
            if changed:
                loc.canonical_id = anchor
                await session.flush()
                await _repoint(session, loc.id, anchor)
            await seeder.record(loc, changed, "alias_of", anchor)
    result = SeedResult(
        created=len(seeder.created),
        updated=len(seeder.changed),
        recorded=seeder.recorded,
        skipped_split=len(seeder.skipped),
    )
    await record_audit(
        session,
        principal,
        "location.seed",
        target="locations",
        request_id=request_id,
        metadata={"list_version": listing.version, **vars(result)},
    )
    await session.flush()
    return result


async def _seed_in_worker(path: Path) -> tuple[LocationList, SeedResult]:
    from app.worker.tasks.ingest import open_sessionmaker

    listing = load_location_list(path)
    async with open_sessionmaker() as maker, maker() as session:
        result = await seed_locations(session, listing, request_id=f"seed-{uuid.uuid4().hex[:12]}")
        await session.commit()
    return listing, result


def main(argv: Sequence[str] | None = None) -> int:
    """命令列：`python -m app.locations.seed [清單路徑]`。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print("用法：python -m app.locations.seed [清單路徑]")
        return 2
    try:
        listing, result = asyncio.run(_seed_in_worker(Path(args[0]) if args else DEFAULT_LIST))
    except LocationListError as e:
        print(f"清單格式錯誤：{e}")
        return 1
    print(
        f"地點清單 {listing.version}：新增 {result.created}、更新 {result.updated}、"
        f"寫入紀錄 {result.recorded}、略過拆開過的 {result.skipped_split}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_LIST",
    "SEED_ACTOR",
    "ListedLocation",
    "LocationList",
    "LocationListError",
    "SeedResult",
    "load_location_list",
    "parse_location_list",
    "seed_locations",
]
