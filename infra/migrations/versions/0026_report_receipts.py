"""匿名回報收據碼（P8-T08，v1.0 §2、D11）：report_receipts。

依 ADR-0009 的編號原則使用下一個可用編號 0026。每則投稿（原投稿或補件）一列：收據碼的
SHA-256 與對應的 anonymous_reports.id。明文收據碼不落地；anonymous_reports 本身不變
（仍然只有內容、選填地點與收到時間）。既有投稿沒有收據碼。

downgrade 刪除整張表（收據碼對應，投稿本身不受影響）。

Revision ID: 0026_report_receipts
Revises: 0025_series_publications
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_report_receipts"
down_revision: str | None = "0025_series_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_receipts",
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("is_supplement", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["anonymous_reports.id"],
            name="fk_report_receipts_report_id_anonymous_reports",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("report_id", name="pk_report_receipts"),
    )
    op.create_index("ix_report_receipts_code_hash", "report_receipts", ["code_hash"])


def downgrade() -> None:
    op.drop_index("ix_report_receipts_code_hash", table_name="report_receipts")
    op.drop_table("report_receipts")
