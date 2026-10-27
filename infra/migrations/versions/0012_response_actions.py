"""改善措施追蹤（P4-T05）：response_actions、response_action_transitions。

依 ADR-0009 的編號原則使用下一個可用編號 0012。追蹤項目可從事件小報告的 S08 校方措施建立
（report_id + measure_index 唯一，保存措施快照），也可手動新增。狀態：discovered（發現）→
handling（處理）→ recovered（恢復）→ improved（改進），另有 dropped（不追蹤）；轉移規則在
app/resilience/actions.py。每次狀態變更（含建立）新增一筆 response_action_transitions。

Revision ID: 0012_response_actions
Revises: 0011_event_reports
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_response_actions"
down_revision: str | None = "0011_event_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/response_action.py 同步；已上線的值不可改名
STATES = ("discovered", "handling", "recovered", "improved", "dropped")
ORIGINS = ("measure", "manual")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _uuid_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamp(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "response_actions",
        _uuid_pk(),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("measure_index", sa.Integer(), nullable=True),
        sa.Column(
            "measure",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="建立時的 S08 措施快照（含 evidence_ids、cause_basis）",
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("owner", sa.String(100), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("proof", sa.Text(), nullable=True, comment="最近一次恢復或改進的佐證"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_response_actions"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_response_actions_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["event_reports.id"],
            name="fk_response_actions_report_id_event_reports",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"state IN ({_in(STATES)})", name="ck_response_actions_state"),
        sa.CheckConstraint(f"origin IN ({_in(ORIGINS)})", name="ck_response_actions_origin"),
        sa.CheckConstraint(
            "(origin = 'measure') = (report_id IS NOT NULL AND measure_index IS NOT NULL)",
            name="ck_response_actions_measure_source",
        ),
        sa.UniqueConstraint(
            "report_id", "measure_index", name="uq_response_actions_report_id_measure_index"
        ),
    )
    op.create_index("ix_response_actions_event_id", "response_actions", ["event_id"])
    op.create_index("ix_response_actions_state", "response_actions", ["state"])

    op.create_table(
        "response_action_transitions",
        _uuid_pk(),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_state", sa.String(16), nullable=True),
        sa.Column("to_state", sa.String(16), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("proof", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_response_action_transitions"),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["response_actions.id"],
            name="fk_response_action_transitions_action_id_response_actions",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"from_state IS NULL OR from_state IN ({_in(STATES)})",
            name="ck_response_action_transitions_from_state",
        ),
        sa.CheckConstraint(
            f"to_state IN ({_in(STATES)})", name="ck_response_action_transitions_to_state"
        ),
    )
    op.create_index(
        "ix_response_action_transitions_action_id", "response_action_transitions", ["action_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_response_action_transitions_action_id", table_name="response_action_transitions"
    )
    op.drop_table("response_action_transitions")
    op.drop_index("ix_response_actions_state", table_name="response_actions")
    op.drop_index("ix_response_actions_event_id", table_name="response_actions")
    op.drop_table("response_actions")
