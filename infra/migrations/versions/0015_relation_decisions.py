"""event_relations 加入 hypothesized_cause、方法與人工決策（P6-T01）。

依 ADR-0009 的編號原則使用下一個可用編號 0015。既有的關聯都是 P2-T09 管線以 S04 寫入，
方法回填為 ai、決策為 pending。決策只記目前狀態；變更歷史在稽核紀錄（relation.decide）。

Revision ID: 0015_relation_decisions
Revises: 0014_anonymous_reports
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_relation_decisions"
down_revision: str | None = "0014_anonymous_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/ai.py 同步
TYPES_BEFORE = "'same_event', 'related_event'"
TYPES_AFTER = "'same_event', 'related_event', 'hypothesized_cause'"
METHODS = "'ai', 'rule', 'manual'"
DECISIONS = "'pending', 'confirmed', 'rejected'"
# 0005 建立時套上命名慣例而重複了前綴；升版改為與 model 一致的名稱，降版還原
OLD_TYPE_CHECK = "ck_event_relations_ck_event_relations_type"


def upgrade() -> None:
    op.drop_constraint(op.f(OLD_TYPE_CHECK), "event_relations", type_="check")
    op.create_check_constraint(
        op.f("ck_event_relations_type"), "event_relations", f"type IN ({TYPES_AFTER})"
    )
    op.add_column(
        "event_relations",
        sa.Column(
            "method",
            sa.String(16),
            nullable=False,
            server_default="ai",
            comment="關聯由誰提出：ai（Skill）、rule（確定性規則）、manual（人工）",
        ),
    )
    op.add_column(
        "event_relations",
        sa.Column("decision", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "event_relations", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("event_relations", sa.Column("decided_by", sa.String(100), nullable=True))
    op.add_column(
        "event_relations",
        sa.Column("decision_note", sa.String(200), nullable=True, comment="已遮蔽個資"),
    )
    op.create_check_constraint(
        op.f("ck_event_relations_method"), "event_relations", f"method IN ({METHODS})"
    )
    op.create_check_constraint(
        op.f("ck_event_relations_decision"), "event_relations", f"decision IN ({DECISIONS})"
    )
    op.create_check_constraint(
        op.f("ck_event_relations_decided"),
        "event_relations",
        "(decision = 'pending') = (decided_at IS NULL AND decided_by IS NULL)",
    )
    op.create_index("ix_event_relations_decision", "event_relations", ["decision"])


def downgrade() -> None:
    op.drop_index("ix_event_relations_decision", table_name="event_relations")
    for name in ("decided", "decision", "method"):
        op.drop_constraint(op.f(f"ck_event_relations_{name}"), "event_relations", type_="check")
    for column in ("decision_note", "decided_by", "decided_at", "decision", "method"):
        op.drop_column("event_relations", column)
    # 舊版沒有推測原因的類型；降版會刪除這類關聯
    op.execute("DELETE FROM event_relations WHERE type = 'hypothesized_cause'")
    op.drop_constraint(op.f("ck_event_relations_type"), "event_relations", type_="check")
    op.create_check_constraint(op.f(OLD_TYPE_CHECK), "event_relations", f"type IN ({TYPES_BEFORE})")
