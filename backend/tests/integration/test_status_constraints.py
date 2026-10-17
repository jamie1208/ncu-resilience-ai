"""P0-T07（併入 P0-T06 決定）：狀態欄位的 CHECK 約束與 app/domain/states.py 一致。"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.states import EventStatus
from app.models import Event

pytestmark = pytest.mark.anyio


async def test_invalid_event_status_is_rejected(session: AsyncSession) -> None:
    session.add(Event(title="x", type="other", status="confirmed"))
    with pytest.raises(IntegrityError, match="ck_events_status"):
        await session.flush()
    await session.rollback()


async def test_every_event_status_is_accepted(session: AsyncSession) -> None:
    session.add_all(Event(title=s.value, type="other", status=s.value) for s in EventStatus)
    await session.flush()


async def test_constraint_values_match_enum(session: AsyncSession) -> None:
    definition = (
        await session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_events_status'"
            )
        )
    ).scalar_one()
    assert set(re.findall(r"'([a-z_]+)'", definition)) == {s.value for s in EventStatus}
