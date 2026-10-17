"""擷取死信表 ingestion_dead_letters 與 sources.cursor（P1-T07）。

執行計畫原寫 0002_ingestion_dead_letters，但 0002 已由 P0-T07 的 audit_logs trigger
使用，改為 0003。

Revision ID: 0003_ingestion_dead_letters
Revises: 0002_audit_append_only
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_ingestion_dead_letters"
down_revision: str | None = "0002_audit_append_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "cursor",
            sa.Text(),
            nullable=True,
            comment="增量擷取的 cursor（Adapter 自訂的不透明字串，P1-T07）",
        ),
    )
    op.create_table(
        "ingestion_dead_letters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("error_type", sa.String(100), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True, comment="失敗當時的 cursor"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_dead_letters"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_ingestion_dead_letters_source_id_sources",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_runs.id"],
            name="fk_ingestion_dead_letters_run_id_ingestion_runs",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_ingestion_dead_letters_source_id_created_at",
        "ingestion_dead_letters",
        ["source_id", "created_at"],
    )


def downgrade() -> None:
    """刪除死信表與 cursor 欄位；死信紀錄與擷取進度將一併刪除。"""
    op.drop_index(
        "ix_ingestion_dead_letters_source_id_created_at", table_name="ingestion_dead_letters"
    )
    op.drop_table("ingestion_dead_letters")
    op.drop_column("sources", "cursor")
