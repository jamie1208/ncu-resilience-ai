"""管理端事件關聯圖 API（P6-T01，規格 F07）。

- `GET /api/v1/admin/relations`：關聯圖（nodes、edges，格式對應 P6-T02 的 Cytoscape.js）；
  可依事件、類型、決策篩選，預設不含被駁回的邊（`include_rejected=true` 時包含）。
  需要 `events:read_all`。
- `GET /api/v1/admin/relations/{id}`：一條關聯與兩端事件。需要 `events:read_all`。
- `POST /api/v1/admin/relations/{id}/decision`：確認（confirmed）、駁回（rejected），或回滾到
  待確認（pending）；已決定的關聯要先回滾才能改判，非法改變回 CONFLICT。需要 `events:verify`，
  寫入稽核 `relation.decide`。

只在管理端；推測原因不經公開端（ADR-0015）。規則見 app/relations/service.py。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.ai import EventRelation
from app.models.event import Event
from app.relations.service import (
    Decision,
    RelationConflictError,
    RelationError,
    RelationInvalidError,
    RelationType,
    decide_relation,
    get_relation,
    relation_graph,
    relation_nodes,
)

router = APIRouter(prefix="/relations", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]
CanWrite = Annotated[Principal, Depends(require_permission(Permission.EVENTS_VERIFY))]


class NodeOut(BaseModel):
    """關聯圖的節點（事件）。"""

    id: uuid.UUID
    title: str
    type: str
    status: str
    occurrence_start_date: date | None


class EdgeOut(BaseModel):
    """關聯圖的邊；source、target 為事件 id，hypothesized_cause 由 source 指向結果 target。"""

    id: uuid.UUID
    source: uuid.UUID
    target: uuid.UUID
    type: str
    method: str
    score: float | None
    evidence_ids: list[str]
    ai_run_id: uuid.UUID | None
    decision: str
    decided_by: str | None
    decided_at: AwareDatetime | None
    decision_note: str | None
    created_at: AwareDatetime


class GraphOut(BaseModel):
    """關聯圖。"""

    nodes: list[NodeOut]
    edges: list[EdgeOut]


class RelationDetail(BaseModel):
    """一條關聯與兩端事件。"""

    edge: EdgeOut
    nodes: list[NodeOut]


class DecisionIn(BaseModel):
    """人工決策；pending 代表回滾。備註不得含個資。"""

    decision: Decision
    note: Annotated[str | None, Field(max_length=500)] = None


def _api_error(error: RelationError) -> ApiError:
    if isinstance(error, RelationInvalidError):
        return ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(error),
            details=[{"field": f"body.{error.field}", "message": str(error)}],
        )
    if isinstance(error, RelationConflictError):
        return ApiError(ErrorCode.CONFLICT, str(error))
    return ApiError(ErrorCode.NOT_FOUND, str(error))


def _nodes(events: Sequence[Event]) -> list[NodeOut]:
    return [
        NodeOut(
            id=e.id,
            title=e.title,
            type=e.type,
            status=e.status,
            occurrence_start_date=e.occurrence_start_date,
        )
        for e in events
    ]


def _edge(r: EventRelation) -> EdgeOut:
    return EdgeOut(
        id=r.id,
        source=r.from_event_id,
        target=r.to_event_id,
        type=r.type,
        method=r.method,
        score=r.score,
        evidence_ids=[str(e) for e in r.evidence],
        ai_run_id=r.ai_run_id,
        decision=r.decision,
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        decision_note=r.decision_note,
        created_at=r.created_at,
    )


async def _detail(session: AsyncSession, relation: EventRelation) -> dict[str, Any]:
    await session.refresh(relation)
    nodes = await relation_nodes(session, relation)
    return RelationDetail(edge=_edge(relation), nodes=_nodes(nodes)).model_dump(mode="json")


@router.get("")
async def list_relations(
    session: Session,
    _: CanRead,
    event_id: Annotated[uuid.UUID | None, Query()] = None,
    type: Annotated[RelationType | None, Query()] = None,
    decision: Annotated[Decision | None, Query()] = None,
    include_rejected: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """關聯圖（最多 500 條邊，新到舊）。"""
    graph = await relation_graph(
        session,
        event_id=event_id,
        type=type,
        decision=decision,
        include_rejected=include_rejected,
    )
    data = GraphOut(nodes=_nodes(graph.nodes), edges=[_edge(r) for r in graph.edges])
    return envelope(data.model_dump(mode="json"))


@router.get("/{relation_id}")
async def get_event_relation(
    relation_id: uuid.UUID, session: Session, _: CanRead
) -> dict[str, Any]:
    """一條關聯；不存在回 NOT_FOUND。"""
    try:
        relation = await get_relation(session, relation_id)
    except RelationError as e:
        raise _api_error(e) from None
    return envelope(await _detail(session, relation))


@router.post("/{relation_id}/decision")
async def decide_event_relation(
    relation_id: uuid.UUID, body: DecisionIn, session: Session, principal: CanWrite
) -> dict[str, Any]:
    """確認、駁回或回滾關聯。"""
    try:
        relation = await decide_relation(
            session, principal, relation_id, body.decision, note=body.note
        )
    except RelationError as e:
        raise _api_error(e) from None
    await session.commit()
    return envelope(await _detail(session, relation))
