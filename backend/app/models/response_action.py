"""改善措施追蹤（P4-T05，0012）：response_actions 與 response_action_transitions。

追蹤項目可從事件小報告中 S08 的校方措施建立（保存措施快照與報告、索引），
也可手動新增。
狀態轉移規則在 `app/resilience/actions.py`；每次狀態變更（含建立）新增一筆 transition，
不修改、不刪除。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk

ACTION_STATES = ("discovered", "handling", "recovered", "improved", "dropped")
ACTION_ORIGINS = ("measure", "manual")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class ResponseAction(Base):
    """一項改善措施的追蹤：負責人、期限、狀態與最新佐證。"""

    __tablename__ = "response_actions"
    __table_args__ = (
        CheckConstraint(f"state IN ({_in(ACTION_STATES)})", name="state"),
        CheckConstraint(f"origin IN ({_in(ACTION_ORIGINS)})", name="origin"),
        CheckConstraint(
            "(origin = 'measure') = (report_id IS NOT NULL AND measure_index IS NOT NULL)",
            name="measure_source",
        ),
        UniqueConstraint("report_id", "measure_index"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str] = mapped_column(String(16))
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("event_reports.id", ondelete="SET NULL")
    )
    measure_index: Mapped[int | None] = mapped_column(Integer)
    measure: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="建立時的 S08 措施快照（含 evidence_ids、cause_basis）"
    )
    title: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(String(100))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), index=True)
    proof: Mapped[str | None] = mapped_column(Text, comment="最近一次恢復或改進的佐證")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class ResponseActionTransition(Base):
    """一次狀態變更；建立時記一筆 from_state 為 null 的「發現」。"""

    __tablename__ = "response_action_transitions"
    __table_args__ = (
        CheckConstraint(
            f"from_state IS NULL OR from_state IN ({_in(ACTION_STATES)})", name="from_state"
        ),
        CheckConstraint(f"to_state IN ({_in(ACTION_STATES)})", name="to_state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("response_actions.id", ondelete="CASCADE"), index=True
    )
    from_state: Mapped[str | None] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(200))
    proof: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()
