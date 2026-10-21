"""users、user_sessions（P3-T01；ADR-0008：單一使用者）。

users 只允許一列（對常數 `(true)` 建唯一索引），角色固定為 admin。
user_sessions 只存 session／CSRF token 的 SHA-256 雜湊，不存 token 本身。

Revision ID: 0007_users_roles
Revises: 0006_event_embeddings
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_users_roles"
down_revision: str | None = "0006_event_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), server_default=sa.text("'admin'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("role = 'admin'", name="ck_users_role_admin_only"),
        sa.CheckConstraint(
            "failed_login_count >= 0", name="ck_users_failed_login_count_non_negative"
        ),
    )
    op.create_index("uq_users_single_user", "users", [sa.text("(true)")], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("user_sessions")
    op.drop_table("users")
