"""匿名回報收據碼的對應查詢（P8-T08）。

`report_receipts` 記錄每則投稿（原投稿或補件）屬於哪張收據碼（只存雜湊）。這裡把收件匣 id
對應到擷取後的來源文章（`anonymous_report` Adapter 的 external_id 為 `report-<id>`），
讓管線把補件掛到原投稿的事件、後台標出哪些證據是補件。
"""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.ingestion.adapters.anonymous_report import EXTERNAL_ID_PREFIX
from app.models.anonymous_report import ReportReceipt
from app.models.source import Source, SourceItem

SOURCE_CODE = "anonymous-report"
"""匿名回報的來源代碼（`config/sources.yaml`）。"""


def external_id(report_id: int) -> str:
    """收件匣 id 對應的來源文章 external_id。"""
    return f"{EXTERNAL_ID_PREFIX}{report_id}"


def _report_id(value: str | None) -> int | None:
    suffix = (value or "").removeprefix(EXTERNAL_ID_PREFIX)
    return int(suffix) if suffix.isdigit() else None


async def report_items(
    session: AsyncSession, report_ids: Collection[int]
) -> dict[int, tuple[uuid.UUID, str]]:
    """收件匣 id → （來源文章 id, 分析狀態）；還沒被擷取的投稿不在結果中。"""
    if not report_ids:
        return {}
    rows = await session.execute(
        select(SourceItem.external_id, SourceItem.id, SourceItem.analysis_status)
        .join(Source, Source.id == SourceItem.source_id)
        .where(
            Source.code == SOURCE_CODE,
            SourceItem.external_id.in_([external_id(r) for r in report_ids]),
        )
    )
    result: dict[int, tuple[uuid.UUID, str]] = {}
    for ext, item_id, status in rows.all():
        rid = _report_id(ext)
        if rid is not None:
            result[rid] = (item_id, str(status))
    return result


async def supplement_origins(
    session: AsyncSession, item_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID | None, str | None]]:
    """補件的來源文章 id → （原投稿的來源文章 id, 原投稿的分析狀態）。

    不是補件的文章不在結果中；原投稿還沒被擷取時，原投稿的文章 id 與狀態為 None。
    """
    if not item_ids:
        return {}
    rows = await session.execute(
        select(SourceItem.id, SourceItem.external_id)
        .join(Source, Source.id == SourceItem.source_id)
        .where(Source.code == SOURCE_CODE, SourceItem.id.in_(list(item_ids)))
    )
    by_report = {
        rid: item_id for item_id, ext in rows.all() if (rid := _report_id(ext)) is not None
    }
    if not by_report:
        return {}
    supplement = aliased(ReportReceipt)
    original = aliased(ReportReceipt)
    pairs = await session.execute(
        select(supplement.report_id, original.report_id)
        .join(
            original,
            (original.code_hash == supplement.code_hash) & original.is_supplement.is_(False),
        )
        .where(supplement.report_id.in_(list(by_report)), supplement.is_supplement.is_(True))
    )
    origin_of = dict(pairs.all())
    originals = await report_items(session, set(origin_of.values()))
    result: dict[uuid.UUID, tuple[uuid.UUID | None, str | None]] = {}
    for rid, origin in origin_of.items():
        found = originals.get(origin)
        result[by_report[rid]] = (found[0], found[1]) if found else (None, None)
    return result
