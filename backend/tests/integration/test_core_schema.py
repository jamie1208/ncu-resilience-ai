"""P0-T05：核心資料表的唯一約束、索引與時間型別（規格 §9）。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditLog,
    Event,
    EventEvidence,
    IngestionRun,
    Location,
    Source,
    SourceItem,
    SourceItemVersion,
)

pytestmark = pytest.mark.anyio


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def make_source(s: AsyncSession, code: str = "ncu-announcements") -> Source:
    src = Source(
        code=code, name="校園公告", type="html", access_policy="approved", poll_interval=1800
    )
    s.add(src)
    await s.flush()
    return src


async def expect_integrity_error(s: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await s.flush()
    await s.rollback()


# --- sources -----------------------------------------------------------------


async def test_source_code_is_unique(session: AsyncSession) -> None:
    await make_source(session)
    session.add(
        Source(code="ncu-announcements", name="重複", type="html", access_policy="approved")
    )
    await expect_integrity_error(session)


async def test_source_defaults(session: AsyncSession) -> None:
    src = await make_source(session)
    await session.commit()
    await session.refresh(src)
    assert src.enabled is True
    assert isinstance(src.id, uuid.UUID)
    assert src.created_at.utcoffset() == timedelta(0)


# --- source_items：unique(source_id, external_id)，external_id 為空時以 url 替代 ---------


async def test_item_external_id_unique_per_source(session: AsyncSession) -> None:
    src = await make_source(session)
    session.add(SourceItem(source_id=src.id, external_id="2079", url="https://x/2079"))
    await session.flush()
    session.add(SourceItem(source_id=src.id, external_id="2079", url="https://x/other"))
    await expect_integrity_error(session)


async def test_same_external_id_allowed_in_different_sources(session: AsyncSession) -> None:
    a = await make_source(session, "src-a")
    b = await make_source(session, "src-b")
    session.add_all(
        [
            SourceItem(source_id=a.id, external_id="1", url="https://a/1"),
            SourceItem(source_id=b.id, external_id="1", url="https://b/1"),
        ]
    )
    await session.flush()


async def test_null_external_id_falls_back_to_url_uniqueness(session: AsyncSession) -> None:
    src = await make_source(session)
    session.add(SourceItem(source_id=src.id, external_id=None, url="https://x/a"))
    session.add(SourceItem(source_id=src.id, external_id=None, url="https://x/b"))
    await session.flush()
    session.add(SourceItem(source_id=src.id, external_id=None, url="https://x/a"))
    await expect_integrity_error(session)


async def test_source_with_items_cannot_be_deleted(session: AsyncSession) -> None:
    src = await make_source(session)
    session.add(SourceItem(source_id=src.id, external_id="1", url="https://x/1"))
    await session.commit()
    await session.delete(src)
    await expect_integrity_error(session)


# --- source_item_versions：unique(item_id, content_hash) -----------------------------


async def test_version_content_hash_unique_per_item(session: AsyncSession) -> None:
    src = await make_source(session)
    item = SourceItem(source_id=src.id, external_id="1", url="https://x/1")
    session.add(item)
    await session.flush()
    session.add(SourceItemVersion(item_id=item.id, content_hash=sha("v1"), normalized_text="v1"))
    session.add(SourceItemVersion(item_id=item.id, content_hash=sha("v2"), normalized_text="v2"))
    await session.flush()
    session.add(SourceItemVersion(item_id=item.id, content_hash=sha("v1"), normalized_text="v1"))
    await expect_integrity_error(session)


# --- events / event_evidence / locations -------------------------------------------


async def test_event_evidence_compound_primary_key(session: AsyncSession) -> None:
    src = await make_source(session)
    item = SourceItem(source_id=src.id, external_id="1", url="https://x/1")
    event = Event(title="宿舍停電", type="infrastructure", status="detected")
    session.add_all([item, event])
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    await expect_integrity_error(session)


async def test_event_location_is_optional(session: AsyncSession) -> None:
    unknown = Location(name="校內（位置不明）", lat=None, lng=None, precision="unknown")
    session.add_all([unknown, Event(title="無地點事件", type="other", status="detected")])
    await session.flush()


async def test_event_with_location(session: AsyncSession) -> None:
    loc = Location(name="男三舍", lat=24.9685, lng=121.1932, precision="building")
    session.add(loc)
    await session.flush()
    ev = Event(title="宿舍停電", type="infrastructure", status="detected", location_id=loc.id)
    session.add(ev)
    await session.commit()
    got = (await session.execute(select(Event).where(Event.id == ev.id))).scalar_one()
    assert got.location_id == loc.id


async def test_core_indexes_exist(session: AsyncSession) -> None:
    def indexes(conn: object) -> dict[str, set[tuple[str, ...]]]:
        insp = inspect(conn)
        return {
            t: {tuple(i["column_names"]) for i in insp.get_indexes(t)}  # type: ignore[misc]
            for t in ("events", "ingestion_runs")
        }

    conn = await session.connection()
    idx = await conn.run_sync(indexes)
    assert {("type",), ("status",), ("created_at",)} <= idx["events"]
    assert ("source_id", "started_at") in idx["ingestion_runs"]


# --- ingestion_runs / audit_logs -----------------------------------------------------


async def test_ingestion_run_counts_and_aware_utc_times(session: AsyncSession) -> None:
    src = await make_source(session)
    started = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    run = IngestionRun(source_id=src.id, started_at=started, status="succeeded", counts={"new": 3})
    session.add(run)
    await session.commit()
    await session.refresh(run)
    assert run.started_at == started
    assert run.started_at.utcoffset() == timedelta(0)
    assert run.counts == {"new": 3}


async def test_audit_log_records_request_id_and_metadata(session: AsyncSession) -> None:
    log = AuditLog(
        actor="system",
        action="source.create",
        target="sources/ncu-announcements",
        request_id="req-12345678",
        meta={"fields": ["code"]},
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)
    assert log.meta == {"fields": ["code"]}
    assert log.created_at.utcoffset() == timedelta(0)
