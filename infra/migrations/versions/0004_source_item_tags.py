"""source_items.tags：來源分類標籤（P1-T15）。

依 ADR-0009 的編號原則使用下一個可用編號 0004。

Revision ID: 0004_source_item_tags
Revises: 0003_ingestion_dead_letters
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_source_item_tags"
down_revision: str | None = "0003_ingestion_dead_letters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_items",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="來源分類標籤（P1-T15），依字母排序、不重複",
        ),
    )


def downgrade() -> None:
    op.drop_column("source_items", "tags")
