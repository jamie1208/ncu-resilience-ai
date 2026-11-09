"""改善措施與原因確認掛到系列（P8-T13，ADR-0020 決策 3-3、3-4）。

- response_actions：既有事件層級的措施（event_id 不為 NULL、還沒有 series_id）掛到該事件目前所屬的
  系列；event_id 保留供回溯。不在任何系列的事件（例如被合併的）不掛。
- report_cause_decisions 新增 series_id（系列刪除時設為 NULL），依舊小報告的事件所屬系列回填；
  report_id 保留，舊的確認紀錄唯讀。
- event_reports 不動（保留唯讀）。

依 ADR-0009 的編號原則使用下一個可用編號 0024。downgrade 不刪資料：事件層級措施的 series_id
改回 NULL（系列層級的措施沒有 event_id，不受影響），report_cause_decisions 的 series_id 欄位移除。

Revision ID: 0024_series_actions_backfill
Revises: 0023_series_analysis
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_series_actions_backfill"
down_revision: str | None = "0023_series_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "report_cause_decisions",
        sa.Column(
            "series_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="遷移時事件所屬的系列（P8-T13）",
        ),
    )
    op.create_foreign_key(
        "fk_report_cause_decisions_series_id_event_series",
        "report_cause_decisions",
        "event_series",
        ["series_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_report_cause_decisions_series_id", "report_cause_decisions", ["series_id"])
    op.execute(
        """
        UPDATE report_cause_decisions AS d
        SET series_id = m.series_id
        FROM event_reports AS r
        JOIN event_series_members AS m ON m.event_id = r.event_id
        WHERE d.report_id = r.id
        """
    )
    op.execute(
        """
        UPDATE response_actions AS a
        SET series_id = m.series_id
        FROM event_series_members AS m
        WHERE a.event_id = m.event_id AND a.series_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute("UPDATE response_actions SET series_id = NULL WHERE event_id IS NOT NULL")
    op.drop_index("ix_report_cause_decisions_series_id", table_name="report_cause_decisions")
    op.drop_constraint(
        "fk_report_cause_decisions_series_id_event_series",
        "report_cause_decisions",
        type_="foreignkey",
    )
    op.drop_column("report_cause_decisions", "series_id")
