"""核心資料表：sources、ingestion_runs、source_items、source_item_versions、
events、event_evidence、locations、audit_logs（規格 §9）。

Revision ID: 0001_core
Revises:
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _ts(name: str, *, nullable: bool = False, default_now: bool = True) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def upgrade() -> None:
    op.create_table(
        "sources",
        _uuid_pk(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("access_policy", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("poll_interval", sa.Integer(), nullable=True, comment="擷取間隔（秒）"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.UniqueConstraint("code", name="uq_sources_code"),
    )

    op.create_table(
        "ingestion_runs",
        _uuid_pk(),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        _ts("started_at", default_now=False),
        _ts("finished_at", nullable=True, default_now=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "counts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_runs"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_ingestion_runs_source_id_sources",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_ingestion_runs_source_id_started_at", "ingestion_runs", ["source_id", "started_at"]
    )

    op.create_table(
        "source_items",
        _uuid_pk(),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        _ts("published_at", nullable=True, default_now=False),
        _ts("fetched_at"),
        sa.PrimaryKeyConstraint("id", name="pk_source_items"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_source_items_source_id_sources",
            ondelete="RESTRICT",
        ),
    )
    # unique(source_id, external_id)；external_id 為空時以 (source_id, url) 替代
    op.create_index(
        "uq_source_items_source_id_external_id",
        "source_items",
        ["source_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_source_items_source_id_url_no_external_id",
        "source_items",
        ["source_id", "url"],
        unique=True,
        postgresql_where=sa.text("external_id IS NULL"),
    )

    op.create_table(
        "source_item_versions",
        _uuid_pk(),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "content_hash", sa.String(64), nullable=False, comment="正規化內容的 SHA-256（hex）"
        ),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        _ts("captured_at"),
        sa.PrimaryKeyConstraint("id", name="pk_source_item_versions"),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["source_items.id"],
            name="fk_source_item_versions_item_id_source_items",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "item_id", "content_hash", name="uq_source_item_versions_item_id_content_hash"
        ),
    )

    op.create_table(
        "locations",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("lat", sa.Double(), nullable=True),
        sa.Column("lng", sa.Double(), nullable=True),
        sa.Column("precision", sa.String(32), nullable=True, comment="例：building、area、unknown"),
        sa.PrimaryKeyConstraint("id", name="pk_locations"),
    )

    op.create_table(
        "events",
        _uuid_pk(),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_events_location_id_locations",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_events_type", "events", ["type"])
    op.create_index("ix_events_status", "events", ["status"])
    op.create_index("ix_events_created_at", "events", ["created_at"])

    op.create_table(
        "event_evidence",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "support_type",
            sa.String(32),
            nullable=False,
            comment="例：supports、contradicts、mentions",
        ),
        sa.Column("excerpt", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("event_id", "source_item_id", name="pk_event_evidence"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_evidence_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_item_id"],
            ["source_items.id"],
            name="fk_event_evidence_source_item_id_source_items",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "audit_logs",
        _uuid_pk(),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target", sa.String(300), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    """依相依順序反向刪除；資料將一併刪除。"""
    op.drop_table("audit_logs")
    op.drop_table("event_evidence")
    op.drop_table("events")
    op.drop_table("locations")
    op.drop_table("source_item_versions")
    op.drop_table("source_items")
    op.drop_table("ingestion_runs")
    op.drop_table("sources")
