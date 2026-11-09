"""系列事件（P8-T04，ADR-0018 決策 1-1～1-4、ADR-0020 決策 1）：event_series、成員與移出紀錄；
系列分析與原因確認（P8-T05，0023）：series_analyses、series_cause_decisions。

每個事件屬於一個系列（`event_series_members` 以 event_id 為主鍵）；被手動合併的事件不屬於任何系列，
所以系列件數＝成員數。拆出、解散、改判 false 與合併都新增一筆 `event_series_removals`。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk

JOIN_METHODS = ("ai", "fallback", "manual", "rule")
"""ai：S11 判斷；fallback：沒有呼叫或 abstain、失敗；manual：使用者拆出、解散；
rule：改判 false 移出。
"""

REMOVAL_REASONS = ("split", "dissolve", "false", "merged")

TITLE_SOURCES = ("first_event", "ai", "manual")
"""first_event：建立系列時第一起事件的標題；ai：S12 的共通名稱；manual：人工改名（S12 不覆蓋）。"""

ANALYSIS_STATUSES = ("ok", "abstained", "failed")
CAUSE_DECISIONS = ("confirmed", "hypothesized")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class EventSeries(Base):
    """一個系列：同類別、同主題的事件（不限地點）。"""

    __tablename__ = "event_series"
    __table_args__ = (
        CheckConstraint(f"title_source IN ({_values(TITLE_SOURCES)})", name="title_source"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    category: Mapped[str] = mapped_column(String(32), index=True, comment="S03 類別")
    title: Mapped[str] = mapped_column(String(300), comment="建立系列時第一起事件的標題")
    title_source: Mapped[str] = mapped_column(
        String(16),
        server_default="first_event",
        comment="first_event：第一起事件的標題；ai：S12 的共通名稱；manual：人工改名",
    )
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


def _jsonb_list() -> Mapped[Any]:
    return mapped_column(JSONB, server_default=text("'[]'::jsonb"))


class SeriesAnalysis(Base):
    """一次 S12 系列分析的結果；各區塊已經過引用驗證，件數由程式依引用的事件計算。

    `events` 是本次輸入的代號與事件 id（E1 → uuid），各區塊的 `event_ids` 已換成事件 id。
    """

    __tablename__ = "series_analyses"
    __table_args__ = (
        CheckConstraint(f"status IN ({_values(ANALYSIS_STATUSES)})", name="status"),
        CheckConstraint(f"pattern_status IN ({_values(ANALYSIS_STATUSES)})", name="pattern_status"),
        Index("ix_series_analyses_series_created", "series_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_series.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    abstain_reason: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(60), comment="S12 建議的共通名稱（已驗證）")
    summary: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list[dict[str, Any]]] = _jsonb_list()
    bursts: Mapped[list[dict[str, Any]]] = _jsonb_list()
    location_groups: Mapped[list[dict[str, Any]]] = _jsonb_list()
    pattern_status: Mapped[str] = mapped_column(String(16))
    pattern_abstain_reason: Mapped[str | None] = mapped_column(String(64))
    patterns: Mapped[list[dict[str, Any]]] = _jsonb_list()
    causes: Mapped[list[dict[str, Any]]] = _jsonb_list()
    measures: Mapped[list[dict[str, Any]]] = _jsonb_list()
    dropped: Mapped[list[dict[str, Any]]] = _jsonb_list()
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL")
    )
    trace_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()


class SeriesCauseDecision(Base):
    """系列分析中一個推測原因的人工決策（ADR-0017 的規則，ADR-0020 決策 3-4）；只新增不修改。"""

    __tablename__ = "series_cause_decisions"
    __table_args__ = (
        CheckConstraint(f"decision IN ({_values(CAUSE_DECISIONS)})", name="decision"),
        CheckConstraint("(decision = 'confirmed') = (basis IS NOT NULL)", name="basis"),
        Index("ix_series_cause_decisions_analysis_cause", "analysis_id", "cause_id", "decided_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("series_analyses.id", ondelete="CASCADE")
    )
    cause_id: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16))
    basis: Mapped[str | None] = mapped_column(String(200), comment="確認依據；不含個資")
    decided_by: Mapped[str] = mapped_column(String(100))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
