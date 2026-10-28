"""事件件數的週／月 materialized views（P6-T03）。

依 ADR-0009 的編號原則使用下一個可用編號 0016。以 (週期起日, 類別, 地點名稱) 分組計數，
事件日期與 app/resilience/recurrence.py 一致：S02 的發生日期，沒有時用建立時間的台北日期；
查證為不實（false）與類別為 none 的事件不列入。views 不會自動更新，由
`app.analytics.timeseries.refresh_views` 重新整理。

Revision ID: 0016_analytics_views
Revises: 0015_relation_decisions
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_analytics_views"
down_revision: str | None = "0015_relation_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/analytics/timeseries.py 的 VIEWS 同步
VIEWS = {"week": "analytics_event_counts_week", "month": "analytics_event_counts_month"}


def _create(freq: str, name: str) -> None:
    op.execute(f"""
        CREATE MATERIALIZED VIEW {name} AS
        SELECT date_trunc('{freq}', d.occurred_on)::date AS period_start,
               d.event_type,
               d.location_name,
               count(*)::integer AS event_count
        FROM (
            SELECT e.type AS event_type,
                   l.name AS location_name,
                   COALESCE(
                       e.occurrence_start_date,
                       (e.created_at AT TIME ZONE 'Asia/Taipei')::date
                   ) AS occurred_on
            FROM events e
            LEFT JOIN locations l ON l.id = e.location_id
            WHERE e.status <> 'false' AND e.type <> 'none'
        ) d
        GROUP BY 1, 2, 3
        WITH DATA
    """)  # noqa: S608 — 固定 view 名稱與頻率
    op.execute(f"CREATE INDEX ix_{name}_period_type ON {name} (period_start, event_type)")


def upgrade() -> None:
    for freq, name in VIEWS.items():
        _create(freq, name)


def downgrade() -> None:
    for name in VIEWS.values():
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name}")
