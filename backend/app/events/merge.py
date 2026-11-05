"""手動合併／拆分事件（P8-T01，v1.0 F03，規格 D24）。

合併（`merge_events`）：把被併入事件的來源文章（證據）、改善措施，以及這些文章的 S01–S03
執行紀錄移到目標事件；被併入的事件改為 archived（經狀態機）、小報告失效，並寫入
event_merges 導向目標事件。archived 已被候選召回（S04）、公開同步與通知排除，所以：
- 被併入事件的公開內容在同一個交易內撤下（已下架的維持下架），公開快照只剩目標事件；
- S04 不會再把文章併回被併入的事件；被移走的文章重跑管線時，依證據歸到目標事件。
目標事件的事件說明依 P4-T08 的規則（官方來源優先、其次最新）從移入文章的 S02 結果重新挑選。
被併入事件的風險評估、查證與 S04–S06 執行紀錄留在原處當作歷史。

拆分（`split_event`）：把指定的來源文章移到新事件（沿用原事件的類型、地點與開始日期，
標題由使用者填寫），新事件從 analyzing 開始；兩邊的事件說明依各自剩下的文章重新挑選。
兩邊的向量都清空（原本的向量可能來自被拆走的文章），由重跑分析時依各自最早的文章重新計算
（`app/pipeline/analyze_item.py`）；在那之前候選召回暫時找不到這兩個事件。
改善措施與小報告留在原事件。呼叫端提交後再把兩個事件排入重跑 S05、S06（API 負責）。

兩者都只 flush 不 commit；稽核 `event.merge`、`event.split` 只記 id 與筆數，不記標題文字。
分析中（detected、analyzing）或已封存的事件不能合併或拆分。
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.validate import find_pii
from app.alerts.service import sync_event_alert
from app.core.security import Principal
from app.domain.states import EventStatus, transition
from app.models.ai import AIAnalysisRun, EventRelation
from app.models.event import Event, EventEvidence, EventMerge
from app.models.response_action import ResponseAction
from app.pipeline.analyze_item import MAX_TITLE_CHARS, S01, S02, S03, _update_summary
from app.publishing.sync import sync_publication
from app.reports.service import invalidate_reports
from app.services.audit import record_audit

INVALID_EVENT_MERGED = "event_merged"
"""被併入事件的小報告失效原因（event_reports.invalidated_reason）。"""

MAX_SPLIT_ITEMS = 50

_BUSY = frozenset({EventStatus.DETECTED.value, EventStatus.ANALYZING.value})
_ARTICLE_SKILLS = (S01, S02, S03)


class MergeError(Exception):
    """合併或拆分失敗的基底。"""


class MergeNotFoundError(MergeError):
    """事件不存在。"""


class MergeConflictError(MergeError):
    """事件狀態不允許（分析中、已封存或已被併入）。"""


class MergeInvalidError(MergeError):
    """請求內容不正確；`field` 為出錯的欄位。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


