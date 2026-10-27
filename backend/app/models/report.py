"""事件小報告（P4-T09，0011）：event_reports。

每次產生新增一筆（保留歷史），最新一筆是事件目前的報告。內容是 S06 分項、S07 原因與 S08 措施
經 `app/reports/service.py` 的規則整理後的結果：每一項都有 evidence_ids（文章 id 的十六進位），
文字已遮蔽個資。事件被查證為不實時，既有報告標為失效（invalidated_at），不刪除。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk

REPORT_TRIGGERS = ("auto", "manual")
SECTION_STATUSES = ("ok", "abstained", "failed")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _json_list() -> Any:
    return mapped_column(JSONB, server_default=text("'[]'::jsonb"))


class EventReport(Base):
    """一次產生的事件小報告與它使用的 AI 執行紀錄、風險評估。"""

    __tablename__ = "event_reports"
    __table_args__ = (
        CheckConstraint(f"trigger IN ({_in(REPORT_TRIGGERS)})", name="trigger"),
        CheckConstraint(f"causes_status IN ({_in(SECTION_STATUSES)})", name="causes_status"),
        CheckConstraint(f"measures_status IN ({_in(SECTION_STATUSES)})", name="measures_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(16))
    verification_status: Mapped[str] = mapped_column(
        String(16), comment="產生時交給 S07／S08 的查證狀態"
    )
    risk_level: Mapped[str | None] = mapped_column(String(16))
    impacts: Mapped[list[dict[str, Any]]] = _json_list()
    causes_status: Mapped[str] = mapped_column(String(16))
    causes_abstain_reason: Mapped[str | None] = mapped_column(String(64))
    causes: Mapped[list[dict[str, Any]]] = _json_list()
    missing_information: Mapped[list[str]] = _json_list()
    measures_status: Mapped[str] = mapped_column(String(16))
    measures_abstain_reason: Mapped[str | None] = mapped_column(String(64))
    measures: Mapped[list[dict[str, Any]]] = _json_list()
    risk_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("risk_assessments.id", ondelete="SET NULL")
    )
    root_cause_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL")
    )
    solution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL")
    )
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col(index=True)
