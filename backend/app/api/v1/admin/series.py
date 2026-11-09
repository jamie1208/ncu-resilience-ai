"""管理端系列 API（P8-T04，ADR-0018 決策 1-1～1-4、ADR-0020 決策 1）。

- `GET /api/v1/admin/series`：系列清單（最近有事件加入的在前，最多 `limit` 筆），可依 `category`
  或 `event_id`（該事件所屬的系列）篩選。需要 `events:read_all`。
- `GET /api/v1/admin/series/{id}`：系列與它的事件（加入時間排序，附加入方式）及移出紀錄。
  需要 `events:read_all`；系列不存在回 NOT_FOUND。
- `POST /api/v1/admin/series/{id}/events/{event_id}/split`（回 201）：把事件拆出系列，
  自成 1 件的系列，之後不再自動歸回。需要 `events:verify`，寫入稽核 `series.split`。
- `POST /api/v1/admin/series/{id}/dissolve`：解散系列，每個事件各自成為 1 件的系列。
  需要 `events:verify`，寫入稽核 `series.dissolve`。
系列或事件不存在（含事件不在這個系列）回 NOT_FOUND，系列只有一件回 CONFLICT。

P8-T05（ADR-0018 決策 1-5～1-8、ADR-0020 決策 3-4）：
- `GET /api/v1/admin/series/{id}` 另附名稱來源、最新一次成功的 S12 分析（原因附目前的人工決策；
  建議附已建立的追蹤項目）、最近一次分析嘗試的狀態與系列層級的措施。
- `PATCH /api/v1/admin/series/{id}`：手動改名，之後 S12 不覆蓋。需要 `events:verify`，
  稽核 `series.rename`（from／to）。
- `GET /api/v1/admin/series/{id}/timeline`：日／週／月件數、4 期移動平均、措施標記與每項措施的
  前後比較（任一邊少於 5 件為「資料還不夠」，一律附「變少不一定是措施的功勞」）；可依代表地點
  篩選，附系列中的地點與件數。需要 `events:read_all`。
- `POST /api/v1/admin/series/{id}/actions`（回 201）：從最新分析的建議一鍵加入追蹤（analysis_id、
  measure_index），或手動新增（title）。需要 `events:verify`，稽核 `response_action.create`。
- `POST /api/v1/admin/series/{id}/causes/{cause_id}/decision`（回 201）：人工確認或回滾推測原因。
  需要 `events:verify`，稽核 `series.cause_decide`。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.event import Event
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.models.series import EventSeries, EventSeriesMember, EventSeriesRemoval
from app.resilience.actions import ActionError, create_series_action, is_overdue
from app.series.analysis import (
    CONFIRMED_LABEL,
    AnalysisConflictError,
    AnalysisError,
    AnalysisInvalidError,
    CauseDecision,
    current_decisions,
    decide_series_cause,
    event_date,
    latest_analysis,
    location_names,
    rename_series,
    series_events,
    taipei_date,
)
from app.series.grouping import (
    SeriesConflictError,
    SeriesNotFoundError,
    dissolve_series,
    split_from_series,
)
from app.series.timeline import ActionMarker, before_after, build_timeline

router = APIRouter(prefix="/series", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]
CanVerify = Annotated[Principal, Depends(require_permission(Permission.EVENTS_VERIFY))]


def _series_data(series: EventSeries, count: int, latest: datetime | None) -> dict[str, Any]:
    return {
        "id": str(series.id),
        "category": series.category,
        "title": series.title,
        "title_source": series.title_source,
        "event_count": count,
        "latest_joined_at": latest.isoformat() if latest else None,
        "created_at": series.created_at.isoformat(),
    }


@router.get("")
async def list_series(
    session: Session,
    _: CanRead,
    category: Annotated[str | None, Query(max_length=32)] = None,
    event_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """系列清單與件數。"""
    latest = func.max(EventSeriesMember.joined_at)
    query = (
        select(EventSeries, func.count(EventSeriesMember.event_id), latest)
        .join(EventSeriesMember, EventSeriesMember.series_id == EventSeries.id)
        .group_by(EventSeries.id)
        .order_by(latest.desc(), EventSeries.id)
        .limit(limit)
    )
    if category is not None:
        query = query.where(EventSeries.category == category)
    if event_id is not None:
        query = query.where(
            EventSeries.id
            == select(EventSeriesMember.series_id)
            .where(EventSeriesMember.event_id == event_id)
            .scalar_subquery()
        )
    rows = (await session.execute(query)).all()
    return envelope([_series_data(s, int(count), last) for s, count, last in rows])


@router.get("/{series_id}")
async def get_series(series_id: uuid.UUID, session: Session, _: CanRead) -> dict[str, Any]:
    """系列與它的事件。"""
    series = await session.get(EventSeries, series_id)
    if series is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到系列")
    rows = list(
        (
            await session.execute(
                select(Event, EventSeriesMember)
                .join(EventSeriesMember, EventSeriesMember.event_id == Event.id)
                .where(EventSeriesMember.series_id == series_id)
                .order_by(EventSeriesMember.joined_at, Event.id)
            )
        ).all()
    )
    joined = [m.joined_at for _, m in rows]
    events = [
        {
            "id": str(e.id),
            "title": e.title,
            "status": e.status,
            "occurrence_start_date": (
                e.occurrence_start_date.isoformat() if e.occurrence_start_date else None
            ),
            "created_at": e.created_at.isoformat(),
            "joined_at": m.joined_at.isoformat(),
            "method": m.method,
        }
        for e, m in rows
    ]
    removals = [
        {"event_id": str(r.event_id), "reason": r.reason, "created_at": r.created_at.isoformat()}
        for r in await session.scalars(
            select(EventSeriesRemoval)
            .where(EventSeriesRemoval.series_id == series_id)
            .order_by(EventSeriesRemoval.created_at)
        )
    ]
    data = _series_data(series, len(events), max(joined, default=None))
    actions = list(
        await session.scalars(
            select(ResponseAction)
            .where(ResponseAction.series_id == series_id)
            .order_by(ResponseAction.created_at, ResponseAction.id)
        )
    )
    attempt = await latest_analysis(session, series_id, ok_only=False)
    return envelope(
        {
            **data,
            "events": events,
            "removals": removals,
            "analysis": await _analysis_data(session, series_id, actions),
            "latest_attempt": (
                {
                    "status": attempt.status,
                    "abstain_reason": attempt.abstain_reason,
                    "created_at": attempt.created_at.isoformat(),
                }
                if attempt
                else None
            ),
            "actions": [_action_data(a) for a in actions],
        }
    )


def _action_data(action: ResponseAction) -> dict[str, Any]:
    return {
        "id": str(action.id),
        "origin": action.origin,
        "analysis_id": str(action.analysis_id) if action.analysis_id else None,
        "measure_index": action.measure_index,
        "title": action.title,
        "owner": action.owner,
        "due_at": action.due_at.isoformat() if action.due_at else None,
        "state": action.state,
        "overdue": is_overdue(action),
        "created_at": action.created_at.isoformat(),
    }


async def _analysis_data(
    session: AsyncSession, series_id: uuid.UUID, actions: list[ResponseAction]
) -> dict[str, Any] | None:
    analysis = await latest_analysis(session, series_id)
    if analysis is None:
        return None
    decisions = await current_decisions(session, analysis.id)
    tracked = {a.measure_index: str(a.id) for a in actions if a.analysis_id == analysis.id}
    causes = []
    for cause in analysis.causes:
        decision = decisions.get(str(cause["cause_id"]))
        confirmed = decision is not None and decision.decision == CauseDecision.CONFIRMED
        causes.append(
            {
                **cause,
                "decision": "confirmed" if confirmed else "hypothesized",
                "label": CONFIRMED_LABEL if confirmed else cause["label"],
            }
        )
    return {
        "id": str(analysis.id),
        "status": analysis.status,
        "title": analysis.title,
        "summary": analysis.summary,
        "bursts": analysis.bursts,
        "location_groups": analysis.location_groups,
        "pattern_status": analysis.pattern_status,
        "pattern_abstain_reason": analysis.pattern_abstain_reason,
        "patterns": analysis.patterns,
        "causes": causes,
        "measures": [
            {**m, "tracked_action_id": tracked.get(i)} for i, m in enumerate(analysis.measures)
        ],
        "dropped": len(analysis.dropped or []),
        "created_at": analysis.created_at.isoformat(),
    }


def _analysis_error(error: AnalysisError) -> ApiError:
    if isinstance(error, AnalysisInvalidError):
        return ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(error),
            details=[{"field": f"body.{error.field}", "message": str(error)}],
        )
    if isinstance(error, AnalysisConflictError):
        return ApiError(ErrorCode.CONFLICT, str(error))
    return ApiError(ErrorCode.NOT_FOUND, str(error))


class SeriesPatch(BaseModel):
    """手動改名。"""

    title: Annotated[str, Field(min_length=1, max_length=300)]


@router.patch("/{series_id}")
async def rename(
    series_id: uuid.UUID, body: SeriesPatch, session: Session, principal: CanVerify
) -> dict[str, Any]:
    """手動改名；之後 S12 不覆蓋名稱。"""
    try:
        series = await rename_series(session, principal, series_id, body.title)
    except AnalysisError as e:
        raise _analysis_error(e) from None
    await session.commit()
    return envelope(
        {"id": str(series.id), "title": series.title, "title_source": series.title_source}
    )


class SeriesActionIn(BaseModel):
    """從建議建立（analysis_id + measure_index）或手動新增（title）。"""

    title: Annotated[str | None, Field(max_length=500)] = None
    analysis_id: uuid.UUID | None = None
    measure_index: Annotated[int | None, Field(ge=0)] = None
    owner: Annotated[str | None, Field(max_length=500)] = None
    due_at: AwareDatetime | None = None


@router.post("/{series_id}/actions", status_code=status.HTTP_201_CREATED)
async def add_action(
    series_id: uuid.UUID, body: SeriesActionIn, session: Session, principal: CanVerify
) -> dict[str, Any]:
    """新增系列層級的改善措施。"""
    from app.api.v1.admin.actions import _api_error

    try:
        action = await create_series_action(
            session,
            principal,
            series_id,
            title=body.title,
            analysis_id=body.analysis_id,
            measure_index=body.measure_index,
            owner=body.owner,
            due_at=body.due_at,
        )
    except ActionError as e:
        raise _api_error(e) from None
    await session.commit()
    await session.refresh(action)
    return envelope(_action_data(action))


class CauseDecisionIn(BaseModel):
    """人工確認（confirmed，必填依據）或回滾（hypothesized）。"""

    analysis_id: uuid.UUID
    decision: CauseDecision
    basis: Annotated[str | None, Field(max_length=500)] = None


@router.post("/{series_id}/causes/{cause_id}/decision", status_code=status.HTTP_201_CREATED)
async def decide_cause(
    series_id: uuid.UUID,
    cause_id: Annotated[str, Field(max_length=32)],
    body: CauseDecisionIn,
    session: Session,
    principal: CanVerify,
) -> dict[str, Any]:
    """人工確認或回滾推測原因。"""
    try:
        decision = await decide_series_cause(
            session,
            principal,
            series_id,
            body.analysis_id,
            cause_id,
            body.decision,
            basis=body.basis,
        )
    except AnalysisError as e:
        raise _analysis_error(e) from None
    await session.commit()
    return envelope(
        {
            "analysis_id": str(decision.analysis_id),
            "cause_id": decision.cause_id,
            "decision": decision.decision,
            "decided_at": decision.decided_at.isoformat(),
        }
    )


async def _markers(session: AsyncSession, actions: list[ResponseAction]) -> list[ActionMarker]:
    if not actions:
        return []
    titles = {a.id: a.title for a in actions}
    rows = await session.execute(
        select(
            ResponseActionTransition.action_id,
            ResponseActionTransition.to_state,
            func.min(ResponseActionTransition.created_at),
        )
        .where(
            ResponseActionTransition.action_id.in_(titles),
            ResponseActionTransition.to_state.in_(("handling", "improved")),
        )
        .group_by(ResponseActionTransition.action_id, ResponseActionTransition.to_state)
    )
    return [
        ActionMarker(
            action_id,
            titles[action_id],
            "started" if state == "handling" else "completed",
            taipei_date(at),
        )
        for action_id, state, at in rows.all()
    ]


@router.get("/{series_id}/timeline")
async def timeline(
    series_id: uuid.UUID,
    session: Session,
    _: CanRead,
    granularity: Literal["day", "week", "month"] = "week",
    range_key: Annotated[Literal["1M", "3M", "6M", "1Y", "all"], Query(alias="range")] = "6M",
    location: Annotated[str | None, Query(max_length=100)] = None,
) -> dict[str, Any]:
    """系列時間軸與措施前後比較。"""
    if await session.get(EventSeries, series_id) is None:
        raise ApiError(ErrorCode.NOT_FOUND, "找不到系列")
    events = await series_events(session, series_id)
    names = await location_names(session, events)
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    shown = [e for e in events if location is None or names.get(e.id) == location]
    dates: list[date] = [event_date(e) for e in shown]
    actions = list(
        await session.scalars(
            select(ResponseAction)
            .where(ResponseAction.series_id == series_id)
            .order_by(ResponseAction.created_at, ResponseAction.id)
        )
    )
    markers = await _markers(session, actions)
    today = taipei_date(datetime.now(UTC))
    result = build_timeline(
        dates, granularity=granularity, range_key=range_key, today=today, markers=markers
    )
    comparisons = []
    for marker in sorted(markers, key=lambda m: (m.on, str(m.action_id))):
        if marker.kind != "started":
            continue
        c = before_after(dates, marker.on, today=today)
        comparisons.append(
            {
                "action_id": str(marker.action_id),
                "title": marker.title,
                "start": c.start.isoformat(),
                "status": c.status,
                "before_events": c.before_events,
                "after_events": c.after_events,
                "after_weeks": round(c.after_weeks, 2),
                "before_weekly_average": c.before_weekly_average,
                "after_weekly_average": c.after_weekly_average,
                "change_ratio": c.change_ratio,
                "message": c.message,
                "caveat": c.caveat,
            }
        )
    return envelope(
        {
            "granularity": result.granularity,
            "range": result.range_key,
            "location": location,
            "start": result.start.isoformat(),
            "end": result.end.isoformat(),
            "points": [
                {
                    "period_start": p.period_start.isoformat(),
                    "count": p.count,
                    "moving_average": p.moving_average,
                }
                for p in result.points
            ],
            "markers": [
                {
                    "action_id": str(m.action_id),
                    "title": m.title,
                    "kind": m.kind,
                    "on": m.on.isoformat(),
                }
                for m in result.markers
            ],
            "comparisons": comparisons,
            "locations": [
                {"name": n, "event_count": c}
                for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        }
    )


@router.post("/{series_id}/events/{event_id}/split", status_code=status.HTTP_201_CREATED)
async def split(
    series_id: uuid.UUID, event_id: uuid.UUID, session: Session, principal: CanVerify
) -> dict[str, Any]:
    """把事件拆出系列。"""
    try:
        new = await split_from_series(session, principal, series_id, event_id)
    except SeriesNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    except SeriesConflictError as e:
        raise ApiError(ErrorCode.CONFLICT, str(e)) from None
    await session.commit()
    return envelope(
        {"event_id": str(event_id), "series_id": str(new), "from_series_id": str(series_id)}
    )


@router.post("/{series_id}/dissolve")
async def dissolve(series_id: uuid.UUID, session: Session, principal: CanVerify) -> dict[str, Any]:
    """解散系列。"""
    try:
        moved = await dissolve_series(session, principal, series_id)
    except SeriesNotFoundError as e:
        raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
    except SeriesConflictError as e:
        raise ApiError(ErrorCode.CONFLICT, str(e)) from None
    await session.commit()
    return envelope(
        {
            "series_id": str(series_id),
            "events": [{"event_id": str(e), "series_id": str(s)} for e, s in moved.items()],
        }
    )
