"""系列分析（P8-T05，ADR-0018 決策 1-5～1-8、ADR-0020 決策 3-2、3-4）。

- event_series 新增 title_source（first_event／ai／manual）：S12 產生的共通名稱取代第一起事件的
  標題，人工改過的名稱不被 S12 覆蓋。已在 2026-11-09 手動改名（稽核 `series.rename`）的系列
  標為 manual。
- 新增 series_analyses：每次 S12 分析的結果（已經過引用驗證、計數由程式算）；系列刪除時一併刪除。
- 新增 series_cause_decisions：系列分析中推測原因的人工確認（沿用 ADR-0017 的規則），只新增不修改。
- response_actions 加系列層級：series_id（系列刪除時設為 NULL）、analysis_id（從 S12 建議建立時
  的分析）、origin 新增 series_measure；系列措施沒有 event_id，所以 event_id 改為可 NULL。

依 ADR-0009 的編號原則使用下一個可用編號 0023。downgrade 會刪除系列層級的措施（event_id 為 NULL
的列）、系列分析與原因確認，名稱來源欄位一併移除（名稱本身保留）。

Revision ID: 0023_series_analysis
Revises: 0022_event_series
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_series_analysis"
down_revision: str | None = "0022_event_series"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/series.py、app/models/response_action.py 同步
TITLE_SOURCES = "'first_event', 'ai', 'manual'"
ANALYSIS_STATUSES = "'ok', 'abstained', 'failed'"
DECISIONS = "'confirmed', 'hypothesized'"
ORIGINS_OLD = "'measure', 'manual'"
ORIGINS_NEW = "'measure', 'manual', 'series_measure'"


def _uuid(name: str, *, nullable: bool = False, **kw: Any) -> sa.Column[Any]:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable, **kw)


def _jsonb(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
    )


def upgrade() -> None:
    op.add_column(
        "event_series",
        sa.Column(
            "title_source",
            sa.String(16),
            server_default="first_event",
            nullable=False,
            comment="first_event：第一起事件的標題；ai：S12 的共通名稱；manual：人工改名",
        ),
    )
    op.create_check_constraint(
        "ck_event_series_title_source", "event_series", f"title_source IN ({TITLE_SOURCES})"
    )
    op.execute(
        "UPDATE event_series SET title_source = 'manual' WHERE 'series:' || id::text IN "
        "(SELECT target FROM audit_logs WHERE action = 'series.rename')"
    )

    op.create_table(
        "series_analyses",
        _uuid("id", server_default=sa.text("gen_random_uuid()")),
        _uuid("series_id"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("abstain_reason", sa.String(64), nullable=True),
        sa.Column("title", sa.String(60), nullable=True, comment="S12 建議的共通名稱（已驗證）"),
        sa.Column("summary", sa.Text(), nullable=True),
        _jsonb("events"),
        _jsonb("bursts"),
        _jsonb("location_groups"),
        sa.Column("pattern_status", sa.String(16), nullable=False),
        sa.Column("pattern_abstain_reason", sa.String(64), nullable=True),
        _jsonb("patterns"),
        _jsonb("causes"),
        _jsonb("measures"),
        _jsonb("dropped"),
        _uuid("ai_run_id", nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_series_analyses"),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["event_series.id"],
            name="fk_series_analyses_series_id_event_series",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_analysis_runs.id"],
            name="fk_series_analyses_ai_run_id_ai_analysis_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"status IN ({ANALYSIS_STATUSES})", name="ck_series_analyses_status"),
        sa.CheckConstraint(
            f"pattern_status IN ({ANALYSIS_STATUSES})", name="ck_series_analyses_pattern_status"
        ),
    )
    op.create_index(
        "ix_series_analyses_series_created", "series_analyses", ["series_id", "created_at"]
    )

    op.create_table(
        "series_cause_decisions",
        _uuid("id", server_default=sa.text("gen_random_uuid()")),
        _uuid("analysis_id"),
        sa.Column("cause_id", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("basis", sa.String(200), nullable=True, comment="確認依據；不含個資"),
        sa.Column("decided_by", sa.String(100), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_series_cause_decisions"),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["series_analyses.id"],
            name="fk_series_cause_decisions_analysis_id_series_analyses",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"decision IN ({DECISIONS})", name="ck_series_cause_decisions_decision"),
        sa.CheckConstraint(
            "(decision = 'confirmed') = (basis IS NOT NULL)", name="ck_series_cause_decisions_basis"
        ),
    )
    op.create_index(
        "ix_series_cause_decisions_analysis_cause",
        "series_cause_decisions",
        ["analysis_id", "cause_id", "decided_at"],
    )

    op.alter_column("response_actions", "event_id", existing_type=postgresql.UUID(), nullable=True)
    op.add_column("response_actions", _uuid("series_id", nullable=True))
    op.add_column("response_actions", _uuid("analysis_id", nullable=True))
    op.create_foreign_key(
        "fk_response_actions_series_id_event_series",
        "response_actions",
        "event_series",
        ["series_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_response_actions_analysis_id_series_analyses",
        "response_actions",
        "series_analyses",
        ["analysis_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_response_actions_series_id", "response_actions", ["series_id"])
    op.drop_constraint("ck_response_actions_origin", "response_actions", type_="check")
    op.create_check_constraint(
        "ck_response_actions_origin", "response_actions", f"origin IN ({ORIGINS_NEW})"
    )
    op.create_check_constraint(
        "ck_response_actions_series_measure",
        "response_actions",
        "origin <> 'series_measure' OR measure_index IS NOT NULL",
    )
    op.create_unique_constraint(
        "uq_response_actions_analysis_id_measure_index",
        "response_actions",
        ["analysis_id", "measure_index"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM response_actions WHERE event_id IS NULL OR origin = 'series_measure'")
    op.drop_constraint(
        "uq_response_actions_analysis_id_measure_index", "response_actions", type_="unique"
    )
    op.drop_constraint("ck_response_actions_series_measure", "response_actions", type_="check")
    op.drop_constraint("ck_response_actions_origin", "response_actions", type_="check")
    op.create_check_constraint(
        "ck_response_actions_origin", "response_actions", f"origin IN ({ORIGINS_OLD})"
    )
    op.drop_index("ix_response_actions_series_id", table_name="response_actions")
    op.drop_constraint(
        "fk_response_actions_analysis_id_series_analyses", "response_actions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_response_actions_series_id_event_series", "response_actions", type_="foreignkey"
    )
    op.drop_column("response_actions", "analysis_id")
    op.drop_column("response_actions", "series_id")
    op.alter_column("response_actions", "event_id", existing_type=postgresql.UUID(), nullable=False)

    op.drop_index("ix_series_cause_decisions_analysis_cause", table_name="series_cause_decisions")
    op.drop_table("series_cause_decisions")
    op.drop_index("ix_series_analyses_series_created", table_name="series_analyses")
    op.drop_table("series_analyses")
    op.drop_constraint("ck_event_series_title_source", "event_series", type_="check")
    op.drop_column("event_series", "title_source")
