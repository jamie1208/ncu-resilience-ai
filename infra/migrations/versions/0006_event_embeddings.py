"""events.embedding（pgvector）與 events.occurrence_start_date（P2-T09）。

S04 event-linker 的候選召回：以向量的 cosine 距離排序，並以事件日期限制時間視窗。
維度 1536 對應 config/llm.yaml 的 embedding.dimensions
（使用者 2026-10-21 決定 text-embedding-3-small）。
資料量小，先不建 HNSW 索引（精確搜尋）。

Revision ID: 0006_event_embeddings
Revises: 0005_ai_runs
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0006_event_embeddings"
down_revision: str | None = "0005_ai_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIMENSIONS = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "events",
        sa.Column(
            "occurrence_start_date",
            sa.Date(),
            nullable=True,
            comment="事件開始日期（台北時間，S02 抽取）",
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "embedding",
            Vector(DIMENSIONS),
            nullable=True,
            comment="事件標題與證據的向量（S04 候選召回）",
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "embedding")
    op.drop_column("events", "occurrence_start_date")
    op.execute("DROP EXTENSION IF EXISTS vector")
