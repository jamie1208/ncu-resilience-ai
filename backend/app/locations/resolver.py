"""代表地點（P8-T02，ADR-0018 決策 1-5）：以 S13 location-resolver 判斷新的地點名稱。

- S02 抽出的地點名稱在資料庫中沒有完全相同的名稱時才呼叫 S13；同名稱直接沿用，不重複呼叫。
  「不明」與空白不建立地點；「全校」「校外」是 S02 的特殊值，直接建立、不判斷，也不當候選。
- 候選：既有的代表地點（沒有 canonical_id），依名稱（含別名）的字元重疊度挑最多 20 個，
  代號 L1…依序；附上別名與上層地點的名稱。沒有候選時不呼叫。
- 結果：alias_of → `canonical_id` 指向目標的代表地點（別名的別名攤平；地點本身成為別名時，
  原本指向它的別名與下層地點一併改指向新的代表地點）；inside_of → `parent_id` 指向目標的
  代表地點（上層只用於篩選，不併入計數）；new 不改。abstain、失敗或 target_id 不在候選中，
  一律當成新地點（method fallback）。
- 每次判斷新增一筆 location_resolutions；有呼叫 S13 時另寫稽核 `location.resolve`（只記決定、
  目標 id 與方法）。
- 拆開（`split_location`）：清除別名或上層關係，新增一筆 split 紀錄並寫稽核 `location.split`。
  之後同名稱沿用、補判跳過有紀錄的地點，所以不會再被自動合併。
- 補判（`backfill_locations`）：沒有任何判斷紀錄的既有地點各判一次（依名稱排序），證據取該地點
  最新事件最早加入的文章；可重跑，已有紀錄的地點不再判斷。命令列：
  `uv run python -m app.locations.resolver backfill`（使用正式的模型設定，受每日費用上限限制）。

本模組只 flush、不 commit；`run` 由呼叫端提供（管線用 `_Context.run`，執行紀錄由 Runner 寫入）。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.run import RunStatus, SkillRunResult
from app.core.security import Principal
from app.models.event import Event, EventEvidence, Location, LocationResolution
from app.services.audit import record_audit

S13 = "location-resolver"
UNKNOWN = "不明"
SPECIAL_NAMES = frozenset({"全校", "校外"})
MAX_CANDIDATES = 20
MAX_NAME_CHARS = 200

SkillCall = Callable[[Mapping[str, Any]], Awaitable[SkillRunResult]]


class LocationSplitError(Exception):
    """地點不能拆開（不是別名，也沒有上層地點）。"""


class LocationNotFoundError(LocationSplitError):
    """地點不存在。"""


async def canonical_id_of(session: AsyncSession, location_id: uuid.UUID) -> uuid.UUID:
    """地點的代表地點 id（自己就是代表地點時回傳自己）。"""
    canonical = await session.scalar(
        select(Location.canonical_id).where(Location.id == location_id)
    )
    return canonical or location_id


def _similarity(a: str, b: str) -> float:
    """名稱的字元重疊度（Jaccard）；只用來挑候選與排序，不是判斷依據。"""
    sa, sb = set(a.replace(" ", "")), set(b.replace(" ", ""))
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


async def _candidates(
    session: AsyncSession, name: str, exclude: uuid.UUID
) -> list[tuple[Location, list[str], str | None]]:
    """名稱最像的代表地點，附上別名與上層地點名稱。"""
    canonical = list(
        await session.scalars(
            select(Location).where(
                Location.canonical_id.is_(None),
                Location.id != exclude,
                Location.name.not_in(list(SPECIAL_NAMES | {UNKNOWN})),
            )
        )
    )
    if not canonical:
        return []
    ids = [c.id for c in canonical]
    aliases: dict[uuid.UUID, list[str]] = {}
    for alias_name, canonical_id in await session.execute(
        select(Location.name, Location.canonical_id)
        .where(Location.canonical_id.in_(ids), Location.id != exclude)
        .order_by(Location.name)
    ):
        if canonical_id is not None:
            aliases.setdefault(canonical_id, []).append(alias_name)
    parents = {
        loc_id: parent_name
        for loc_id, parent_name in await session.execute(
            select(Location.id, Location.name).where(
                Location.id.in_([c.parent_id for c in canonical if c.parent_id])
            )
        )
    }

    def score(loc: Location) -> float:
        return max(_similarity(name, n) for n in [loc.name, *aliases.get(loc.id, [])])

    ranked = sorted(canonical, key=lambda c: (-score(c), c.name, str(c.id)))[:MAX_CANDIDATES]
    return [
        (c, aliases.get(c.id, [])[:20], parents.get(c.parent_id) if c.parent_id else None)
        for c in ranked
    ]


async def _record(
    session: AsyncSession,
    principal: Principal,
    loc: Location,
    decision: str,
    method: str,
    *,
    target: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> None:
    session.add(
        LocationResolution(
            location_id=loc.id,
            decision=decision,
            target_id=target,
            method=method,
            ai_run_id=run_id,
            decided_by=principal.actor,
            # 應用端時間：同一個交易內的多筆紀錄也能依時間排序
            created_at=datetime.now(UTC),
        )
    )
    if run_id is None:  # 沒有呼叫 S13（特殊值、沒有候選或沒有證據）：只留判斷紀錄
        await session.flush()
        return
    await record_audit(
        session,
        principal,
        "location.resolve",
        target=f"location:{loc.id}",
        request_id=request_id,
        metadata={
            "decision": decision,
            "target_id": str(target) if target else None,
            "method": method,
        },
    )
    await session.flush()


async def _repoint(session: AsyncSession, old: uuid.UUID, anchor: uuid.UUID) -> None:
    """`old` 成為別名後，原本指向它的別名與下層地點改指向 `anchor`，不留下別名的別名。"""
    await session.execute(
        update(Location).where(Location.canonical_id == old).values(canonical_id=anchor)
    )
    await session.execute(
        update(Location).where(Location.parent_id == old).values(parent_id=anchor)
    )


async def _judge(
    session: AsyncSession,
    loc: Location,
    evidence: Sequence[Mapping[str, Any]],
    *,
    run: SkillCall | None,
    principal: Principal,
    request_id: str | None,
) -> None:
    """以 S13 判斷 `loc` 與既有代表地點的關係，套用結果並寫紀錄。"""
    candidates = [] if loc.name in SPECIAL_NAMES else await _candidates(session, loc.name, loc.id)
    if run is None or not candidates or not evidence:
        await _record(session, principal, loc, "new", "fallback", request_id=request_id)
        return
    codes = {f"L{i}": c for i, (c, _, _) in enumerate(candidates, start=1)}
    payload = {
        "evidence": list(evidence)[:3],
        "new_location": {
            "name": loc.name[:MAX_NAME_CHARS],
            "evidence_ids": [str(e["evidence_id"]) for e in list(evidence)[:3]],
        },
        "candidates": [
            {"location_id": code, "name": c.name, "aliases": aliases, "parent": parent}
            for code, (c, aliases, parent) in zip(codes, candidates, strict=True)
        ],
    }
    result = await run(payload)
    output = result.output if result.status is RunStatus.SUCCEEDED else None
    decision = output.get("decision") if output and output.get("status") == "ok" else None
    target = codes.get(str(output.get("target_id"))) if output else None
    if decision in ("alias_of", "inside_of") and target is not None:
        anchor = target.canonical_id or target.id
        if decision == "alias_of":
            loc.canonical_id = anchor
            await _repoint(session, loc.id, anchor)
        else:
            loc.parent_id = anchor
        await _record(
            session,
            principal,
            loc,
            decision,
            "ai",
            target=anchor,
            run_id=result.run_id,
            request_id=request_id,
        )
    elif decision == "new":
        await _record(
            session, principal, loc, "new", "ai", run_id=result.run_id, request_id=request_id
        )
    else:
        await _record(
            session,
            principal,
            loc,
            "new",
            "fallback",
            run_id=result.run_id,
            request_id=request_id,
        )


async def resolve_location(
    session: AsyncSession,
    name: str | None,
    *,
    evidence: Sequence[Mapping[str, Any]],
    run: SkillCall | None,
    principal: Principal,
    request_id: str | None = None,
) -> uuid.UUID | None:
    """S02 的地點名稱對應到 locations：同名稱沿用；沒見過的建立並以 S13 判斷。"""
    cleaned = (name or "").strip()[:MAX_NAME_CHARS]
    if not cleaned or cleaned == UNKNOWN:
        return None
    existing = await session.scalar(
        select(Location.id).where(Location.name == cleaned).order_by(Location.id).limit(1)
    )
    if existing is not None:
        return existing
    loc = Location(name=cleaned)
    session.add(loc)
    await session.flush()
    await _judge(session, loc, evidence, run=run, principal=principal, request_id=request_id)
    return loc.id


async def split_location(
    session: AsyncSession, principal: Principal, location_id: uuid.UUID
) -> Location:
    """把別名從代表地點拆開，或把地點從上層地點拆開；之後不再自動合併（flush，不 commit）。"""
    loc = await session.scalar(
        select(Location)
        .where(Location.id == location_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if loc is None:
        raise LocationNotFoundError("找不到地點")
    if loc.canonical_id is not None:
        relation, previous = "alias_of", loc.canonical_id
        loc.canonical_id = None
    elif loc.parent_id is not None:
        relation, previous = "inside_of", loc.parent_id
        loc.parent_id = None
    else:
        raise LocationSplitError("這個地點不是別名，也沒有上層地點，不需要拆開")
    session.add(
        LocationResolution(
            location_id=loc.id,
            decision="split",
            target_id=previous,
            method="manual",
            decided_by=principal.actor,
            created_at=datetime.now(UTC),
        )
    )
    await record_audit(
        session,
        principal,
        "location.split",
        target=f"location:{loc.id}",
        metadata={"from": str(previous), "relation": relation},
    )
    await session.flush()
    return loc


async def _location_evidence(session: AsyncSession, loc: Location) -> list[dict[str, Any]]:
    """地點最新事件最早加入的文章（補判用的證據）；沒有事件或文章時為空。"""
    from app.pipeline.analyze_item import _evidence  # 管線匯入本模組

    item_id = await session.scalar(
        select(EventEvidence.source_item_id)
        .join(Event, Event.id == EventEvidence.event_id)
        .where(Event.location_id == loc.id)
        .order_by(Event.created_at.desc(), EventEvidence.created_at, EventEvidence.source_item_id)
        .limit(1)
    )
    return await _evidence(session, [item_id]) if item_id is not None else []


async def backfill_locations(
    session: AsyncSession,
    *,
    run: SkillCall | None,
    principal: Principal,
    request_id: str | None = None,
) -> int:
    """沒有判斷紀錄的既有地點各判一次；回傳這次判斷的地點數（可重跑）。"""
    judged = select(LocationResolution.location_id)
    pending = list(
        await session.scalars(
            select(Location.id)
            .where(Location.id.not_in(judged), Location.name != UNKNOWN)
            .order_by(Location.name, Location.id)
        )
    )
    for location_id in pending:
        loc = await session.get(Location, location_id)
        if loc is None:
            continue
        evidence = await _location_evidence(session, loc)
        await _judge(session, loc, evidence, run=run, principal=principal, request_id=request_id)
    return len(pending)


async def _backfill_in_worker() -> int:
    from app.ai.runner.run import SqlRunStore, run_skill
    from app.pipeline.analyze_item import PIPELINE_PRINCIPAL, build_deps
    from app.worker.tasks.ingest import open_sessionmaker

    deps = build_deps()
    trace = f"backfill-{uuid.uuid4().hex[:12]}"
    async with open_sessionmaker() as maker, maker() as session:

        async def call(payload: Mapping[str, Any]) -> SkillRunResult:
            result = await run_skill(
                deps.skills[S13],
                payload,
                provider=deps.provider,
                config=deps.config,
                store=SqlRunStore(session),
                trace_id=trace,
            )
            await session.commit()
            return result

        count = await backfill_locations(
            session, run=call, principal=PIPELINE_PRINCIPAL, request_id=trace
        )
        await session.commit()
        return count


def main(argv: Sequence[str] | None = None) -> int:
    """命令列：`python -m app.locations.resolver backfill`。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["backfill"]:
        print("用法：python -m app.locations.resolver backfill")
        return 2
    print(f"已補判 {asyncio.run(_backfill_in_worker())} 個地點")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_CANDIDATES",
    "S13",
    "SPECIAL_NAMES",
    "LocationNotFoundError",
    "LocationSplitError",
    "SkillCall",
    "backfill_locations",
    "canonical_id_of",
    "resolve_location",
    "split_location",
]
