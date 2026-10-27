"""來源與擷取：sources、ingestion_runs、source_items、source_item_versions（規格 §9），
以及 P1-T07 新增的 ingestion_dead_letters 與 sources.cursor。

來源與原文刪除採 RESTRICT：provenance 不可因誤刪來源而連帶消失。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class AnalysisStatus(StrEnum):
    """source_items.analysis_status（P4-T07）。"""

    PENDING = "pending"  # 等待掃描排入
    QUEUED = "queued"  # 已排入 Celery
    DONE = "done"  # 管線已完成（含 S01 判斷無關、abstain）
    SKIPPED = "skipped"  # 超過分析時間窗，不分析
    FAILED = "failed"  # 失敗次數達上限


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
    cursor: Mapped[str | None] = mapped_column(
        Text, comment="增量擷取的 cursor（Adapter 自訂的不透明字串，P1-T07）"
    )
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
        CheckConstraint(
            f"analysis_status IN ({', '.join(repr(s.value) for s in AnalysisStatus)})",
            name="analysis_status",
        ),
        Index(
            "ix_source_items_analysis_pending",
            "analysis_status",
            postgresql_where=text("analysis_status IN ('pending', 'queued')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    external_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("'{}'"),
        comment="來源分類標籤（P1-T15），依字母排序、不重複",
    )
    analysis_status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'pending'"),
        comment="分析管線狀態（P4-T07）：pending、queued、done、skipped、failed",
    )
    analysis_attempts: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), comment="分析失敗次數（費用上限不計）"
    )
    analysis_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class IngestionDeadLetter(Base):
    """重試用盡或不可重試的擷取失敗，等待人工處理（P1-T07）。

    `error_message` 只記錄 Adapter 錯誤訊息（來源代碼與狀態碼），其他例外只記類型，避免原文外洩。
    """

    __tablename__ = "ingestion_dead_letters"
    __table_args__ = (Index(None, "source_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="RESTRICT"))
    error_type: Mapped[str] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer)
    cursor: Mapped[str | None] = mapped_column(Text, comment="失敗當時的 cursor")
    created_at: Mapped[datetime] = created_at_col()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
