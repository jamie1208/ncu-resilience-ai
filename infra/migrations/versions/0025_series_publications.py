"""公開系列頁（P8-T06，ADR-0018 決策 1-9、ADR-0020 決策 2）：publications 可以是事件或系列。

- publications 新增 series_id（event_series，系列刪除時 CASCADE，唯一）；event_id 改為可為 NULL；
  CHECK：event_id 與 series_id 恰好一個不為 NULL。系列頁沿用同一套版本快照、一鍵下架與稽核。
- 公開提醒（疑似指控、推測內容首次公開或改變）記在 publication_versions.reasons（`remind`），
  不另建資料表。

依 ADR-0009 的編號原則使用下一個可用編號 0025。downgrade 刪除系列的公開狀態（含版本與下架紀錄，
CASCADE）後移除欄位，event_id 改回 NOT NULL；事件的公開狀態不受影響。

Revision ID: 0025_series_publications
Revises: 0024_series_actions_backfill
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_series_publications"
down_revision: str | None = "0024_series_actions_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "publications",
        sa.Column(
            "series_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "event_series.id", ondelete="CASCADE", name=op.f("fk_publications_series_id_event_series")
            ),
            nullable=True,
            comment="系列的公開狀態（P8-T06）；與 event_id 恰好一個不為 NULL",
        ),
    )
    op.create_unique_constraint(op.f("uq_publications_series_id"), "publications", ["series_id"])
    op.alter_column("publications", "event_id", existing_type=postgresql.UUID(), nullable=True)
    op.create_check_constraint(
        op.f("ck_publications_subject"), "publications", "(event_id IS NULL) <> (series_id IS NULL)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM publications WHERE series_id IS NOT NULL")
    op.drop_constraint(op.f("ck_publications_subject"), "publications", type_="check")
    op.alter_column("publications", "event_id", existing_type=postgresql.UUID(), nullable=False)
    op.drop_constraint(op.f("uq_publications_series_id"), "publications", type_="unique")
    op.drop_column("publications", "series_id")