async def _lock(session: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Event]:
    """依 id 順序鎖住事件，避免兩個請求交錯合併時互相等待。"""
    rows = await session.scalars(
        select(Event)
        .where(Event.id.in_(list(ids)))
        .order_by(Event.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {e.id: e for e in rows}


def _ensure_open(event: Event, what: str) -> None:
    if event.status in _BUSY:
        raise MergeConflictError(f"{what}還在分析中，稍後再試")
    if event.status == EventStatus.ARCHIVED.value:
        raise MergeConflictError(f"{what}已封存或已被併入其他事件")


async def _item_ids(session: AsyncSession, event_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(EventEvidence.source_item_id).where(EventEvidence.event_id == event_id)
    )
    return set(rows)


async def _move_article_runs(
    session: AsyncSession, source: uuid.UUID, target: uuid.UUID, items: Collection[uuid.UUID]
) -> None:
    """文章的 S01–S03 執行紀錄跟著文章移到目標事件（方便由事件回溯）。"""
    if not items:
        return
    await session.execute(
        update(AIAnalysisRun)
        .where(
            AIAnalysisRun.event_id == source,
            AIAnalysisRun.skill_name.in_(_ARTICLE_SKILLS),
            AIAnalysisRun.evidence_ids.overlap([i.hex for i in items]),
        )
        .values(event_id=target)
    )


async def _refresh_summary(session: AsyncSession, event: Event, items: Iterable[uuid.UUID]) -> None:
    """以文章最近一次成功的 S02 摘要依 P4-T08 的規則重新挑選事件說明（不呼叫模型）。"""
    for item_id in sorted(items):
        run = await session.scalar(
            select(AIAnalysisRun)
            .where(
                AIAnalysisRun.skill_name == S02,
                AIAnalysisRun.status == "succeeded",
                AIAnalysisRun.evidence_ids.contains([item_id.hex]),
            )
            .order_by(AIAnalysisRun.created_at.desc(), AIAnalysisRun.id.desc())
            .limit(1)
        )
        if run is not None and run.result is not None:
            await _update_summary(
                session, event, run.result.get("summary"), item_id=item_id, run_id=run.id
            )


async def _move_relations(session: AsyncSession, source: uuid.UUID, target: uuid.UUID) -> int:
    """被併入事件的關聯改指向目標事件；回傳改指向後留下的筆數。"""
    rows = await session.scalars(
        select(EventRelation)
        .where(or_(EventRelation.from_event_id == source, EventRelation.to_event_id == source))
        .order_by(EventRelation.created_at, EventRelation.id)
    )
    moved = 0
    for rel in list(rows):
        other = rel.to_event_id if rel.from_event_id == source else rel.from_event_id
        if other == target:
            await session.delete(rel)
            continue
        existing = await session.scalar(
            select(EventRelation).where(
                EventRelation.type == rel.type,
                or_(
                    (EventRelation.from_event_id == target) & (EventRelation.to_event_id == other),
                    (EventRelation.from_event_id == other) & (EventRelation.to_event_id == target),
                ),
            )
        )
        if existing is not None:
            if existing.decision != "pending" or rel.decision == "pending":
                await session.delete(rel)
                continue
            await session.delete(existing)
            await session.flush()
        if rel.from_event_id == source:
            rel.from_event_id = target
        else:
            rel.to_event_id = target
        await session.flush()
        moved += 1
    return moved


def _clear_summary(event: Event) -> None:
    event.summary = None
    event.summary_evidence_ids = None
    event.summary_source_item_id = None
    event.summary_ai_run_id = None
    event.summary_updated_at = None


async def merge_events(
    session: AsyncSession,
    principal: Principal,
    event_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    request_id: str,
) -> EventMerge:
    """把 `event_id` 併入 `target_id`；回傳合併紀錄（flush，不 commit）。"""
    if event_id == target_id:
        raise MergeInvalidError("target_event_id", "不能把事件併入自己")
    events = await _lock(session, (event_id, target_id))
    source, target = events.get(event_id), events.get(target_id)
    if source is None:
        raise MergeNotFoundError("找不到要併入的事件")
    if target is None:
        raise MergeNotFoundError("找不到目標事件")
    _ensure_open(source, "要併入的事件")
    _ensure_open(target, "目標事件")
    archived = transition(EventStatus(source.status), EventStatus.ARCHIVED)

    moved = await _item_ids(session, source.id)
    shared = moved & await _item_ids(session, target.id)
    if shared:  # 兩邊都有的文章只保留目標事件那一筆
        await session.execute(
            delete(EventEvidence).where(
                EventEvidence.event_id == source.id, EventEvidence.source_item_id.in_(shared)
            )
        )
    await session.execute(
        update(EventEvidence).where(EventEvidence.event_id == source.id).values(event_id=target.id)
    )
    await _move_article_runs(session, source.id, target.id, moved)
    actions = await session.execute(
        update(ResponseAction)
        .where(ResponseAction.event_id == source.id)
        .values(event_id=target.id, updated_at=func.now())
    )
    await _refresh_summary(session, target, moved - shared)
    relations = await _move_relations(session, source.id, target.id)

    source.status = archived.value
    await invalidate_reports(session, source.id, INVALID_EVENT_MERGED)
    record = EventMerge(
        merged_event_id=source.id, target_event_id=target.id, decided_by=principal.actor
    )
    session.add(record)
    await session.flush()
    await record_audit(
        session,
        principal,
        "event.merge",
        target=f"event:{source.id}",
        request_id=request_id,
        metadata={
            "into": str(target.id),
            "moved_evidence": len(moved),
            "moved_actions": int(getattr(actions, "rowcount", 0) or 0),
            "moved_relations": relations,
        },
    )
    # 被併入的事件結束通知、撤下公開內容；目標事件的內容等重跑分析後再更新
    await sync_event_alert(session, source.id, request_id=request_id)
    await sync_publication(session, source.id)
    await sync_publication(session, target.id)
    return record


def _title(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise MergeInvalidError("title", "請填寫新事件的標題")
    if len(cleaned) > MAX_TITLE_CHARS:
        raise MergeInvalidError("title", f"標題超過 {MAX_TITLE_CHARS} 字")
    if find_pii(cleaned):
        raise MergeInvalidError("title", "標題疑似含個資")
    return cleaned


async def split_event(
    session: AsyncSession,
    principal: Principal,
    event_id: uuid.UUID,
    item_ids: Collection[uuid.UUID],
    *,
    title: str,
    request_id: str,
) -> Event:
    """把事件中的 `item_ids` 移到新事件；回傳新事件（flush，不 commit）。"""
    clean_title = _title(title)
    items = set(item_ids)
    if not items:
        raise MergeInvalidError("source_item_ids", "請選擇要拆出的來源文章")
    if len(items) > MAX_SPLIT_ITEMS:
        raise MergeInvalidError("source_item_ids", f"一次最多拆出 {MAX_SPLIT_ITEMS} 篇")
    source = (await _lock(session, (event_id,))).get(event_id)
    if source is None:
        raise MergeNotFoundError("找不到事件")
    _ensure_open(source, "事件")
    current = await _item_ids(session, source.id)
    if not items <= current:
        raise MergeInvalidError("source_item_ids", "只能拆出這個事件的來源文章")
    if items == current:
        raise MergeInvalidError("source_item_ids", "至少要留一篇來源文章在原事件")

    new = Event(
        title=clean_title,
        type=source.type,
        status=EventStatus.DETECTED.value,
        location_id=source.location_id,
        occurrence_start_date=source.occurrence_start_date,
    )
    session.add(new)
    await session.flush()
    new.status = transition(EventStatus.DETECTED, EventStatus.ANALYZING).value
    await session.execute(
        update(EventEvidence)
        .where(EventEvidence.event_id == source.id, EventEvidence.source_item_id.in_(items))
        .values(event_id=new.id)
    )
    await _move_article_runs(session, source.id, new.id, items)
    source.embedding = None
    if source.summary_source_item_id in items:
        _clear_summary(source)
        await _refresh_summary(session, source, current - items)
    await _refresh_summary(session, new, items)
    await session.flush()
    await record_audit(
        session,
        principal,
        "event.split",
        target=f"event:{source.id}",
        request_id=request_id,
        metadata={"new_event_id": str(new.id), "moved_evidence": len(items)},
    )
    await sync_publication(session, source.id)
    return new


__all__ = [
    "INVALID_EVENT_MERGED",
    "MAX_SPLIT_ITEMS",
    "MergeConflictError",
    "MergeError",
    "MergeInvalidError",
    "MergeNotFoundError",
    "merge_events",
    "split_event",
]
