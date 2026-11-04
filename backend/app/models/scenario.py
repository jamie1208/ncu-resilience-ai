"""What-if 模擬紀錄（P7-T02，規格 §9 scenario_runs）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class ScenarioRun(Base):
    """一次模擬：輸入（含種子）、引擎結果與版本；S10 的解釋在 `ai_run_id` 指向的執行紀錄。

    以 input 中的參數與 seed、相同 engine_version 重跑，數值結果相同（G7-1）。
    """

    __tablename__ = "scenario_runs"
    __table_args__ = (CheckConstraint("seed >= 0", name="seed"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), index=True
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    engine_version: Mapped[str] = mapped_column(String(32))
    seed: Mapped[int] = mapped_column(Integer)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_at_col(index=True)
