"""事件、證據與地點：events、event_evidence、locations（規格 §9）。

狀態欄位的合法值由 app/domain/states.py 定義；events.status 以 CHECK 約束強制（0002）。
events.embedding、occurrence_start_date 供 S04 候選召回使用（P2-T09，0006）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Date, Double, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.states import EventStatus
from app.models.base import Base, created_at_col, updated_at_col, uuid_pk

EVENT_EMBEDDING_DIM = 1536
"""events.embedding 的維度；必須等於 config/llm.yaml 的 embedding.dimensions。

改維度要寫 migration。
"""

_EVENT_STATUS_VALUES = ", ".join(f"'{s.value}'" for s in EventStatus)


class Location(Base):
    """校園地點；位置不明時 lat／lng 可為空。"""

    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    lat: Mapped[float | None] = mapped_column(Double)
    lng: Mapped[float | None] = mapped_column(Double)
    precision: Mapped[str | None] = mapped_column(String(32), comment="例：building、area、unknown")


class Event(Base):
    """由一或多篇來源文章彙整出的校園事件。"""

    __tablename__ = "events"
    # 合法值與轉移由 app/domain/states.py 定義；新增狀態需同步 migration
    __table_args__ = (CheckConstraint(f"status IN ({_EVENT_STATUS_VALUES})", name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(32), index=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    occurrence_start_date: Mapped[date | None] = mapped_column(
        Date, comment="事件開始日期（台北時間，S02 抽取）"
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EVENT_EMBEDDING_DIM), comment="事件標題與證據的向量（S04 候選召回）"
    )
    created_at: Mapped[datetime] = created_at_col(index=True)
    updated_at: Mapped[datetime] = updated_at_col()


class EventEvidence(Base):
    """事件與來源文章的多對多關聯（複合主鍵）。刪除事件時一併刪除；來源文章不可被刪。"""

    __tablename__ = "event_evidence"

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_items.id", ondelete="RESTRICT"), primary_key=True
    )
    support_type: Mapped[str] = mapped_column(
        String(32), comment="例：supports、contradicts、mentions"
    )
    excerpt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
