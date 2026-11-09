"""系列事件（P8-T04，ADR-0018 決策 1-1～1-4、ADR-0020 決策 1）：event_series、成員與移出紀錄。

每個事件屬於一個系列（`event_series_members` 以 event_id 為主鍵）；被手動合併的事件不屬於任何系列，
所以系列件數＝成員數。拆出、解散、改判 false 與合併都新增一筆 `event_series_removals`。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk

JOIN_METHODS = ("ai", "fallback", "manual", "rule")
"""ai：S11 判斷；fallback：沒有呼叫或 abstain、失敗；manual：使用者拆出、解散；
rule：改判 false 移出。
"""

REMOVAL_REASONS = ("split", "dissolve", "false", "merged")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class EventSeries(Base):
    """一個系列：同類別、同主題的事件（不限地點）。"""

    __tablename__ = "event_series"

    id: Mapped[uuid.UUID] = uuid_pk()
    category: Mapped[str] = mapped_column(String(32), index=True, comment="S03 類別")
    title: Mapped[str] = mapped_column(String(300), comment="建立系列時第一起事件的標題")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class EventSeriesMember(Base):
    """事件所屬的系列（每個事件最多一筆）。"""

    __tablename__ = "event_series_members"
    __table_args__ = (CheckConstraint(f"method IN ({_values(JOIN_METHODS)})", name="method"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event_series.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(16))
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL")
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventSeriesRemoval(Base):
    """事件離開系列的紀錄：使用者拆出、解散、改判 false 或被合併。"""

    __tablename__ = "event_series_removals"
    __table_args__ = (CheckConstraint(f"reason IN ({_values(REMOVAL_REASONS)})", name="reason"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("event_series.id", ondelete="SET NULL"), comment="離開的系列；系列刪除後為 null"
    )
    reason: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
