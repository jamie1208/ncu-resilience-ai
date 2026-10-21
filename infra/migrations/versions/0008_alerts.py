"""alerts、alert_deliveries（規格 §9；P3-T03 建表，使用者 2026-10-21 決定）。

alerts.state 的合法值由 app/domain/states.py 的 AlertState 定義；idempotency_key 唯一，
alert_deliveries 對 (alert_id, channel, destination, version) 唯一，確保同版本不重複遞送。

Revision ID: 0008_alerts
Revises: 0007_users_roles
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_alerts"
down_revision: str | None = "0007_users_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 AlertState 同步；已上線的值不可改名
ALERT_STATES = (
    "candidate",
    "policy_checked",
    "eligible",
    "blocked",
    "published",
    "updated",
    "retracted",
    "expired",
)


def upgrade() -> None:
    states = ", ".join(f"'{s}'" for s in ALERT_STATES)
    op.create_table(
        "alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "audience", sa.String(32), nullable=False, comment="個人版只有 owner（使用者本人）"
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, comment="已經 PII 遮蔽的通知內容"),
        sa.Column(
            "idempotency_key",
            sa.String(64),
            nullable=False,
            comment="hash(event_id, alert_version, audience)",
        ),
        sa.Column("retract_reason", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_alerts_event_id_events", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_alerts_idempotency_key"),
        sa.CheckConstraint(f"state IN ({states})", name="ck_alerts_state"),
        sa.CheckConstraint("version >= 1", name="ck_alerts_version_positive"),
    )
    op.create_index("ix_alerts_event_id", "alerts", ["event_id"])
    op.create_index("ix_alerts_state", "alerts", ["state"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])

    op.create_table(
        "alert_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "channel",
            sa.String(32),
            nullable=False,
            comment="例：inbox（管理介面通知中心）、email",
        ),
        sa.Column("destination", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_alert_deliveries"),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name="fk_alert_deliveries_alert_id_alerts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "alert_id",
            "channel",
            "destination",
            "version",
            name="uq_alert_deliveries_alert_id_channel_destination_version",
        ),
    )
    op.create_index("ix_alert_deliveries_alert_id", "alert_deliveries", ["alert_id"])


def downgrade() -> None:
    op.drop_table("alert_deliveries")
    op.drop_table("alerts")
