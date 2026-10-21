"""P2-T09：events.embedding（pgvector）與候選事件召回（migration 0006）。

召回條件：有向量、類別相同（可不限）、事件日期在視窗內、不是已封存的事件；依 cosine 距離排序。
事件沒有日期時，以建立時間（台北日期）代替。
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import find_candidate_events
from app.domain.states import EventStatus
from app.models import Event, Location
from app.models.event import EVENT_EMBEDDING_DIM

pytestmark = pytest.mark.anyio

DAY = date(2026, 10, 20)


def vec(*weights: float) -> list[float]:
    """前幾維依 weights 設定、其餘為 0，再正規化成單位向量。"""
    v = list(weights) + [0.0] * (EVENT_EMBEDDING_DIM - len(weights))
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


async def add_event(
    session: AsyncSession,
    title: str,
    embedding: list[float] | None,
    *,
    type_: str = "facility",
    on: date | None = DAY,
    status: EventStatus = EventStatus.DETECTED,
    location: Location | None = None,
    created_at: datetime | None = None,
) -> Event:
    event = Event(
        title=title,
        type=type_,
        status=status.value,
        occurrence_start_date=on,
        embedding=embedding,
        location_id=location.id if location else None,
    )
    if created_at is not None:
        event.created_at = created_at
    session.add(event)
    await session.flush()
    return event


async def test_vector_extension_and_columns_exist(session: AsyncSession) -> None:
    ext = await session.scalar(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
    assert ext == "vector"
    dims = await session.scalar(
        text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'events'::regclass AND attname = 'embedding'"
        )
    )
    assert dims == EVENT_EMBEDDING_DIM


async def test_embedding_roundtrip(session: AsyncSession) -> None:
    event = await add_event(session, "工五停電", vec(1, 0))
    await session.commit()
    stored = await session.get(Event, event.id)
    assert stored is not None and stored.embedding is not None
    assert len(stored.embedding) == EVENT_EMBEDDING_DIM
    assert stored.occurrence_start_date == DAY


async def test_candidates_are_ordered_by_cosine_distance(session: AsyncSession) -> None:
    loc = Location(name="工程五館")
    session.add(loc)
    await session.flush()
    near = await add_event(session, "工五停電", vec(1, 0.1), location=loc)
    far = await add_event(session, "工五電梯故障", vec(0.2, 1))
    mid = await add_event(session, "工五停電恢復", vec(1, 0.6))
    await session.commit()

    found = await find_candidate_events(session, vec(1, 0), on_date=DAY, category="facility")
    assert [c.event_id for c in found] == [near.id, mid.id, far.id]
    assert found[0].distance < found[1].distance < found[2].distance
    assert found[0].title == "工五停電"
    assert found[0].location == "工程五館"
    assert found[1].location is None
    assert found[0].category == "facility"
    assert found[0].occurrence_start_date == DAY


async def test_candidates_filter_category_window_status_and_missing_embedding(
    session: AsyncSession,
) -> None:
    keep = await add_event(session, "工五停電", vec(1, 0), on=date(2026, 10, 14))
    await add_event(session, "工五停電（太早）", vec(1, 0), on=date(2026, 10, 12))
    await add_event(session, "工五停電（太晚）", vec(1, 0), on=date(2026, 10, 28))
    await add_event(session, "颱風", vec(1, 0), type_="weather")
    await add_event(session, "沒有向量", None)
    await add_event(session, "已封存", vec(1, 0), status=EventStatus.ARCHIVED)
    await session.commit()

    found = await find_candidate_events(
        session, vec(1, 0), on_date=DAY, category="facility", window_days=7
    )
    assert [c.event_id for c in found] == [keep.id]

    any_category = await find_candidate_events(session, vec(1, 0), on_date=DAY, window_days=7)
    assert {c.title for c in any_category} == {"工五停電", "颱風"}


async def test_event_without_date_uses_taipei_created_date(session: AsyncSession) -> None:
    # UTC 10-12 17:00 = 台北 10-13 01:00 → 在 10-20 的 7 天視窗內
    inside = await add_event(
        session, "沒有日期 A", vec(1, 0), on=None, created_at=datetime(2026, 10, 12, 17, tzinfo=UTC)
    )
    # UTC 10-12 15:00 = 台北 10-12 23:00 → 視窗外
    await add_event(
        session, "沒有日期 B", vec(1, 0), on=None, created_at=datetime(2026, 10, 12, 15, tzinfo=UTC)
    )
    await session.commit()
    found = await find_candidate_events(session, vec(1, 0), on_date=DAY, window_days=7)
    assert [c.event_id for c in found] == [inside.id]


async def test_candidates_limit_max_distance_and_exclude(session: AsyncSession) -> None:
    a = await add_event(session, "A", vec(1, 0))
    b = await add_event(session, "B", vec(1, 0.2))
    c = await add_event(session, "C", vec(1, 0.4))
    await add_event(session, "D", vec(0, 1))
    await session.commit()

    top = await find_candidate_events(session, vec(1, 0), on_date=DAY, limit=2)
    assert [x.event_id for x in top] == [a.id, b.id]
    close = await find_candidate_events(session, vec(1, 0), on_date=DAY, max_distance=0.1)
    assert [x.event_id for x in close] == [a.id, b.id, c.id]
    excluded = await find_candidate_events(
        session, vec(1, 0), on_date=DAY, exclude=[a.id, uuid.uuid4()], limit=1
    )
    assert [x.event_id for x in excluded] == [b.id]


async def test_wrong_dimensions_are_rejected(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="維度"):
        await find_candidate_events(session, [1.0, 0.0], on_date=DAY)
