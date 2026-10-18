"""P1-T09：從資料庫匯出待標記文章（最新內容版本、排除已標記、來源篩選）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.normalize import content_hash
from app.labeling.export import fetch_for_labeling
from app.models import Source, SourceItem, SourceItemVersion

pytestmark = pytest.mark.anyio
T0 = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)


async def add_item(
    session: AsyncSession,
    source: Source,
    n: int,
    *texts: str,
    published: datetime | None = None,
) -> SourceItem:
    item = SourceItem(
        source_id=source.id,
        external_id=str(n),
        url=f"https://example.ncu.edu.tw/{n}",
        published_at=published,
        fetched_at=T0,
    )
    session.add(item)
    await session.flush()
    for i, text in enumerate(texts):
        session.add(
            SourceItemVersion(
                item_id=item.id,
                content_hash=content_hash(text),
                normalized_text=text,
                captured_at=T0 + timedelta(hours=i),
            )
        )
    await session.commit()
    return item


async def add_source(session: AsyncSession, code: str) -> Source:
    source = Source(code=code, name=code, type="rpage", access_policy="approved")
    session.add(source)
    await session.commit()
    return source


async def test_exports_latest_version_of_each_item(session: AsyncSession) -> None:
    src = await add_source(session, "ncu-announcements")
    item = await add_item(session, src, 1, "停電通知（初版）", "停電通知（更正時間）", published=T0)
    [row] = await fetch_for_labeling(session, exclude=set(), limit=10)
    assert row.item_id == item.id
    assert row.source_code == "ncu-announcements"
    assert row.text == "停電通知（更正時間）"
    assert row.content_hash == content_hash("停電通知（更正時間）")
    assert row.published_at == T0
    assert row.url == "https://example.ncu.edu.tw/1"


async def test_excludes_labeled_items_and_respects_limit(session: AsyncSession) -> None:
    src = await add_source(session, "ncu-announcements")
    items = [
        await add_item(session, src, n, f"公告 {n}", published=T0 + timedelta(days=n))
        for n in range(1, 5)
    ]
    rows = await fetch_for_labeling(session, exclude={items[3].id}, limit=2)
    assert [r.item_id for r in rows] == [items[2].id, items[1].id]  # 新到舊、跳過已標記


async def test_source_filter_and_items_without_versions(session: AsyncSession) -> None:
    a = await add_source(session, "ncu-announcements")
    b = await add_source(session, "cwa-alerts")
    await add_item(session, a, 1, "公告")
    kept = await add_item(session, b, 2, "大雨特報")
    await add_item(session, b, 3)  # 沒有任何內容版本
    rows = await fetch_for_labeling(session, exclude=set(), limit=10, sources=["cwa-alerts"])
    assert [r.item_id for r in rows] == [kept.id]
