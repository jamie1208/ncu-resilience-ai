"""source_items 的分析狀態（P4-T07）：擷取後由定期掃描排入分析管線。

依 ADR-0009 的編號原則使用下一個可用編號 0009。既有文章一律從 pending 開始，
超過分析時間窗的由掃描任務標為 skipped（不在 migration 依當下時間改資料）。

Revision ID: 0009_source_item_analysis
Revises: 0008_alerts
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_source_item_analysis"
down_revision: str | None = "0008_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_items",
        sa.Column(
            "analysis_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="分析管線狀態（P4-T07）：pending、queued、done、skipped、failed",
        ),
    )
    op.add_column(
        "source_items",
        sa.Column(
            "analysis_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="分析失敗次數（費用上限不計）",
        ),
    )
    op.add_column(
        "source_items",
        sa.Column("analysis_queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_source_items_analysis_status"),
        "source_items",
        "analysis_status IN ('pending', 'queued', 'done', 'skipped', 'failed')",
    )
    op.create_index(
        "ix_source_items_analysis_pending",
        "source_items",
        ["analysis_status"],
        postgresql_where=sa.text("analysis_status IN ('pending', 'queued')"),
    )


def downgrade() -> None:
    op.drop_index("ix_source_items_analysis_pending", table_name="source_items")
    op.drop_constraint(op.f("ck_source_items_analysis_status"), "source_items", type_="check")
    op.drop_column("source_items", "analysis_queued_at")
    op.drop_column("source_items", "analysis_attempts")
    op.drop_column("source_items", "analysis_status")
