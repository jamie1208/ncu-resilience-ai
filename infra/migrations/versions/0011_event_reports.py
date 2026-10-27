"""事件小報告（P4-T09）：event_reports。

依 ADR-0009 的編號原則使用下一個可用編號 0011。每次產生報告新增一筆（保留歷史），保存整理後的
可能影響（S06 分項）、原因（S07）、措施（S08），以及使用的風險評估與 AI 執行紀錄，方便回溯。
事件被查證為不實時以 invalidated_at／invalidated_reason 標為失效，不刪除。

Revision ID: 0011_event_reports
Revises: 0010_event_summary
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_event_reports"
down_revision: str | None = "0010_event_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/report.py 同步；已上線的值不可改名
TRIGGERS = ("auto", "manual")
SECTION_STATUSES = ("ok", "abstained", "failed")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _json_list(name: str) -> sa.Column[object]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'[]'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "event_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            comment="產生時交給 S07／S08 的查證狀態",
        ),
        sa.Column("risk_level", sa.String(16), nullable=True),
        _json_list("impacts"),
        sa.Column("causes_status", sa.String(16), nullable=False),
        sa.Column("causes_abstain_reason", sa.String(64), nullable=True),
        _json_list("causes"),
        _json_list("missing_information"),
        sa.Column("measures_status", sa.String(16), nullable=False),
        sa.Column("measures_abstain_reason", sa.String(64), nullable=True),
        _json_list("measures"),
        sa.Column("risk_assessment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("root_cause_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("solution_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_reports"),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_event_reports_event_id_events", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["risk_assessment_id"],
            ["risk_assessments.id"],
            name="fk_event_reports_risk_assessment_id_risk_assessments",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["root_cause_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_event_reports_root_cause_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["solution_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_event_reports_solution_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"trigger IN ({_in(TRIGGERS)})", name="ck_event_reports_trigger"),
        sa.CheckConstraint(
            f"causes_status IN ({_in(SECTION_STATUSES)})", name="ck_event_reports_causes_status"
        ),
        sa.CheckConstraint(
            f"measures_status IN ({_in(SECTION_STATUSES)})",
            name="ck_event_reports_measures_status",
        ),
    )
    op.create_index("ix_event_reports_event_id", "event_reports", ["event_id"])
    op.create_index("ix_event_reports_trace_id", "event_reports", ["trace_id"])
    op.create_index("ix_event_reports_created_at", "event_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_event_reports_created_at", table_name="event_reports")
    op.drop_index("ix_event_reports_trace_id", table_name="event_reports")
    op.drop_index("ix_event_reports_event_id", table_name="event_reports")
    op.drop_table("event_reports")
