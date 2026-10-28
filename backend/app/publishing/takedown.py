"""一鍵下架（P5-T14，ADR-0015）：使用者在後台立即撤下公開內容。

- `take_down()`：published 或 withdrawn 的公開狀態改為 taken_down。公開 API（P5-T11）每次請求都
  只讀 state 為 published 的版本，commit 後立即不再回傳；回應的 ETag 依內容計算，舊 ETag 不再
  得到 304。下架後同步（`sync.py`）一律不覆蓋，即使事件之後又符合公開規則。
  每次下架新增一筆 publication_takedowns，並寫入稽核 `publication.take_down`（同一個 request_id）。
- `restore()`：重新上架只把 taken_down 改回 withdrawn，再交給公開規則重新判斷；是否公開、
  公開到什麼程度仍由規則決定，使用者不能直接公開（AGENTS.md）。寫入稽核 `publication.restore`。

兩者都以 `SELECT ... FOR UPDATE` 鎖住 publication，`sync_publication()` 也一樣，避免同步在下架
的同時把狀態寫回 published。只 flush，不 commit。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal
from app.models.publication import Publication, PublicationTakedown
from app.publishing.sync import sync_publication
from app.services.audit import prepare_metadata, record_audit

TAKEABLE_STATES = frozenset({"published", "withdrawn"})


class TakedownError(Exception):
    """下架或重新上架失敗。"""


class TakedownNotFoundError(TakedownError):
    """找不到公開狀態。"""


class TakedownConflictError(TakedownError):
    """目前狀態不能下架（已下架）或不能重新上架（未下架）。"""


@dataclass(frozen=True)
class TakedownResult:
    """操作後的狀態與操作前的狀態。"""

    publication_id: uuid.UUID
    state: str
    previous: str


async def _locked(session: AsyncSession, publication_id: uuid.UUID) -> Publication:
    pub = await session.scalar(
        select(Publication)
        .where(Publication.id == publication_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if pub is None:
        raise TakedownNotFoundError("找不到公開內容")
    return pub


async def take_down(
    session: AsyncSession,
    publication_id: uuid.UUID,
    principal: Principal,
    *,
    reason: str | None = None,
) -> TakedownResult:
    """下架；已下架拋出 TakedownConflictError，原因含個資拋出 AuditMetadataError。"""
    reason = (reason or "").strip() or None
    pub = await _locked(session, publication_id)
    previous = pub.state
    if previous not in TAKEABLE_STATES:
        raise TakedownConflictError("已經下架")
    metadata: dict[str, object] = {
        "event_id": pub.event_id,
        "version": pub.version,
        "from": previous,
    }
    if reason is not None:
        metadata["reason"] = reason
    prepare_metadata(metadata)  # 先檢查，含個資時不改任何狀態

    pub.state = "taken_down"
    pub.taken_down_at = datetime.now(UTC)
    log = await record_audit(
        session,
        principal,
        "publication.take_down",
        target=f"publication:{pub.id}",
        metadata=metadata,
    )
    session.add(
        PublicationTakedown(
            publication_id=pub.id,
            version=pub.version,
            reason=reason,
            actor=principal.actor,
            request_id=log.request_id,
        )
    )
    await session.flush()
    return TakedownResult(pub.id, pub.state, previous)


async def restore(
    session: AsyncSession, publication_id: uuid.UUID, principal: Principal
) -> TakedownResult:
    """重新上架：改回 withdrawn 後依公開規則重新判斷；未下架拋出 TakedownConflictError。"""
    pub = await _locked(session, publication_id)
    if pub.state != "taken_down":
        raise TakedownConflictError("沒有下架，不需要重新上架")
    pub.state = "withdrawn"
    pub.taken_down_at = None
    await session.flush()
    action = await sync_publication(session, pub.event_id)
    await record_audit(
        session,
        principal,
        "publication.restore",
        target=f"publication:{pub.id}",
        metadata={"event_id": pub.event_id, "version": pub.version, "sync": action.value},
    )
    return TakedownResult(pub.id, pub.state, "taken_down")


__all__ = [
    "TAKEABLE_STATES",
    "TakedownConflictError",
    "TakedownError",
    "TakedownNotFoundError",
    "TakedownResult",
    "restore",
    "take_down",
]
