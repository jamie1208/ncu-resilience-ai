"""依改善建議評估效益（P8-T15，ADR-0019）：impact_evaluations。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk

IMPACT_STATUSES = ("ok", "abstained", "invalid", "failed")


class ImpactEvaluation(Base):
    """一次 S14 評估：系列的哪一次分析、選了哪些建議、改善百分比區間與情境、程式換算的件數。

    `selected` 是建議代號（m1…，對應分析 `measures` 的順序），`selection_key` 是排序後以逗號
    連接的代號，用來找同一次分析、同一組選擇的既有結果。`status`：ok（已評估）、abstained
    （證據不足）、invalid（輸出重試後仍未通過驗證）、failed（呼叫失敗）。
    """

    __tablename__ = "impact_evaluations"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in IMPACT_STATUSES)), name="status"
        ),
        CheckConstraint(
            "(improvement_low IS NULL) = (improvement_high IS NULL)", name="improvement_pair"
        ),
        CheckConstraint(
            "improvement_low IS NULL OR "
            "(improvement_low >= 0 AND improvement_low <= improvement_high "
            "AND improvement_high <= cap)",
            name="improvement_range",
        ),
        CheckConstraint("cap >= 0 AND cap <= 100", name="cap"),
        Index(
            "ix_impact_evaluations_series_id_analysis_id_selection_key",
            "series_id",
            "analysis_id",
            "selection_key",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_series.id", ondelete="CASCADE"))
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("series_analyses.id", ondelete="CASCADE")
    )
    selection_key: Mapped[str] = mapped_column(String(64))
    selected: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    abstain_reason: Mapped[str | None] = mapped_column(String(64))
    improvement_low: Mapped[int | None] = mapped_column(Integer)
    improvement_high: Mapped[int | None] = mapped_column(Integer)
    cap: Mapped[int] = mapped_column(Integer, comment="評估當時的 improvement_cap")
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="已驗證的情境、涵蓋原因、重疊、風險與補強建議"
    )
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="系列基準件數與依據（程式計算）")
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_at_col()
