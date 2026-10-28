"""事件關聯圖資料（P6-T01，規格 F07）。

- 關聯類型：same_event（兩個事件其實是同一件事）、related_event（相關）、hypothesized_cause
  （from 可能是 to 的原因，推測，尚未查證）。每條邊存 evidence_ids、方法（ai／rule／manual）、
  分數與人工決策。推測原因只在管理端，公開端不提供關聯（ADR-0015）。
- 新增：禁止 self-loop；同一對事件同一類型不論方向只能有一條；evidence_ids 必須是兩端事件之一的
  證據文章（hex），hypothesized_cause 至少要有一個。P2-T09 管線直接寫入 related_event，
  方法與決策用資料庫預設值（ai、pending）。
- 人工決策：pending → confirmed／rejected；已決定的關聯只能回到 pending（回滾），不能直接翻轉，
  避免誤觸。回滾清除決策者、時間與備註，邊、證據與分數不變。每次決策寫入稽核 `relation.decide`，
  稽核紀錄就是決策歷史。
- 查詢：關聯圖預設不含被駁回的邊；節點為邊的兩端事件（指定事件時含該事件本身）。

本模組只 flush、不 commit，由呼叫端決定交易邊界；所有檢查都在修改之前完成。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.validate import find_pii
from app.core.security import Principal
from app.models.ai import EventRelation
from app.models.event import Event, EventEvidence
from app.services.audit import record_audit

NOTE_MAX = 200
GRAPH_LIMIT = 500


class RelationType(StrEnum):
    """關聯類型；值存入資料庫，已上線的值不可改名。"""

    SAME_EVENT = "same_event"
    RELATED_EVENT = "related_event"
    HYPOTHESIZED_CAUSE = "hypothesized_cause"


class RelationMethod(StrEnum):
    """關聯由誰提出。"""

    AI = "ai"
    RULE = "rule"
    MANUAL = "manual"


class Decision(StrEnum):
    """人工決策。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


_D = Decision

DECISION_TRANSITIONS: dict[Decision, frozenset[Decision]] = {
    _D.PENDING: frozenset({_D.CONFIRMED, _D.REJECTED}),
    _D.CONFIRMED: frozenset({_D.PENDING}),
    _D.REJECTED: frozenset({_D.PENDING}),
}


class RelationError(Exception):
    """關聯操作失敗；由 API 轉成對應的錯誤碼。"""


class RelationNotFoundError(RelationError):
    """事件或關聯不存在。"""


class RelationConflictError(RelationError):
    """與既有資料或目前決策衝突。"""


