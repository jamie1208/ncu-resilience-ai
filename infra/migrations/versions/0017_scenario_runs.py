"""What-if 模擬紀錄（P7-T02）：scenario_runs。

依 ADR-0009 的編號原則使用下一個可用編號 0017。每次模擬新增一筆，保存輸入（含隨機種子）、
引擎結果與引擎版本，方便以相同參數重跑驗證可重現（G7-1）；S10 的解釋存在 ai_analysis_runs，
以 ai_run_id 連結。事件刪除時保留模擬（event_id 設為 NULL）。

Revision ID: 0017_scenario_runs
Revises: 0016_analytics_views
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_scenario_runs"
down_revision: str | None = "0016_analytics_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("ai_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scenario_runs"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_scenario_runs_event_id_events",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_scenario_runs_ai_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("seed >= 0", name="ck_scenario_runs_seed"),
    )
    op.create_index("ix_scenario_runs_event_id", "scenario_runs", ["event_id"])
    op.create_index("ix_scenario_runs_created_at", "scenario_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_scenario_runs_created_at", table_name="scenario_runs")
    op.drop_index("ix_scenario_runs_event_id", table_name="scenario_runs")
    op.drop_table("scenario_runs")
