"""AI 分析紀錄、風險評估與事件關聯（規格 §9，P2-T04）。

資料表：ai_analysis_runs、risk_assessments、event_relations。

ai_analysis_runs 不存提示詞、來源原文，也不存驗證失敗的原始輸出（AGENTS.md：日誌不得包含原文）；
`result` 只存通過驗證的輸出。
event_id 可空：S01、S02 在事件產生前對文章執行（使用者 2026-10-19 決定）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk

RUN_STATUSES = ("succeeded", "abstained", "failed")
RELATION_TYPES = ("same_event", "related_event", "hypothesized_cause")
RELATION_METHODS = ("ai", "rule", "manual")
RELATION_DECISIONS = ("pending", "confirmed", "rejected")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class AIAnalysisRun(Base):
    """一次 Skill 執行（含重試）的紀錄；每日費用上限依 created_at（台北日）加總 cost_usd。"""

    __tablename__ = "ai_analysis_runs"
    __table_args__ = (CheckConstraint(f"status IN ({_in(RUN_STATUSES)})", name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), index=True
    )
    skill_name: Mapped[str] = mapped_column(String(64), index=True)
    skill_version: Mapped[str] = mapped_column(String(32))
    schema_version: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="通過驗證的輸出；無效輸出不保存"
    )
    abstain_reason: Mapped[str | None] = mapped_column(String(64))
    error_type: Mapped[str | None] = mapped_column(String(100))
    validation_issues: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("'{}'"),
        comment="最後一次無效輸出的問題代碼（不含內容）",
    )
    attempts: Mapped[int] = mapped_column(Integer)
    evidence_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cached_input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = created_at_col(index=True)


class RiskAssessment(Base):
    """事件的風險評估；每次評分新增一筆，保留歷史（P2-T11 寫入）。"""

    __tablename__ = "risk_assessments"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    level: Mapped[str] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(32))
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col(index=True)


class EventRelation(Base):
    """兩個事件的關聯（P2-T09 寫入 related_event，P6-T01 加入人工決策）；禁止關聯到自己。

    hypothesized_cause 表示 from 可能是 to 的原因（推測，尚未查證）。決策只記目前狀態，
    歷史在稽核紀錄；規則見 app/relations/service.py。
    """

    __tablename__ = "event_relations"
    __table_args__ = (
        CheckConstraint("from_event_id <> to_event_id", name="no_self_loop"),
        CheckConstraint(f"type IN ({_in(RELATION_TYPES)})", name="type"),
        CheckConstraint(f"method IN ({_in(RELATION_METHODS)})", name="method"),
        CheckConstraint(f"decision IN ({_in(RELATION_DECISIONS)})", name="decision"),
        CheckConstraint(
            "(decision = 'pending') = (decided_at IS NULL AND decided_by IS NULL)", name="decided"
        ),
        UniqueConstraint("from_event_id", "to_event_id", "type"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    from_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    to_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32))
    score: Mapped[float | None] = mapped_column(Double)
    evidence: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), comment="支持關聯的 evidence_ids"
    )
    method: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'ai'"),
        comment="關聯由誰提出：ai（Skill）、rule（確定性規則）、manual（人工）",
    )
    decision: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decision_note: Mapped[str | None] = mapped_column(String(200), comment="已遮蔽個資")
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
