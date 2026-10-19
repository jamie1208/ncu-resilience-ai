"""ai_analysis_runs、risk_assessments、event_relations（P2-T04）。

ai_analysis_runs.event_id 可空：S01、S02 在事件產生前對文章執行（使用者 2026-10-19 決定）。
不存提示詞、來源原文，也不存驗證失敗的原始輸出。

Revision ID: 0005_ai_runs
Revises: 0004_source_item_tags
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_ai_runs"
down_revision: str | None = "0004_source_item_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id() -> sa.Column[object]:
    return sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "ai_analysis_runs",
        _id(),
        sa.Column("event_id", UUID, nullable=True),
        sa.Column("skill_name", sa.String(64), nullable=False),
        sa.Column("skill_version", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=True,
            comment="通過驗證的輸出；無效輸出不保存",
        ),
        sa.Column("abstain_reason", sa.String(64), nullable=True),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column(
            "validation_issues",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="最後一次無效輸出的問題代碼（不含內容）",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "evidence_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("trace_id", sa.String(64), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_ai_analysis_runs"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_ai_analysis_runs_event_id_events",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'abstained', 'failed')", name="ck_ai_analysis_runs_status"
        ),
    )
    op.create_index("ix_ai_analysis_runs_event_id", "ai_analysis_runs", ["event_id"])
    op.create_index("ix_ai_analysis_runs_skill_name", "ai_analysis_runs", ["skill_name"])
    op.create_index("ix_ai_analysis_runs_trace_id", "ai_analysis_runs", ["trace_id"])
    op.create_index("ix_ai_analysis_runs_created_at", "ai_analysis_runs", ["created_at"])

    op.create_table(
        "risk_assessments",
        _id(),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("dimensions", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("ai_run_id", UUID, nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_risk_assessments"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_risk_assessments_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_risk_assessments_ai_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_risk_assessments_event_id", "risk_assessments", ["event_id"])
    op.create_index("ix_risk_assessments_created_at", "risk_assessments", ["created_at"])

    op.create_table(
        "event_relations",
        _id(),
        sa.Column("from_event_id", UUID, nullable=False),
        sa.Column("to_event_id", UUID, nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="支持關聯的 evidence_ids",
        ),
        sa.Column("ai_run_id", UUID, nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_event_relations"),
        sa.ForeignKeyConstraint(
            ["from_event_id"],
            ["events.id"],
            name="fk_event_relations_from_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_event_id"],
            ["events.id"],
            name="fk_event_relations_to_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_event_relations_ai_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("from_event_id <> to_event_id", name="ck_event_relations_no_self_loop"),
        sa.CheckConstraint(
            "type IN ('same_event', 'related_event')", name="ck_event_relations_type"
        ),
        sa.UniqueConstraint(
            "from_event_id",
            "to_event_id",
            "type",
            name="uq_event_relations_from_event_id_to_event_id_type",
        ),
    )
    op.create_index("ix_event_relations_from_event_id", "event_relations", ["from_event_id"])
    op.create_index("ix_event_relations_to_event_id", "event_relations", ["to_event_id"])


def downgrade() -> None:
    op.drop_table("event_relations")
    op.drop_table("risk_assessments")
    op.drop_table("ai_analysis_runs")
