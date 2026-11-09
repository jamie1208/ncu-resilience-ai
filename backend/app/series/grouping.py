"""系列自動歸組（P8-T04，ADR-0018 決策 1-1～1-4、ADR-0020 決策 1）：以 S11 series-matcher 判斷。

- 每個事件屬於一個系列。還沒有系列的事件（新事件、補判）挑同類別的既有系列當候選，S11 判斷
  同主題就加入，否則建立只有它一件的系列。候選：至少有一個不是 false 的事件、不是這個事件
  離開過的系列；依與事件向量的最近距離排序（沒有向量的排最後，其次最近加入、id），最多 10 個，
  代號 S1…依序，每個附最近 3 起（不是 false 的）事件的標題、摘要與開始日期。輸入不含地點。
- 沒有候選、事件是 false 或已封存、沒有證據或類別不在 S11 的列舉中時不呼叫，直接建立新系列
  （method fallback）。abstain、失敗或 target_id 不在候選中一律建立新系列（method fallback）。
  有呼叫 S11 時寫稽核 `series.assign`（只記系列 id、same_topic 與方法）。
- 每個事件只自動歸組一次：已有系列的事件不再判斷，所以使用者拆出後不會被自動歸回；
  離開過的系列也不會再當它的候選。
- 拆出（`split_from_series`）：事件自成 1 件的系列（method manual），稽核 `series.split`。
  解散（`dissolve_series`）：每個事件各自成為 1 件的系列，原系列刪除，稽核 `series.dissolve`。
- 改判 false（`sync_event_series`）：事件移出、自成 1 件的系列（method rule）；
  已經只有它一件時不動。被合併（`leave_merged`）：事件離開系列、不再屬於任何系列
  （合併後只算 1 件）。兩者稽核 `series.remove`。
- 離開系列都新增一筆 event_series_removals；件數 0 的系列刪除。
- 補判（`backfill_series`）：沒有系列、也沒有被合併的事件依建立時間各判一次；可重跑。命令列：
  `uv run python -m app.series.grouping backfill`（使用正式的模型設定，受每日費用上限限制）。

本模組只 flush、不 commit；`run` 由呼叫端提供（管線用 `_Context.run`，執行紀錄由 Runner 寫入）。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.run import RunStatus, SkillRunResult
from app.core.security import Principal
from app.domain.states import EventStatus
from app.models.event import Event, EventMerge
from app.models.series import EventSeries, EventSeriesMember, EventSeriesRemoval
from app.services.audit import record_audit

S11 = "series-matcher"
MAX_CANDIDATES = 10
EVENTS_PER_CANDIDATE = 3
MAX_EVIDENCE = 3
CATEGORIES = frozenset(
    {"facility", "weather", "security", "traffic", "health", "fire", "academic", "infosec"}
)
"""S11 輸入 schema 的類別列舉（S03 除了 none 以外的類別）。"""
_UNGROUPED = (EventStatus.FALSE.value, EventStatus.ARCHIVED.value)

SkillCall = Callable[[Mapping[str, Any]], Awaitable[SkillRunResult]]


class SeriesError(Exception):
    """系列操作失敗。"""


class SeriesNotFoundError(SeriesError):
    """系列不存在，或事件不在這個系列。"""


class SeriesConflictError(SeriesError):
    """系列只有一件，不需要拆出或解散。"""


def _now() -> datetime:
    # 應用端時間：同一個交易內的多筆紀錄也能依時間排序
    return datetime.now(UTC)


async def series_of(session: AsyncSession, event_id: uuid.UUID) -> uuid.UUID | None:
    """事件所屬的系列；被合併的事件為 None。"""
    return await session.scalar(
        select(EventSeriesMember.series_id).where(EventSeriesMember.event_id == event_id)
    )


async def _size(session: AsyncSession, series_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(EventSeriesMember)
        .where(EventSeriesMember.series_id == series_id)
    )
    return int(count or 0)


async def _is_merged(session: AsyncSession, event_id: uuid.UUID) -> bool:
    merged = await session.scalar(
        select(EventMerge.id).where(EventMerge.merged_event_id == event_id).limit(1)
    )
    return merged is not None


async def _new_series(
    session: AsyncSession, event: Event, method: str, *, run_id: uuid.UUID | None = None
) -> uuid.UUID:
    """為事件建立只有它一件的系列（事件此時不能已有系列）。"""
    series = EventSeries(category=event.type, title=event.title[:300])
    session.add(series)
    await session.flush()
    session.add(
        EventSeriesMember(
            event_id=event.id,
            series_id=series.id,
            method=method,
            ai_run_id=run_id,
            joined_at=_now(),
        )
    )
    await session.flush()
    return series.id


async def _leave(
    session: AsyncSession,
    principal: Principal,
    event_id: uuid.UUID,
    series_id: uuid.UUID,
    reason: str,
) -> None:
    """事件離開系列並留下紀錄；系列沒有事件時刪除。"""
    await session.execute(delete(EventSeriesMember).where(EventSeriesMember.event_id == event_id))
    session.add(
        EventSeriesRemoval(
            event_id=event_id,
            series_id=series_id,
            reason=reason,
            decided_by=principal.actor,
            created_at=_now(),
        )
    )
    await session.flush()
    if await _size(session, series_id) == 0:
        await session.execute(delete(EventSeries).where(EventSeries.id == series_id))
        await session.flush()


async def _move_out(
    session: AsyncSession,
    principal: Principal,
    event: Event,
    series_id: uuid.UUID,
    reason: str,
    method: str,
) -> uuid.UUID:
    await _leave(session, principal, event.id, series_id, reason)
    return await _new_series(session, event, method)


# ---------- 自動歸組 ----------


async def _candidates(session: AsyncSession, event: Event) -> list[tuple[EventSeries, int]]:
    """同類別、可以加入的系列與件數，依與事件向量的距離排序。"""
    left = select(EventSeriesRemoval.series_id).where(
        EventSeriesRemoval.event_id == event.id, EventSeriesRemoval.series_id.is_not(None)
    )
    order: list[Any] = []
    if event.embedding is not None:
        distance = func.min(Event.embedding.cosine_distance(list(event.embedding)))
        order.append(distance.asc().nulls_last())
    order += [func.max(EventSeriesMember.joined_at).desc(), EventSeries.id]
    rows = await session.execute(
        select(EventSeries, func.count(EventSeriesMember.event_id))
        .join(EventSeriesMember, EventSeriesMember.series_id == EventSeries.id)
        .join(Event, Event.id == EventSeriesMember.event_id)
        .where(EventSeries.category == event.type, EventSeries.id.not_in(left))
        .group_by(EventSeries.id)
        .having(func.count().filter(Event.status != EventStatus.FALSE.value) > 0)
        .order_by(*order)
        .limit(MAX_CANDIDATES)
    )
    return [(series, int(count)) for series, count in rows.all()]


async def _recent_events(session: AsyncSession, series_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(Event)
        .join(EventSeriesMember, EventSeriesMember.event_id == Event.id)
        .where(EventSeriesMember.series_id == series_id, Event.status != EventStatus.FALSE.value)
        .order_by(Event.created_at.desc(), Event.id)
        .limit(EVENTS_PER_CANDIDATE)
    )
    return [
        {
            "title": e.title[:300],
            "summary": e.summary[:1000] if e.summary else None,
            "occurrence_start_date": (
                e.occurrence_start_date.isoformat() if e.occurrence_start_date else None
            ),
        }
        for e in rows
    ]


async def _event_evidence(session: AsyncSession, event: Event) -> list[dict[str, Any]]:
    from app.pipeline.analyze_item import _event_item_ids, _evidence  # 管線匯入本模組

    return await _evidence(session, await _event_item_ids(session, event.id, MAX_EVIDENCE))


async def assign_series(
    session: AsyncSession,
    event: Event,
    *,
    run: SkillCall | None,
    principal: Principal,
    request_id: str | None = None,
) -> uuid.UUID | None:
    """還沒有系列的事件歸入系列（S11），回傳系列 id；已有系列時不判斷，被合併的事件為 None。"""
    current = await series_of(session, event.id)
    if current is not None:
        return current
    if await _is_merged(session, event.id):
        return None
    eligible = event.status not in _UNGROUPED and event.type in CATEGORIES
    candidates = await _candidates(session, event) if eligible and run is not None else []
    evidence = await _event_evidence(session, event) if candidates else []
    if run is None or not candidates or not evidence:
        return await _new_series(session, event, "fallback")

    codes = {f"S{i}": series for i, (series, _) in enumerate(candidates, start=1)}
    payload = {
        "evidence": evidence,
        "event": {
            "title": event.title[:300],
            "summary": event.summary[:1000] if event.summary else None,
            "category": event.type,
            "evidence_ids": [str(e["evidence_id"]) for e in evidence],
        },
        "candidates": [
            {
                "series_id": code,
                "title": series.title,
                "event_count": count,
                "events": await _recent_events(session, series.id),
            }
            for code, (series, count) in zip(codes, candidates, strict=True)
        ],
    }
    result = await run(payload)
    output = result.output if result.status is RunStatus.SUCCEEDED else None
    same = output.get("same_topic") if output and output.get("status") == "ok" else None
    target = codes.get(str(output.get("target_id"))) if output else None
    if same is True and target is not None:
        session.add(
            EventSeriesMember(
                event_id=event.id,
                series_id=target.id,
                method="ai",
                ai_run_id=result.run_id,
                joined_at=_now(),
            )
        )
        await session.flush()
        series_id, method = target.id, "ai"
    else:
        method = "ai" if same is False else "fallback"
        series_id = await _new_series(session, event, method, run_id=result.run_id)
    await record_audit(
        session,
        principal,
        "series.assign",
        target=f"event:{event.id}",
        request_id=request_id,
        metadata={"series_id": str(series_id), "same_topic": same, "method": method},
    )
    await session.flush()
    return series_id


async def leave_merged(
    session: AsyncSession, principal: Principal, event_id: uuid.UUID, *, request_id: str | None
) -> None:
    """被合併的事件離開系列（合併後在系列中只算 1 件）。"""
    current = await series_of(session, event_id)
    if current is None:
        return
    await _leave(session, principal, event_id, current, "merged")
    await record_audit(
        session,
        principal,
        "series.remove",
        target=f"event:{event_id}",
        request_id=request_id,
        metadata={"reason": "merged", "from": str(current)},
    )


async def sync_event_series(
    session: AsyncSession,
    event: Event,
    *,
    run: SkillCall | None,
    principal: Principal,
    request_id: str | None = None,
) -> uuid.UUID | None:
    """事件查證後更新系列：沒有系列時歸組，改判 false 時移出自成一件；回傳目前的系列。"""
    if await _is_merged(session, event.id):
        await leave_merged(session, principal, event.id, request_id=request_id)
        return None
    current = await series_of(session, event.id)
    if current is None:
        return await assign_series(
            session, event, run=run, principal=principal, request_id=request_id
        )
    if event.status != EventStatus.FALSE.value or await _size(session, current) <= 1:
        return current
    new = await _move_out(session, principal, event, current, "false", "rule")
    await record_audit(
        session,
        principal,
        "series.remove",
        target=f"event:{event.id}",
        request_id=request_id,
        metadata={"reason": "false", "from": str(current), "to": str(new)},
    )
    return new


# ---------- 使用者調整 ----------


async def _lock_series(session: AsyncSession, series_id: uuid.UUID) -> EventSeries:
    series = await session.scalar(
        select(EventSeries).where(EventSeries.id == series_id).with_for_update()
    )
    if series is None:
        raise SeriesNotFoundError("找不到系列")
    return series


async def split_from_series(
    session: AsyncSession, principal: Principal, series_id: uuid.UUID, event_id: uuid.UUID
) -> uuid.UUID:
    """把事件拆出系列，自成 1 件的系列；之後不再自動歸回。回傳新系列 id（flush，不 commit）。"""
    await _lock_series(session, series_id)
    event = await session.get(Event, event_id)
    if event is None or await series_of(session, event_id) != series_id:
        raise SeriesNotFoundError("事件不在這個系列")
    if await _size(session, series_id) <= 1:
        raise SeriesConflictError("這個系列只有一件，不需要拆出")
    new = await _move_out(session, principal, event, series_id, "split", "manual")
    await record_audit(
        session,
        principal,
        "series.split",
        target=f"event:{event_id}",
        metadata={"from": str(series_id), "to": str(new)},
    )
    return new


async def dissolve_series(
    session: AsyncSession, principal: Principal, series_id: uuid.UUID
) -> dict[uuid.UUID, uuid.UUID]:
    """解散系列：每個事件各自成為 1 件的系列，原系列刪除。回傳事件 id → 新系列 id。"""
    await _lock_series(session, series_id)
    events = list(
        await session.scalars(
            select(Event)
            .join(EventSeriesMember, EventSeriesMember.event_id == Event.id)
            .where(EventSeriesMember.series_id == series_id)
            .order_by(EventSeriesMember.joined_at, Event.id)
        )
    )
    if len(events) <= 1:
        raise SeriesConflictError("這個系列只有一件，不需要解散")
    moved = {
        e.id: await _move_out(session, principal, e, series_id, "dissolve", "manual")
        for e in events
    }
    await record_audit(
        session,
        principal,
        "series.dissolve",
        target=f"series:{series_id}",
        metadata={"events": len(events)},
    )
    return moved


# ---------- 補判 ----------


async def backfill_series(
    session: AsyncSession,
    *,
    run: SkillCall | None,
    principal: Principal,
    request_id: str | None = None,
) -> int:
    """沒有系列、也沒有被合併的事件依建立時間各判一次；回傳這次歸組的事件數（可重跑）。"""
    pending = list(
        await session.scalars(
            select(Event.id)
            .where(
                ~exists().where(EventSeriesMember.event_id == Event.id),
                ~exists().where(EventMerge.merged_event_id == Event.id),
            )
            .order_by(Event.created_at, Event.id)
        )
    )
    for event_id in pending:
        event = await session.get(Event, event_id)
        if event is not None:
            await assign_series(session, event, run=run, principal=principal, request_id=request_id)
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
                deps.skills[S11],
                payload,
                provider=deps.provider,
                config=deps.config,
                store=SqlRunStore(session),
                trace_id=trace,
            )
            await session.commit()
            return result

        count = await backfill_series(
            session, run=call, principal=PIPELINE_PRINCIPAL, request_id=trace
        )
        await session.commit()
        return count


def main(argv: Sequence[str] | None = None) -> int:
    """命令列：`python -m app.series.grouping backfill`。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["backfill"]:
        print("用法：python -m app.series.grouping backfill")
        return 2
    print(f"已補判 {asyncio.run(_backfill_in_worker())} 個事件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_CANDIDATES",
    "S11",
    "SeriesConflictError",
    "SeriesError",
    "SeriesNotFoundError",
    "SkillCall",
    "assign_series",
    "backfill_series",
    "dissolve_series",
    "leave_merged",
    "series_of",
    "split_from_series",
    "sync_event_series",
]
