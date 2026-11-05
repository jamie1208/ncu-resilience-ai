"""手動合併事件的紀錄（P8-T01，v1.0 F03）：event_merges。

依 ADR-0009 的編號原則使用下一個可用編號 0019。被併入的事件改為 archived（沿用既有狀態，
候選召回、公開同步與通知都已排除 archived），並由這裡導向目標事件；每個事件最多被併入一次。
任一事件刪除時一併刪除紀錄。拆分不另建資料表，以稽核 `event.split` 記錄。

Revision ID: 0019_event_merges
Revises: 0018_report_cause_decisions
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_event_merges"
down_revision: str | None = "0018_report_cause_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_merges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("merged_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_merges"),
        sa.ForeignKeyConstraint(
            ["merged_event_id"],
            ["events.id"],
            name="fk_event_merges_merged_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_event_id"],
            ["events.id"],
            name="fk_event_merges_target_event_id_events",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("merged_event_id", name="uq_event_merges_merged_event_id"),
        sa.CheckConstraint(
            "merged_event_id <> target_event_id", name="ck_event_merges_distinct_events"
        ),
    )
    op.create_index("ix_event_merges_target_event_id", "event_merges", ["target_event_id"])


def downgrade() -> None:
    op.drop_index("ix_event_merges_target_event_id", table_name="event_merges")
    op.drop_table("event_merges")
