"""再發生偵測（P4-T10，G4-6）：同地點、同類別的事件是不是不只一次。

規則依 `config/recurrence.yaml`，完全確定性，不呼叫模型：
- 同地點、同類別的事件，從最近一次往回 `window_days` 天內（含）達到 `min_events` 件
  → `within_window`；
- 有改善措施達到「改進」（improved）的事件，改進日之後（不含當天）`after_improvement_days`
  天內又有同地點、同類別的事件 → `after_improvement`；
- 地點不明、類別為 none、查證為不實（false）的事件不列入；最近一次發生不在 `active_days`
  天內的組不顯示。

事件日期以 S02 抽取的發生日期（台北時間）為準，沒有時用建立時間的台北日期；改進日是該事件
最早一次轉到 improved 的台北日期。結果在通知中心讀取時重算（`find_recurrences`），不另存資料表，
地點與標題經 `mask_pii()`。提醒只顯示給使用者本人，不會遞送到其他管道。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.pii import mask_pii
from app.domain.states import EventStatus
from app.models.event import Event, Location
from app.models.response_action import ResponseAction, ResponseActionTransition

DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "config" / "recurrence.yaml"
TAIPEI = ZoneInfo("Asia/Taipei")
WITHIN_WINDOW = "within_window"
AFTER_IMPROVEMENT = "after_improvement"
REMINDER = "可能不是單純個案，要不要整批檢查？"
IGNORED_TYPES = frozenset({"none"})
IGNORED_STATUSES = frozenset({EventStatus.FALSE.value})


class RecurrencePolicy(BaseModel):
    """`config/recurrence.yaml` 的內容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=32)
    window_days: int = Field(ge=1, le=366)
    min_events: int = Field(ge=2, le=100)
    active_days: int = Field(ge=1, le=366)
    after_improvement_days: int = Field(ge=1, le=3660)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.after_improvement_days < self.window_days:
            raise ValueError("after_improvement_days 不得小於 window_days")
        return self


@dataclass(frozen=True)
class EventOccurrence:
    """判斷再發生所需的一個事件（由 `load_occurrences` 從資料庫組出）。"""

    event_id: uuid.UUID
    title: str
    event_type: str
    status: str
    location_id: uuid.UUID | None
    location_name: str | None
    occurred_on: date
    """台北時間的發生日期。"""
    improved_on: date | None
    """最早一項改善措施達到「改進」的台北日期；沒有則為 None。"""


@dataclass(frozen=True)
class RecurrenceEvent:
    """提醒中的一個事件；標題已遮蔽個資。"""

    event_id: uuid.UUID
    title: str
    status: str
    occurred_on: date
    improved_on: date | None


@dataclass(frozen=True)
class Recurrence:
    """同地點、同類別的一組再發生提醒；`events` 新到舊。"""

    location_id: uuid.UUID
    location_name: str | None
    event_type: str
    reasons: tuple[str, ...]
    events: tuple[RecurrenceEvent, ...]
    latest_on: date
    message: str
    policy_version: str


