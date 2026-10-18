"""P1-T15：source_items.tags（寫入、與既有標籤取聯集、只有標籤改變時不新增版本）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal, Role
from app.ingestion.normalize import NormalizedItem, content_hash
from app.ingestion.store import StoreResult, store_items
from app.models import Source, SourceItem, SourceItemVersion

pytestmark = pytest.mark.anyio
T0 = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
INGESTION = Principal(
    kind="service", subject="ingestion", roles=frozenset({Role.SERVICE_INGESTION})
)


def item(
    text_: str = "停水通知\n\n10/20 停水", tags: frozenset[str] = frozenset(), **kw: Any
) -> NormalizedItem:
    fields: dict[str, Any] = {
        "source_code": "ncu-cc",
        "external_id": "5003",
        "url": "https://www.cc.ncu.edu.tw/p/406-1033-5003,r16.php",
        "title": text_.split("\n", 1)[0],
        "text": text_,
        "language": "zh",
        "content_hash": content_hash(text_),
        "published_at": T0,
        "fetched_at": T0,
        "tags": tags,
    }
    fields.update(kw)
    return NormalizedItem(**fields)


async def add_source(session: AsyncSession) -> None:
    session.add(Source(code="ncu-cc", name="ncu-cc", type="rpage", access_policy="approved"))
    await session.commit()


async def store(session: AsyncSession, *items: NormalizedItem) -> StoreResult:
    async with session.begin():
        return await store_items(session, INGESTION, "ncu-cc", list(items))


async def tags_of(session: AsyncSession) -> list[str]:
    return list((await session.execute(select(SourceItem.tags))).scalar_one())


async def versions(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(SourceItemVersion))).scalar_one()


async def test_new_item_stores_sorted_tags(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, item(tags=frozenset({"資安訊息", "宿舍網路"})))
    assert await tags_of(session) == sorted(["資安訊息", "宿舍網路"])


async def test_item_without_tags_defaults_to_empty_array(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, item())
    assert await tags_of(session) == []


async def test_new_tags_are_merged_without_new_version(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, item(tags=frozenset({"系統維護"})))
    result = await store(
        session, item(tags=frozenset({"宿舍網路"}), fetched_at=T0 + timedelta(hours=1))
    )
    assert await tags_of(session) == sorted(["系統維護", "宿舍網路"])
    assert await versions(session) == 1  # 只有標籤改變，不新增內容版本
    assert result.unchanged == 1


async def test_tags_are_never_removed(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, item(tags=frozenset({"系統維護"})))
    await store(session, item())
    assert await tags_of(session) == ["系統維護"]


async def test_content_change_adds_version_and_merges_tags(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, item(tags=frozenset({"系統維護"})))
    result = await store(
        session, item("停水通知（更正）", tags=frozenset({"系統維護", "行政公告"}))
    )
    assert result.changed_items == 1
    assert await versions(session) == 2
    assert await tags_of(session) == sorted(["系統維護", "行政公告"])


async def test_duplicates_in_one_batch_merge_tags(session: AsyncSession) -> None:
    await add_source(session)
    await store(session, item(tags=frozenset({"a"})), item(tags=frozenset({"b"})))
    assert await tags_of(session) == ["a", "b"]


async def test_column_default_is_empty_array(session: AsyncSession) -> None:
    row = (
        await session.execute(
            text(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'source_items' AND column_name = 'tags'"
            )
        )
    ).one()
    assert "'{}'" in row[0]
    assert row[1] == "NO"
