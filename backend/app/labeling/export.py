"""匯出待標記文章（P1-T09，ADR-0009）。

從資料庫取出尚未標記的文章（每篇取最新內容版本），加上規則式預標，寫成 CSV 讓使用者在
試算表填寫。CSV 含內文，只能放在 `data/raw/labeling/`（不進 git）；匯入時不保留內文。
"""

from __future__ import annotations

import csv
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.labeling.prelabel import prelabel
from app.models import Source, SourceItem, SourceItemVersion

LABEL_COLUMNS: tuple[str, ...] = (
    "is_campus_relevant",
    "category",
    "claim_type",
    "verification",
    "event_group",
    "related_groups",
    "high_risk",
    "mentions_person",
    "tags",
)
"""使用者填寫的欄位；多值欄位（related_groups、tags）以分號分隔，布林欄位填 y／n。"""

EXPORT_COLUMNS: tuple[str, ...] = (
    "item_id",
    "source_code",
    "content_hash",
    "published_at",
    "url",
    "text",
    *LABEL_COLUMNS,
    "confirm",
)
"""匯出 CSV 的欄位順序；`confirm` 填 y 的列才會匯入。"""


@dataclass(frozen=True)
class ExportRow:
    """一篇待標記文章。`text` 是來源內容（不可信輸入），只寫進本機工作檔。"""

    item_id: uuid.UUID
    source_code: str
    content_hash: str
    published_at: datetime | None
    url: str
    text: str


async def fetch_for_labeling(
    session: AsyncSession,
    *,
    exclude: Collection[uuid.UUID],
    limit: int,
    sources: Collection[str] | None = None,
) -> list[ExportRow]:
    """取出尚未標記的文章，依發布時間新到舊；每篇取最新的內容版本，沒有版本的文章略過。"""
    latest = (
        select(
            SourceItemVersion.item_id,
            SourceItemVersion.content_hash,
            SourceItemVersion.normalized_text,
        )
        .ext(distinct_on(SourceItemVersion.item_id))
        .order_by(SourceItemVersion.item_id, SourceItemVersion.captured_at.desc())
        .subquery()
    )
    query = (
        select(
            SourceItem.id,
            Source.code,
            latest.c.content_hash,
            SourceItem.published_at,
            SourceItem.url,
            latest.c.normalized_text,
        )
        .join(Source, Source.id == SourceItem.source_id)
        .join(latest, latest.c.item_id == SourceItem.id)
        .order_by(SourceItem.published_at.desc().nulls_last(), SourceItem.id)
        .limit(limit)
    )
    if exclude:
        query = query.where(SourceItem.id.not_in(list(exclude)))
    if sources:
        query = query.where(Source.code.in_(list(sources)))
    result = await session.execute(query)
    return [
        ExportRow(
            item_id=item_id,
            source_code=code,
            content_hash=digest,
            published_at=published,
            url=url,
            text=text,
        )
        for item_id, code, digest, published, url, text in result.all()
    ]


def _yes_no(value: bool | None) -> str:
    return "" if value is None else ("y" if value else "n")


def write_export_csv(path: Path, rows: Iterable[ExportRow]) -> int:
    """寫出含規則式預標的 CSV（UTF-8 加 BOM，Excel 才能正確顯示中文）；回傳筆數。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(EXPORT_COLUMNS))
        writer.writeheader()
        for row in rows:
            hint = prelabel(row.source_code, row.text)
            writer.writerow(
                {
                    "item_id": str(row.item_id),
                    "source_code": row.source_code,
                    "content_hash": row.content_hash,
                    "published_at": row.published_at.isoformat() if row.published_at else "",
                    "url": row.url,
                    "text": row.text,
                    "is_campus_relevant": _yes_no(hint.is_campus_relevant),
                    "category": hint.category or "",
                    "claim_type": hint.claim_type or "",
                    "verification": hint.verification or "",
                    "event_group": "",
                    "related_groups": "",
                    "high_risk": "",
                    "mentions_person": "",
                    "tags": "",
                    "confirm": "",
                }
            )
            count += 1
    return count
