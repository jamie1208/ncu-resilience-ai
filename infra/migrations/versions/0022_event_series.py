"""系列事件（P8-T04，ADR-0018 決策 1-1～1-4、ADR-0020 決策 1）。

新增 event_series、event_series_members、event_series_removals 表。

依 ADR-0009 的編號原則使用下一個可用編號 0022。每個事件屬於一個系列（成員表以 event_id 為主鍵），
S11 判斷同主題就加入既有系列，否則建立只有它一件的系列；拆出、解散、改判 false 與合併都新增一筆
移出紀錄。事件刪除時成員與移出紀錄一併刪除；系列刪除時成員一併刪除、移出紀錄的 series_id 設為 NULL。

Revision ID: 0022_event_series
Revises: 0021_analytics_views_canonical
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_event_series"
down_revision: str | None = "0021_analytics_views_canonical"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/series.py 同步
METHODS = "'ai', 'fallback', 'manual', 'rule'"
REASONS = "'split', 'dissolve', 'false', 'merged'"


def _uuid(name: str, *, nullable: bool = False, **kw: Any) -> sa.Column[Any]:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable, **kw)


def upgrade() -> None:
    op.create_table(
        "event_series",
        _uuid("id", server_default=sa.text("gen_random_uuid()")),
        sa.Column("category", sa.String(32), nullable=False, comment="S03 類別"),
        sa.Column(
            "title", sa.String(300), nullable=False, comment="建立系列時第一起事件的標題"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_series"),
    )
    op.create_index("ix_event_series_category", "event_series", ["category"])

    op.create_table(
        "event_series_members",
        _uuid("event_id"),
        _uuid("series_id"),
        sa.Column("method", sa.String(16), nullable=False),
        _uuid("ai_run_id", nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_event_series_members"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_series_members_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["event_series.id"],
            name="fk_event_series_members_series_id_event_series",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_event_series_members_ai_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"method IN ({METHODS})", name="ck_event_series_members_method"),
    )
    op.create_index("ix_event_series_members_series_id", "event_series_members", ["series_id"])

    op.create_table(
        "event_series_removals",
        _uuid("id", server_default=sa.text("gen_random_uuid()")),
        _uuid("event_id"),
        _uuid("series_id", nullable=True, comment="離開的系列；系列刪除後為 null"),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_event_series_removals"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_series_removals_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["event_series.id"],
            name="fk_event_series_removals_series_id_event_series",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"reason IN ({REASONS})", name="ck_event_series_removals_reason"),
    )
    op.create_index("ix_event_series_removals_event_id", "event_series_removals", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_event_series_removals_event_id", table_name="event_series_removals")
    op.drop_table("event_series_removals")
    op.drop_index("ix_event_series_members_series_id", table_name="event_series_members")
    op.drop_table("event_series_members")
    op.drop_index("ix_event_series_category", table_name="event_series")
    op.drop_table("event_series")