def load_policy(path: Path = DEFAULT_POLICY) -> RecurrencePolicy:
    """讀取並驗證再發生規則設定檔。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RecurrencePolicy.model_validate(raw)


@cache
def default_policy() -> RecurrencePolicy:
    """`config/recurrence.yaml` 的規則（讀一次後快取）。"""
    return load_policy()


def detect(
    events: Sequence[EventOccurrence], policy: RecurrencePolicy, *, today: date
) -> list[Recurrence]:
    """依規則找出再發生的組；同一輸入永遠得到同一結果（最近發生的組在前）。"""
    groups: dict[tuple[uuid.UUID, str], list[EventOccurrence]] = defaultdict(list)
    for e in events:
        if e.location_id is None or e.event_type in IGNORED_TYPES or e.status in IGNORED_STATUSES:
            continue
        groups[(e.location_id, e.event_type)].append(e)

    found = [
        r
        for (location_id, event_type), members in groups.items()
        if (r := _detect_group(location_id, event_type, members, policy, today)) is not None
    ]
    found.sort(key=lambda r: (-r.latest_on.toordinal(), str(r.location_id), r.event_type))
    return found


def _detect_group(
    location_id: uuid.UUID,
    event_type: str,
    members: list[EventOccurrence],
    policy: RecurrencePolicy,
    today: date,
) -> Recurrence | None:
    members = sorted(members, key=lambda e: (e.occurred_on, str(e.event_id)))
    latest = members[-1]
    if latest.occurred_on < today - timedelta(days=policy.active_days):
        return None

    reasons: list[str] = []
    picked: dict[uuid.UUID, EventOccurrence] = {}

    start = latest.occurred_on - timedelta(days=policy.window_days)
    in_window = [e for e in members if e.occurred_on >= start]
    if len(in_window) >= policy.min_events:
        reasons.append(WITHIN_WINDOW)
        picked.update((e.event_id, e) for e in in_window)

    span = timedelta(days=policy.after_improvement_days)
    after: dict[uuid.UUID, EventOccurrence] = {}
    for improved in members:
        if improved.improved_on is None:
            continue
        again = [
            e
            for e in members
            if e.event_id != improved.event_id
            and improved.improved_on < e.occurred_on <= improved.improved_on + span
        ]
        if again:
            after[improved.event_id] = improved
            after.update((e.event_id, e) for e in again)
    if after:
        reasons.append(AFTER_IMPROVEMENT)
        picked.update(after)

    if not reasons:
        return None
    ordered = sorted(picked.values(), key=lambda e: (e.occurred_on, str(e.event_id)), reverse=True)
    return Recurrence(
        location_id=location_id,
        location_name=mask_pii(latest.location_name) if latest.location_name else None,
        event_type=event_type,
        reasons=tuple(reasons),
        events=tuple(
            RecurrenceEvent(e.event_id, mask_pii(e.title), e.status, e.occurred_on, e.improved_on)
            for e in ordered
        ),
        latest_on=latest.occurred_on,
        message=REMINDER,
        policy_version=policy.policy_version,
    )


def _taipei_date(moment: datetime) -> date:
    return moment.astimezone(TAIPEI).date()


async def load_occurrences(
    session: AsyncSession, policy: RecurrencePolicy, *, today: date
) -> list[EventOccurrence]:
    """從資料庫讀出可能列入的事件：近期發生的，加上有改善措施達到「改進」的。"""
    improved_at = (
        select(
            ResponseAction.event_id.label("event_id"),
            func.min(ResponseActionTransition.created_at).label("at"),
        )
        .join(ResponseActionTransition, ResponseActionTransition.action_id == ResponseAction.id)
        .where(ResponseActionTransition.to_state == "improved")
        .group_by(ResponseAction.event_id)
        .subquery()
    )
    since = today - timedelta(
        days=policy.active_days + max(policy.window_days, policy.after_improvement_days)
    )
    since_at = datetime.combine(since, datetime.min.time(), TAIPEI)
    rows = await session.execute(
        select(
            Event.id,
            Event.title,
            Event.type,
            Event.status,
            Event.location_id,
            Event.occurrence_start_date,
            Event.created_at,
            Location.name,
            improved_at.c.at,
        )
        .join(Location, Location.id == Event.location_id)
        .outerjoin(improved_at, improved_at.c.event_id == Event.id)
        .where(
            Event.status.not_in(IGNORED_STATUSES),
            Event.type.not_in(IGNORED_TYPES),
            or_(
                Event.occurrence_start_date >= since,
                Event.occurrence_start_date.is_(None) & (Event.created_at >= since_at),
                improved_at.c.at.is_not(None),
            ),
        )
    )
    return [
        EventOccurrence(
            event_id=row.id,
            title=row.title,
            event_type=row.type,
            status=row.status,
            location_id=row.location_id,
            location_name=row.name,
            occurred_on=row.occurrence_start_date or _taipei_date(row.created_at),
            improved_on=_taipei_date(row.at) if row.at is not None else None,
        )
        for row in rows.all()
    ]


async def find_recurrences(
    session: AsyncSession,
    *,
    today: date | None = None,
    policy: RecurrencePolicy | None = None,
) -> list[Recurrence]:
    """依目前的事件與改善措施找出再發生提醒；`today` 預設為台北今天。"""
    rules = policy or default_policy()
    day = today or datetime.now(TAIPEI).date()
    return detect(await load_occurrences(session, rules, today=day), rules, today=day)
