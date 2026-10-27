"""冪等寫入與版本（P1-T06；規格 F01「重試不重複寫入」、F02「來源版本、內容雜湊、去重」）。

- `source_items`：以 `INSERT ... ON CONFLICT DO UPDATE` 寫入。唯一鍵為 (source_id, external_id)；
  沒有 external_id 時改以 (source_id, url)，對應 0001_core 的兩個部分唯一索引。
  已存在的文章會更新網址、補上發布時間（不以空值覆寫）、把擷取時間推到最新（不倒退）。
- `source_items.tags`（P1-T15）：與既有標籤取聯集、排序、去重，標籤不會被移除；
  標籤不影響內容雜湊，只有標籤改變時不新增版本，計入 `unchanged`。
- `source_item_versions`：以 (item_id, content_hash) 唯一鍵 `ON CONFLICT DO NOTHING`，
  內容雜湊沒變就不新增版本；`captured_at` 為該次擷取時間。
- 交易：只 flush、不 commit（與 `record_audit` 相同）。呼叫端以 `async with session.begin():`
  包住整批，任何一筆失敗整批回滾，稽核紀錄也一併回滾。
- 每批寫入一筆稽核紀錄（`source_items.ingest`），metadata 只有筆數，不含內文。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from sqlalchemy import Boolean, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Permission, Principal, require
from app.ingestion.normalize import NormalizedItem, content_hash
from app.ingestion.registry import SourceNotApprovedError
from app.ingestion.types import AccessPolicy
from app.models.source import AnalysisStatus, Source, SourceItem, SourceItemVersion
from app.services.audit import record_audit

AUDIT_ACTION = "source_items.ingest"
_MERGE_TAGS = (
    "ARRAY(SELECT DISTINCT t FROM unnest(source_items.tags || excluded.tags) AS t ORDER BY t)"
)
"""ON CONFLICT 時把既有標籤與新標籤取聯集、排序、去重。"""


class UnknownSourceError(LookupError):
    """`sources` 表中沒有這個來源代碼。"""


@dataclass(frozen=True)
class StoreResult:
    """一批寫入的結果，供稽核與 ingestion_runs.counts（P1-T07）使用。

    - `new_items`：新文章（含第一個版本）
    - `changed_items`：既有文章、內容改變而新增版本
    - `unchanged`：既有文章、內容沒變（含同一批中的重複項目）
    """

    received: int
    new_items: int
    changed_items: int
    unchanged: int


async def store_items(
    session: AsyncSession,
    principal: Principal,
    source_code: str,
    items: Sequence[NormalizedItem],
    *,
    request_id: str | None = None,
) -> StoreResult:
    """把同一來源的一批標準化項目冪等寫入；只 flush 不 commit。

    權限不足拋出 PermissionDeniedError；來源不存在拋出 UnknownSourceError；
    來源未核准或停用拋出 SourceNotApprovedError；項目來源代碼不符或內容雜湊錯誤拋出 ValueError。
    以上檢查都在寫入任何資料之前完成。
    """
    require(principal, Permission.INGESTION_WRITE)
    if not items:
        return StoreResult(received=0, new_items=0, changed_items=0, unchanged=0)
    for item in items:
        if item.source_code != source_code:
            raise ValueError(f"項目的來源代碼 {item.source_code} 與寫入目標 {source_code} 不符")
        if item.content_hash != content_hash(item.text):
            raise ValueError(f"來源 {source_code} 有項目的 content_hash 與內容不符")
    source = await _approved_source(session, source_code)

    new_items = changed_items = unchanged = 0
    for item in items:
        item_id, inserted = await _upsert_item(session, source.id, item)
        version_added = await _add_version(session, item_id, item)
        if inserted:
            new_items += 1
        elif version_added:
            changed_items += 1
            await _reset_analysis(session, item_id)
        else:
            unchanged += 1

    result = StoreResult(
        received=len(items),
        new_items=new_items,
        changed_items=changed_items,
        unchanged=unchanged,
    )
    await record_audit(
        session,
        principal,
        AUDIT_ACTION,
        target=f"sources/{source_code}",
        request_id=request_id,
        metadata=asdict(result),
    )
    return result


async def _approved_source(session: AsyncSession, source_code: str) -> Source:
    source = (
        await session.execute(select(Source).where(Source.code == source_code))
    ).scalar_one_or_none()
    if source is None:
        raise UnknownSourceError(f"sources 表中沒有來源 {source_code}")
    try:
        approved = AccessPolicy(source.access_policy) is AccessPolicy.APPROVED
    except ValueError:
        approved = False
    if not approved or not source.enabled:
        raise SourceNotApprovedError(
            f"來源 {source_code} 未核准或未啟用"
            f"（access_policy={source.access_policy}, enabled={source.enabled}）"
        )
    return source


async def _upsert_item(
    session: AsyncSession, source_id: uuid.UUID, item: NormalizedItem
) -> tuple[uuid.UUID, bool]:
    """寫入或更新文章，回傳（id, 是否為新文章）。"""
    stmt = pg_insert(SourceItem).values(
        source_id=source_id,
        external_id=item.external_id,
        url=item.url,
        published_at=item.published_at,
        fetched_at=item.fetched_at,
        tags=sorted(item.tags),
    )
    updates = {
        "tags": text(_MERGE_TAGS),
        "published_at": func.coalesce(stmt.excluded.published_at, SourceItem.published_at),
        "fetched_at": func.greatest(stmt.excluded.fetched_at, SourceItem.fetched_at),
    }
    if item.external_id is not None:
        stmt = stmt.on_conflict_do_update(
            index_elements=[SourceItem.source_id, SourceItem.external_id],
            index_where=SourceItem.external_id.is_not(None),
            set_={**updates, "url": stmt.excluded.url},
        )
    else:
        stmt = stmt.on_conflict_do_update(
            index_elements=[SourceItem.source_id, SourceItem.url],
            index_where=SourceItem.external_id.is_(None),
            set_=updates,
        )
    # xmax = 0 表示這一列是本次 INSERT 新建，而不是 ON CONFLICT 更新
    inserted = literal_column("(xmax = 0)", Boolean).label("inserted")
    row = (await session.execute(stmt.returning(SourceItem.id, inserted))).one()
    return row[0], row[1]


async def _reset_analysis(session: AsyncSession, item_id: uuid.UUID) -> None:
    """內容改變的文章重新排入分析（P4-T07）。"""
    await session.execute(
        update(SourceItem)
        .where(SourceItem.id == item_id)
        .values(
            analysis_status=AnalysisStatus.PENDING, analysis_attempts=0, analysis_queued_at=None
        )
    )


async def _add_version(session: AsyncSession, item_id: uuid.UUID, item: NormalizedItem) -> bool:
    """內容雜湊是新的才新增版本；回傳是否新增。"""
    stmt = (
        pg_insert(SourceItemVersion)
        .values(
            item_id=item_id,
            content_hash=item.content_hash,
            normalized_text=item.text,
            captured_at=item.fetched_at,
        )
        .on_conflict_do_nothing(
            index_elements=[SourceItemVersion.item_id, SourceItemVersion.content_hash]
        )
        .returning(SourceItemVersion.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None
