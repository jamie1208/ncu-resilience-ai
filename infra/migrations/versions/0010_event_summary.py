"""事件說明（P4-T08）：events 加上 S02 產生的摘要與它的出處。

依 ADR-0009 的編號原則使用下一個可用編號 0010。摘要來自同事件中證據最強、其次最新的一篇文章，
所以一併保存那篇文章、依據的 evidence_ids 與產生摘要的 S02 執行紀錄，方便回溯。
既有事件的摘要為空，之後有文章併入時補上（不在 migration 呼叫模型）。

Revision ID: 0010_event_summary
Revises: 0009_source_item_analysis
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_event_summary"
down_revision: str | None = "0009_source_item_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("summary", sa.Text(), nullable=True, comment="事件說明（S02，已遮蔽個資）"),
    )
    op.add_column(
        "events",
        sa.Column(
            "summary_evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="事件說明依據的 evidence_id（文章 id 的十六進位）",
        ),
    )
    op.add_column(
        "events",
        sa.Column("summary_source_item_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("summary_ai_run_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_events_summary_source_item_id_source_items"),
        "events",
        "source_items",
        ["summary_source_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_events_summary_ai_run_id_ai_analysis_runs"),
        "events",
        "ai_analysis_runs",
        ["summary_ai_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_events_summary_ai_run_id_ai_analysis_runs"), "events", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_events_summary_source_item_id_source_items"), "events", type_="foreignkey"
    )
    op.drop_column("events", "summary_updated_at")
    op.drop_column("events", "summary_ai_run_id")
    op.drop_column("events", "summary_source_item_id")
    op.drop_column("events", "summary_evidence_ids")
    op.drop_column("events", "summary")
