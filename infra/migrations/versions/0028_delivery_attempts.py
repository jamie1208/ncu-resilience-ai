"""通知失敗重試（P8-T07，v1.0 A10）：alert_deliveries 加上嘗試次數與下次時間。

依 ADR-0009 的編號原則使用下一個可用編號 0028。

- `attempts`：已送出的次數（含第一次）；既有列一律視為 1 次；
- `next_attempt_at`：failed 且還要重試時的下次時間；送達、放棄重試或不需重試時為 NULL。
  既有的 failed 列不回填下次時間（升級後不會突然重送舊的通知）。
- 部分索引只涵蓋待重試的列，排程掃描不必讀整張表。

唯一約束 (alert_id, channel, destination, version) 不變：重試沿用同一列，不會新增遞送。
downgrade 移除兩個欄位與索引（只失去重試排程，遞送紀錄本身保留）。

Revision ID: 0028_delivery_attempts
Revises: 0027_impact_evaluations
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_delivery_attempts"
down_revision: str | None = "0027_impact_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alert_deliveries",
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="已送出的次數（含第一次）",
        ),
    )
    op.add_column(
        "alert_deliveries",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="failed 且還要重試時的下次時間；其餘為 NULL",
        ),
    )
    op.create_check_constraint(
        op.f("ck_alert_deliveries_attempts_positive"), "alert_deliveries", "attempts >= 1"
    )
    op.create_index(
        "ix_alert_deliveries_next_attempt_at",
        "alert_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("next_attempt_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_alert_deliveries_next_attempt_at", table_name="alert_deliveries")
    op.drop_constraint(
        op.f("ck_alert_deliveries_attempts_positive"), "alert_deliveries", type_="check"
    )
    op.drop_column("alert_deliveries", "next_attempt_at")
    op.drop_column("alert_deliveries", "attempts")
