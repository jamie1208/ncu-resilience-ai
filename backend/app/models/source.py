"""來源與擷取：sources、ingestion_runs、source_items、source_item_versions（規格 §9）。

來源與原文刪除採 RESTRICT：provenance 不可因誤刪來源而連帶消失。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class Source(Base):
    """已核准的資料來源；`code` 對應 docs/governance/source-inventory.md 的代碼。"""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(32))
    access_policy: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    poll_interval: Mapped[int | None] = mapped_column(Integer, comment="擷取間隔（秒）")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class IngestionRun(Base):
    """一次來源擷取的執行紀錄。"""

    __tablename__ = "ingestion_runs"
    __table_args__ = (Index(None, "source_id", "started_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class SourceItem(Base):
    """來源中的一篇文章或公告。

    唯一性：有 external_id 時為 (source_id, external_id)；external_id 為空時改以 (source_id, url)。
    """

    __tablename__ = "source_items"
    __table_args__ = (
        Index(
            "uq_source_items_source_id_external_id",
            "source_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_source_items_source_id_url_no_external_id",
            "source_id",
            "url",
            unique=True,
            postgresql_where=text("external_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    external_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceItemVersion(Base):
    """文章的一個內容版本；內容雜湊相同則不重複保存。"""

    __tablename__ = "source_item_versions"
    __table_args__ = (UniqueConstraint("item_id", "content_hash"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_items.id", ondelete="RESTRICT"))
    content_hash: Mapped[str] = mapped_column(String(64), comment="正規化內容的 SHA-256（hex）")
    normalized_text: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
