"""事件件數 views 改依代表地點分組、排除被手動合併的事件（P8-T03）。

依 ADR-0009 的編號原則使用下一個可用編號 0021。重建 0016 的週／月 materialized views：
- 地點名稱改為代表地點的名稱：從沒有 canonical_id 的地點沿著 canonical_id 往下展開（別名的別名
  也找得到最上層），parent_id 不併入；不在展開結果中（循環）的地點維持自己的名稱，與
  `app.analytics.timeseries.representative` 相同；
- 被手動合併到其他事件的事件（event_merges.merged_event_id）不列入，合併後只算 1 件。
其餘規則（事件日期、false 與 none 不列入）與 0016 相同；downgrade 重建 0016 的 views。

Revision ID: 0021_analytics_views_canonical
Revises: 0020_location_canonical
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_analytics_views_canonical"
down_revision: str | None = "0020_location_canonical"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/analytics/timeseries.py 的 VIEWS 同步
VIEWS = {"week": "analytics_event_counts_week", "month": "analytics_event_counts_month"}
# 別名鏈最多展開 32 層（S13 會攤平別名，正常只有一層）
CANONICAL_SOURCE = """
    WITH RECURSIVE rep(id, root_name, depth) AS (
        SELECT id, name, 0 FROM locations WHERE canonical_id IS NULL
        UNION ALL
        SELECT l.id, rep.root_name, rep.depth + 1
        FROM locations l JOIN rep ON l.canonical_id = rep.id
        WHERE rep.depth < 32
    )
    SELECT e.type AS event_type,
           COALESCE(rep.root_name, l.name) AS location_name,
           COALESCE(
               e.occurrence_start_date,
               (e.created_at AT TIME ZONE 'Asia/Taipei')::date
           ) AS occurred_on
    FROM events e
    LEFT JOIN locations l ON l.id = e.location_id
    LEFT JOIN rep ON rep.id = e.location_id
    WHERE e.status <> 'false' AND e.type <> 'none'
      AND NOT EXISTS (SELECT 1 FROM event_merges m WHERE m.merged_event_id = e.id)
"""

# 0016 的版本（downgrade 用）
LEGACY_SOURCE = """
    SELECT e.type AS event_type,
           l.name AS location_name,
           COALESCE(
               e.occurrence_start_date,
               (e.created_at AT TIME ZONE 'Asia/Taipei')::date
           ) AS occurred_on
    FROM events e
    LEFT JOIN locations l ON l.id = e.location_id
    WHERE e.status <> 'false' AND e.type <> 'none'
"""


def _recreate(source: str) -> None:
    for freq, name in VIEWS.items():
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name}")
        op.execute(f"""
            CREATE MATERIALIZED VIEW {name} AS
            SELECT date_trunc('{freq}', d.occurred_on)::date AS period_start,
                   d.event_type,
                   d.location_name,
                   count(*)::integer AS event_count
            FROM ({source}) d
            GROUP BY 1, 2, 3
            WITH DATA
        """)  # noqa: S608 — 固定 view 名稱、頻率與查詢
        op.execute(f"CREATE INDEX ix_{name}_period_type ON {name} (period_start, event_type)")


def upgrade() -> None:
    _recreate(CANONICAL_SOURCE)


def downgrade() -> None:
    _recreate(LEGACY_SOURCE)
