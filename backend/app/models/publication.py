"""公開狀態（P5-T10，0013）：publications、publication_versions、publication_takedowns。

每個事件最多一筆 publication，記錄目前的公開程度（`app/publishing/policy.py` 的 PublicLevel）
與狀態。公開內容每次改變新增一筆版本快照（已遮蔽 PII，不修改、不刪除）；使用者每次下架
（P5-T14）新增一筆下架紀錄。公開 API（P5-T11）只讀取 state 為 published 的目前版本。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk

PUBLICATION_LEVELS = ("content", "safety_notice", "none")
PUBLICATION_STATES = ("published", "withdrawn", "taken_down")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _json_list(*, comment: str | None = None) -> Any:
    return mapped_column(JSONB, server_default=text("'[]'::jsonb"), comment=comment)


class Publication(Base):
    """一個事件目前的公開狀態。"""

    __tablename__ = "publications"
    __table_args__ = (
        CheckConstraint(f"level IN ({_in(PUBLICATION_LEVELS)})", name="level"),
        CheckConstraint(f"state IN ({_in(PUBLICATION_STATES)})", name="state"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("state <> 'published' OR level <> 'none'", name="published_level"),
        CheckConstraint("(state = 'taken_down') = (taken_down_at IS NOT NULL)", name="taken_down"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), unique=True
    )
    level: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(Integer, comment="目前版本；對應 publication_versions")
    policy_version: Mapped[str] = mapped_column(String(32))
    reasons: Mapped[list[str]] = _json_list(comment="最近一次判斷觸發的規則代碼")
    needs_review: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), comment="疑似指控，等使用者在後台判斷"
    )
    taken_down_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class PublicationVersion(Base):
    """一個版本的公開內容快照。"""

    __tablename__ = "publication_versions"
    __table_args__ = (
        CheckConstraint(f"level IN ({_in(PUBLICATION_LEVELS)})", name="level"),
        CheckConstraint("(level = 'none') = (content IS NULL)", name="content"),
        UniqueConstraint("publication_id", "version"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="公開規則產生、已遮蔽 PII 的內容；level 為 none 時為 null"
    )
    content_hash: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(32))
    reasons: Mapped[list[str]] = _json_list()
    created_at: Mapped[datetime] = created_at_col()


class PublicationTakedown(Base):
    """一次下架（P5-T14）。"""

    __tablename__ = "publication_takedowns"

    id: Mapped[uuid.UUID] = uuid_pk()
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, comment="下架時公開中的版本")
    reason: Mapped[str | None] = mapped_column(String(200))
    actor: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()
