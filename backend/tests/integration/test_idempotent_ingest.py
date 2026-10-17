"""P1-T06：冪等寫入與版本（同批資料重複寫入筆數不變；內容改變才新增版本）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import PermissionDeniedError, Principal, Role
from app.ingestion.normalize import NormalizedItem, content_hash
from app.ingestion.registry import SourceNotApprovedError
from app.ingestion.store import StoreResult, UnknownSourceError, store_items
from app.models.audit import AuditLog
from app.models.source import Source, SourceItem, SourceItemVersion

pytestmark = pytest.mark.anyio

INGESTION = Principal(
    kind="service", subject="ingestion", roles=frozenset({Role.SERVICE_INGESTION})
)
AI = Principal(kind="service", subject="ai", roles=frozenset({Role.SERVICE_AI}))
T0 = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)


def item(
    external_id: str | None = "5003", text: str = "停水通知\n\n10/20 停水", **kw: Any
) -> NormalizedItem:
    fields: dict[str, Any] = {
        "source_code": "ncu-announcements",
        "external_id": external_id,
        "url": f"https://www.ncu.edu.tw/p/406-1000-{external_id or 'x'},r15.php?Lang=zh-tw",
        "title": text.split("\n", 1)[0],
        "text": text,
        "language": "zh",
        "content_hash": content_hash(text),
        "published_at": datetime(2026, 10, 16, 16, 0, tzinfo=UTC),
        "fetched_at": T0,
    }
    fields.update(kw)
    return NormalizedItem(**fields)


def batch() -> list[NormalizedItem]:
    return [
        item("5001", "網路維護\n\n10/18 網路維護"),
        item("5002", "講座延期"),
        item("5003", "停水通知\n\n10/20 停水"),
    ]


async def add_source(
    session: AsyncSession,
    code: str = "ncu-announcements",
    *,
    access_policy: str = "approved",
    enabled: bool = True,
) -> Source:
    src = Source(code=code, name=code, type="rpage", access_policy=access_policy, enabled=enabled)
    session.add(src)
    await session.commit()
    return src


async def count(session: AsyncSession, model: type[Any]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def store(session: AsyncSession, items: list[NormalizedItem], **kw: Any) -> StoreResult:
    async with session.begin():
        return await store_items(session, INGESTION, "ncu-announcements", items, **kw)


# --- 冪等 ------------------------------------------------------------------------------


async def test_same_batch_three_times_keeps_counts(session: AsyncSession) -> None:
    await add_source(session)
    first = await store(session, batch())
    assert first == StoreResult(received=3, new_items=3, changed_items=0, unchanged=0)

    for _ in range(2):
        again = await store(session, batch())
        assert again == StoreResult(received=3, new_items=0, changed_items=0, unchanged=3)

    assert await count(session, SourceItem) == 3
    assert await count(session, SourceItemVersion) == 3


async def test_changed_content_adds_a_version(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, batch())
    changed = [
        item("5003", "停水通知\n\n10/20 停水（延長至 17:00）", fetched_at=T0 + timedelta(hours=1))
    ]

    result = await store(session, changed)
    assert result == StoreResult(received=1, new_items=0, changed_items=1, unchanged=0)
    assert await count(session, SourceItem) == 3
    assert await count(session, SourceItemVersion) == 4

    item_id = (
        await session.execute(select(SourceItem.id).where(SourceItem.external_id == "5003"))
    ).scalar_one()
    versions = (
        (
            await session.execute(
                select(SourceItemVersion)
                .where(SourceItemVersion.item_id == item_id)
                .order_by(SourceItemVersion.captured_at)
            )
        )
        .scalars()
        .all()
    )
    assert [v.normalized_text for v in versions] == [
        "停水通知\n\n10/20 停水",
        "停水通知\n\n10/20 停水（延長至 17:00）",
    ]
    # captured_at 為擷取時間（aware UTC），可反查各版本何時取得
    assert [v.captured_at for v in versions] == [T0, T0 + timedelta(hours=1)]
    assert all(v.content_hash == content_hash(v.normalized_text) for v in versions)


async def test_duplicates_within_one_batch_are_written_once(session: AsyncSession) -> None:
    await add_source(session)
    result = await store(session, [item("5003"), item("5003")])
    assert result == StoreResult(received=2, new_items=1, changed_items=0, unchanged=1)
    assert await count(session, SourceItem) == 1
    assert await count(session, SourceItemVersion) == 1


async def test_items_without_external_id_deduplicate_by_url(session: AsyncSession) -> None:
    await add_source(session)
    url = "https://news.example.edu/posts/9"
    for _ in range(3):
        await store(session, [item(None, url=url)])
    rows = (await session.execute(select(SourceItem))).scalars().all()
    assert len(rows) == 1
    assert rows[0].external_id is None
    assert rows[0].url == url


async def test_existing_item_keeps_provenance_fields_up_to_date(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, [item("5003", published_at=None)])
    later = T0 + timedelta(days=1)
    await store(
        session,
        [
            item(
                "5003",
                url="https://www.ncu.edu.tw/p/406-1000-5003,r15.php",
                published_at=datetime(2026, 10, 16, 16, 0, tzinfo=UTC),
                fetched_at=later,
            )
        ],
    )
    row = (await session.execute(select(SourceItem))).scalar_one()
    assert row.url == "https://www.ncu.edu.tw/p/406-1000-5003,r15.php"
    assert row.published_at == datetime(2026, 10, 16, 16, 0, tzinfo=UTC)
    assert row.fetched_at == later  # 最近一次擷取時間
    await session.commit()  # 結束讀取用的交易

    # 之後的擷取沒有發布時間或時間較舊時，不覆寫已知的值
    await store(session, [item("5003", published_at=None, fetched_at=T0)])
    await session.refresh(row)
    assert row.published_at == datetime(2026, 10, 16, 16, 0, tzinfo=UTC)
    assert row.fetched_at == later


async def test_same_external_id_in_other_source_is_a_different_item(
    session: AsyncSession,
) -> None:
    await add_source(session)
    await add_source(session, "ncu-sec")
    await store(session, [item("5003")])
    async with session.begin():
        await store_items(session, INGESTION, "ncu-sec", [item("5003", source_code="ncu-sec")])
    assert await count(session, SourceItem) == 2


# --- 交易與防護 ------------------------------------------------------------------------


async def test_failure_rolls_back_the_whole_batch(session: AsyncSession) -> None:
    await add_source(session)
    bad = item("5099", source_code="ncu-sec")  # 來源代碼不符
    with pytest.raises(ValueError):
        await store(session, [*batch(), bad])
    assert await count(session, SourceItem) == 0
    assert await count(session, SourceItemVersion) == 0
    assert await count(session, AuditLog) == 0


async def test_store_does_not_commit_by_itself(session: AsyncSession) -> None:
    await add_source(session)
    await store_items(session, INGESTION, "ncu-announcements", batch())
    await session.rollback()
    assert await count(session, SourceItem) == 0


async def test_unknown_source_is_rejected(session: AsyncSession) -> None:
    with pytest.raises(UnknownSourceError):
        await store(session, batch())


@pytest.mark.parametrize(
    ("access_policy", "enabled"),
    [("pending", True), ("deferred", True), ("rejected", True), ("approved", False)],
)
async def test_unapproved_or_disabled_source_is_rejected(
    session: AsyncSession, access_policy: str, enabled: bool
) -> None:
    await add_source(session, access_policy=access_policy, enabled=enabled)
    with pytest.raises(SourceNotApprovedError):
        await store(session, batch())
    assert await count(session, SourceItem) == 0


async def test_principal_without_ingestion_write_is_rejected(session: AsyncSession) -> None:
    await add_source(session)
    with pytest.raises(PermissionDeniedError):
        async with session.begin():
            await store_items(session, AI, "ncu-announcements", batch())
    assert await count(session, SourceItem) == 0


# --- 稽核 ------------------------------------------------------------------------------


async def test_each_batch_writes_one_audit_log_without_content(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, batch(), request_id="run-1")
    await store(session, batch(), request_id="run-2")

    logs = (await session.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars().all()
    assert [log.request_id for log in logs] == ["run-1", "run-2"]
    first = logs[0]
    assert first.actor == "service:ingestion"
    assert first.action == "source_items.ingest"
    assert first.target == "sources/ncu-announcements"
    assert first.meta == {"received": 3, "new_items": 3, "changed_items": 0, "unchanged": 0}
    assert "停水" not in str(first.meta)
    assert logs[1].meta["unchanged"] == 3


async def test_empty_batch_writes_nothing(session: AsyncSession) -> None:
    await add_source(session)
    result = await store(session, [])
    assert result == StoreResult(received=0, new_items=0, changed_items=0, unchanged=0)
    assert await count(session, AuditLog) == 0
