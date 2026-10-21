"""個人通知：alerts、alert_deliveries（規格 §9；P3-T03 建表，P3-T07／T08 寫入）。

個人版的「發布」只表示通知使用者本人（ADR-0008），audience 目前只有 `owner`。
alerts.state 的合法值與轉移由 app/domain/states.py 的 AlertState 定義，以 CHECK 約束強制。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.states import AlertState
from app.models.base import Base, created_at_col, updated_at_col, uuid_pk

_ALERT_STATE_VALUES = ", ".join(f"'{s.value}'" for s in AlertState)


class Alert(Base):
    """一則通知；改版時 version 遞增，同一 idempotency_key 只會有一筆。"""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(f"state IN ({_ALERT_STATE_VALUES})", name="state"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    audience: Mapped[str] = mapped_column(String(32), comment="個人版只有 owner（使用者本人）")
    state: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, comment="已經 PII 遮蔽的通知內容")
    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, comment="hash(event_id, alert_version, audience)"
    )
    retract_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at_col(index=True)
    updated_at: Mapped[datetime] = updated_at_col()


class AlertDelivery(Base):
    """通知的一次遞送；同一版本對同一管道與目的地只遞送一次。"""

    __tablename__ = "alert_deliveries"
    __table_args__ = (UniqueConstraint("alert_id", "channel", "destination", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), comment="例：inbox（管理介面通知中心）、email")
    destination: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = created_at_col()
