"""依改善建議評估效益（P8-T15，ADR-0019）：impact_evaluations。

依 ADR-0009 的編號原則使用下一個可用編號 0027。另建一張表、不擴充 `scenario_runs`（AI 自行決定）：
`scenario_runs` 是 P7-T02 引擎的紀錄（engine_version、seed 必填，G7-1 依它重跑），S14 的評估沒有
引擎與種子，混在一起會改到 G7-1 的資料與測試。

每次評估一列：系列、所依據的系列分析、選擇的建議代號（`selection_key` 為排序後的代號，用來沿用
同一次分析、同一組選擇的結果）、狀態、改善百分比區間（`0 ≤ low ≤ high ≤ cap`）、評估當時的上限、
已驗證的結果、程式算出的件數基準、S14 執行紀錄與建立者。系列或分析刪除時一併刪除。

downgrade 刪除整張表（評估結果只在後台參考，不影響其他資料）。

Revision ID: 0027_impact_evaluations
Revises: 0026_report_receipts
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_impact_evaluations"
down_revision: str | None = "0026_report_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "'ok', 'abstained', 'invalid', 'failed'"
INDEX = "ix_impact_evaluations_series_id_analysis_id_selection_key"


def upgrade() -> None:
    op.create_table(
        "impact_evaluations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_key", sa.String(64), nullable=False),
        sa.Column("selected", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("abstain_reason", sa.String(64), nullable=True),
        sa.Column("improvement_low", sa.Integer(), nullable=True),
        sa.Column("improvement_high", sa.Integer(), nullable=True),
        sa.Column("cap", sa.Integer(), nullable=False, comment="評估當時的 improvement_cap"),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=True,
            comment="已驗證的情境、涵蓋原因、重疊、風險與補強建議",
        ),
        sa.Column(
            "counts", postgresql.JSONB(), nullable=False, comment="系列基準件數與依據（程式計算）"
        ),
        sa.Column("ai_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_impact_evaluations"),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["event_series.id"],
            name="fk_impact_evaluations_series_id_event_series",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["series_analyses.id"],
            name="fk_impact_evaluations_analysis_id_series_analyses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_impact_evaluations_ai_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_impact_evaluations_status"),
        sa.CheckConstraint(
            "(improvement_low IS NULL) = (improvement_high IS NULL)",
            name="ck_impact_evaluations_improvement_pair",
        ),
        sa.CheckConstraint(
            "improvement_low IS NULL OR "
            "(improvement_low >= 0 AND improvement_low <= improvement_high "
            "AND improvement_high <= cap)",
            name="ck_impact_evaluations_improvement_range",
        ),
        sa.CheckConstraint("cap >= 0 AND cap <= 100", name="ck_impact_evaluations_cap"),
    )
    op.create_index(INDEX, "impact_evaluations", ["series_id", "analysis_id", "selection_key"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name="impact_evaluations")
    op.drop_table("impact_evaluations")