class RelationInvalidError(RelationError):
    """輸入不合格；`field` 為出問題的欄位。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Graph:
    """關聯圖：節點為事件，邊為關聯。"""

    nodes: Sequence[Event]
    edges: Sequence[EventRelation]


def _note(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > NOTE_MAX:
        raise RelationInvalidError("note", f"超過 {NOTE_MAX} 字")
    if find_pii(cleaned):
        raise RelationInvalidError("note", "疑似含個資")
    return cleaned


async def _evidence_ids(session: AsyncSession, event_ids: Iterable[uuid.UUID]) -> set[str]:
    rows = await session.scalars(
        select(EventEvidence.source_item_id).where(EventEvidence.event_id.in_(list(event_ids)))
    )
    return {item_id.hex for item_id in rows}


async def add_relation(
    session: AsyncSession,
    *,
    from_event_id: uuid.UUID,
    to_event_id: uuid.UUID,
    type: RelationType | str,
    method: RelationMethod | str,
    evidence_ids: Sequence[str],
    score: float | None = None,
    ai_run_id: uuid.UUID | None = None,
) -> EventRelation:
    """新增一條待確認的關聯；違反規則時拋出 RelationError，不寫入任何資料。"""
    kind = RelationType(type)
    how = RelationMethod(method)
    if from_event_id == to_event_id:
        raise RelationInvalidError("to_event_id", "事件不能關聯到自己")
    if score is not None and not 0 <= score <= 1:
        raise RelationInvalidError("score", "分數必須介於 0 與 1")
    for event_id in (from_event_id, to_event_id):
        if await session.get(Event, event_id) is None:
            raise RelationNotFoundError("找不到事件")
    cited = list(dict.fromkeys(evidence_ids))
    if kind is RelationType.HYPOTHESIZED_CAUSE and not cited:
        raise RelationInvalidError("evidence_ids", "推測原因必須附證據")
    known = await _evidence_ids(session, (from_event_id, to_event_id))
    if any(e not in known for e in cited):
        raise RelationInvalidError("evidence_ids", "證據必須屬於關聯兩端的事件")
    exists = await session.scalar(
        select(EventRelation.id).where(
            EventRelation.type == kind.value,
            or_(
                (EventRelation.from_event_id == from_event_id)
                & (EventRelation.to_event_id == to_event_id),
                (EventRelation.from_event_id == to_event_id)
                & (EventRelation.to_event_id == from_event_id),
            ),
        )
    )
    if exists is not None:
        raise RelationConflictError("這兩個事件已有同類型的關聯")
    relation = EventRelation(
        from_event_id=from_event_id,
        to_event_id=to_event_id,
        type=kind.value,
        method=how.value,
        score=score,
        evidence=cited,
        ai_run_id=ai_run_id,
        decision=Decision.PENDING.value,
    )
    session.add(relation)
    await session.flush()
    return relation


async def get_relation(session: AsyncSession, relation_id: uuid.UUID) -> EventRelation:
    """取得關聯；不存在時拋出 RelationNotFoundError。"""
    relation = await session.get(EventRelation, relation_id)
    if relation is None:
        raise RelationNotFoundError("找不到關聯")
    return relation


async def decide_relation(
    session: AsyncSession,
    principal: Principal,
    relation_id: uuid.UUID,
    to: Decision,
    *,
    note: str | None = None,
) -> EventRelation:
    """確認、駁回或回滾（回到 pending）；不在轉移表中回 RelationConflictError。"""
    relation = await get_relation(session, relation_id)
    note = _note(note)
    previous = Decision(relation.decision)
    if to not in DECISION_TRANSITIONS[previous]:
        raise RelationConflictError(f"關聯不能從 {previous.value} 改為 {to.value}")
    relation.decision = to.value
    if to is Decision.PENDING:
        relation.decided_at = None
        relation.decided_by = None
        relation.decision_note = None
    else:
        relation.decided_at = datetime.now(UTC)
        relation.decided_by = principal.actor
        relation.decision_note = note
    await session.flush()
    await record_audit(
        session,
        principal,
        "relation.decide",
        target=f"event_relation:{relation.id}",
        metadata={"from": previous.value, "to": to.value, "type": relation.type},
    )
    return relation


async def _events(session: AsyncSession, ids: Iterable[uuid.UUID]) -> Sequence[Event]:
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    rows = await session.scalars(select(Event).where(Event.id.in_(wanted)))
    by_id = {e.id: e for e in rows}
    return [by_id[i] for i in wanted if i in by_id]


async def relation_graph(
    session: AsyncSession,
    *,
    event_id: uuid.UUID | None = None,
    type: RelationType | None = None,
    decision: Decision | None = None,
    include_rejected: bool = False,
    limit: int = GRAPH_LIMIT,
) -> Graph:
    """關聯圖；指定 event_id 時只含與該事件直接相連的邊。邊依建立時間新到舊。"""
    query = select(EventRelation)
    if event_id is not None:
        query = query.where(
            or_(EventRelation.from_event_id == event_id, EventRelation.to_event_id == event_id)
        )
    if type is not None:
        query = query.where(EventRelation.type == type.value)
    if decision is not None:
        query = query.where(EventRelation.decision == decision.value)
    elif not include_rejected:
        query = query.where(EventRelation.decision != Decision.REJECTED.value)
    query = query.order_by(EventRelation.created_at.desc(), EventRelation.id.desc()).limit(limit)
    edges = list((await session.scalars(query)).all())
    ids = [event_id] if event_id is not None else []
    for edge in edges:
        ids += [edge.from_event_id, edge.to_event_id]
    return Graph(nodes=await _events(session, ids), edges=edges)


async def relation_nodes(session: AsyncSession, relation: EventRelation) -> Sequence[Event]:
    """一條關聯兩端的事件。"""
    return await _events(session, (relation.from_event_id, relation.to_event_id))
