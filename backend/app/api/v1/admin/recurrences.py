"""管理端再發生提醒 API（P4-T10）。

- `GET /api/v1/admin/recurrences`：通知中心的「可能不是單純個案，要不要整批檢查？」提醒。
  每次讀取時由 `app/resilience/recurrence.py` 的確定性規則依目前的事件與改善措施重算，
  不另存資料表、不分頁（只含最近仍在發生的組）；每組附同地點、同類別的事件，供逐一檢查。
  類別的中文名稱沿用 `config/notification-policy.yaml`。需要 `alerts:read_all`；只讀，不寫稽核。

提醒只顯示在管理端，不遞送到任何管道（ADR-0008）。
"""

from __future__ import annotations

import uuid
from datetime import date
from functools import cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.policy import NotificationPolicy
from app.alerts.policy import load_policy as load_notification_policy
from app.api.deps import get_db_session, require_permission
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.resilience.recurrence import default_policy, find_recurrences

router = APIRouter(prefix="/recurrences", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.ALERTS_READ_ALL))]


class RecurrenceEventOut(BaseModel):
    """一組提醒中的一個事件。"""

    id: uuid.UUID
    title: str
    status: str
    occurred_on: date
    improved_on: date | None


class RecurrenceOut(BaseModel):
    """同地點、同類別的一組再發生提醒。"""

    location_id: uuid.UUID
    location_name: str | None
    event_type: str
    event_type_label: str
    reasons: list[str]
    message: str
    latest_on: date
    events: list[RecurrenceEventOut]


@cache
def _notification_policy() -> NotificationPolicy:
    return load_notification_policy()


def _type_label(event_type: str) -> str:
    text = _notification_policy().event_types.get(event_type)
    return text.label if text else event_type


@router.get("")
async def list_recurrences(session: Session, _: CanRead) -> dict[str, Any]:
    """列出再發生提醒（最近發生的在前）；meta 附規則版本。"""
    policy = default_policy()
    found = await find_recurrences(session, policy=policy)
    data = [
        RecurrenceOut(
            location_id=r.location_id,
            location_name=r.location_name,
            event_type=r.event_type,
            event_type_label=_type_label(r.event_type),
            reasons=list(r.reasons),
            message=r.message,
            latest_on=r.latest_on,
            events=[
                RecurrenceEventOut(
                    id=e.event_id,
                    title=e.title,
                    status=e.status,
                    occurred_on=e.occurred_on,
                    improved_on=e.improved_on,
                )
                for e in r.events
            ],
        ).model_dump(mode="json")
        for r in found
    ]
    return envelope(data, policy_version=policy.policy_version)
