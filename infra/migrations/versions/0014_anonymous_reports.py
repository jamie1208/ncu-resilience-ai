"""匿名回報收件匣（P5-T13）：anonymous_reports。

依 ADR-0009 的編號原則使用下一個可用編號 0014。公開端點只寫入這張表，`anonymous_report`
Adapter 依遞增 id 拉進既有擷取管線。只有內容、選填地點與收到時間：不保存 IP、裝置資訊或
聯絡方式（ADR-0015 第 8 點）。

Revision ID: 0014_anonymous_reports
Revises: 0013_publications
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_anonymous_reports"
down_revision: str | None = "0013_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/anonymous_report.py 同步
CONTENT_MIN_CHARS = 10
CONTENT_MAX_CHARS = 2000


def upgrade() -> None:
    op.create_table(
        "anonymous_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("location", sa.String(100), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_anonymous_reports"),
        sa.CheckConstraint(
            f"char_length(content) BETWEEN {CONTENT_MIN_CHARS} AND {CONTENT_MAX_CHARS}",
            name="ck_anonymous_reports_content_length",
        ),
    )


def downgrade() -> None:
    op.drop_table("anonymous_reports")
