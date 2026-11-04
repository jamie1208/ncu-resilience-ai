"""小報告原因的人工決策（P7-T07，ADR-0017）：report_cause_decisions。

依 ADR-0009 的編號原則使用下一個可用編號 0018。管理者可以把最新一份有效小報告中的推測原因
（hypothesized）標為人工確認，也可以回滾；每次決策新增一筆（確認依據、決定者、時間），
同一份報告同一個原因最新的一筆是目前狀態。報告刪除時一併刪除。

Revision ID: 0018_report_cause_decisions
Revises: 0017_scenario_runs
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_report_cause_decisions"
down_revision: str | None = "0017_scenario_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/report.py 同步
DECISIONS = "'confirmed', 'hypothesized'"


def upgrade() -> None:
    op.create_table(
        "report_cause_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cause_id", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("basis", sa.String(200), nullable=True, comment="確認依據；不含個資"),
        sa.Column("decided_by", sa.String(100), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_cause_decisions"),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["event_reports.id"],
            name="fk_report_cause_decisions_report_id_event_reports",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"decision IN ({DECISIONS})", name="ck_report_cause_decisions_decision"),
        sa.CheckConstraint(
            "(decision = 'confirmed') = (basis IS NOT NULL)", name="ck_report_cause_decisions_basis"
        ),
    )
    op.create_index(
        "ix_report_cause_decisions_report_cause",
        "report_cause_decisions",
        ["report_id", "cause_id", "decided_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_cause_decisions_report_cause", table_name="report_cause_decisions")
    op.drop_table("report_cause_decisions")
