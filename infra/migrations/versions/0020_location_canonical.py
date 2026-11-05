"""代表地點與地點判斷紀錄（P8-T02，ADR-0018 決策 1-5）：locations.canonical_id／parent_id、location_resolutions。

依 ADR-0009 的編號原則使用下一個可用編號 0020。S13 判斷沒見過的地點名稱是既有地點的別名
（canonical_id 指向代表地點）、位在某個地點之內（parent_id），還是新地點；每次判斷與使用者的
拆開（split）都新增一筆 location_resolutions，最新的一筆是目前狀態。地點刪除時紀錄一併刪除，
被指向的地點刪除時 canonical_id、parent_id、target_id 設為 NULL。

Revision ID: 0020_location_canonical
Revises: 0019_event_merges
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_location_canonical"
down_revision: str | None = "0019_event_merges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/event.py 同步
DECISIONS = "'alias_of', 'inside_of', 'new', 'split'"
METHODS = "'ai', 'fallback', 'manual'"


def upgrade() -> None:
    for column, comment in (
        (
            "canonical_id",
            "代表地點（別名時有值，指向沒有 canonical_id 的地點）；null 表示自己就是代表地點",
        ),
        ("parent_id", "上層地點（例如宿舍區）；只用於篩選，不併入計數"),
    ):
        op.add_column(
            "locations",
            sa.Column(column, postgresql.UUID(as_uuid=True), nullable=True, comment=comment),
        )
        op.create_foreign_key(
            f"fk_locations_{column}_locations",
            "locations",
            "locations",
            [column],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_locations_{column}", "locations", [column])

    op.create_table(
        "location_resolutions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "method",
            sa.String(16),
            nullable=False,
            comment="ai（S13 判斷）、fallback（沒呼叫或 abstain）、manual（使用者）",
        ),
        sa.Column("ai_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_location_resolutions"),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_location_resolutions_location_id_locations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["locations.id"],
            name="fk_location_resolutions_target_id_locations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_location_resolutions_ai_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"decision IN ({DECISIONS})", name="ck_location_resolutions_decision"),
        sa.CheckConstraint(f"method IN ({METHODS})", name="ck_location_resolutions_method"),
    )
    op.create_index("ix_location_resolutions_location_id", "location_resolutions", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_location_resolutions_location_id", table_name="location_resolutions")
    op.drop_table("location_resolutions")
    for column in ("parent_id", "canonical_id"):
        op.drop_index(f"ix_locations_{column}", table_name="locations")
        op.drop_constraint(f"fk_locations_{column}_locations", "locations", type_="foreignkey")
        op.drop_column("locations", column)
