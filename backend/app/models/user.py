"""使用者與登入 session（P3-T01；ADR-0008：單一使用者）。

`users` 以唯一的常數運算式索引限制最多一列；`user_sessions` 只存 token 的 SHA-256 雜湊。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class User(Base):
    """系統唯一的使用者（使用者本人，角色固定為 admin）。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role = 'admin'", name="role_admin_only"),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        # 個人版只允許一位使用者：對常數建唯一索引，第二列一定衝突
        Index("uq_users_single_user", text("(true)"), unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), server_default=text("'admin'"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class UserSession(Base):
    """一個登入 session；登出或改密碼時設定 `revoked_at`，過期後不再有效。"""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
