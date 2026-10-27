"""公開狀態（P5-T10）：publications、publication_versions、publication_takedowns。

依 ADR-0009 的編號原則使用下一個可用編號 0013。每個事件最多一筆 publications，記錄目前的
公開程度（app/publishing/policy.py 的 PublicLevel）與狀態：published（公開中）、withdrawn
（規則判斷不再公開，例如查證為不實）、taken_down（使用者下架，P5-T14）。公開內容每次改變
新增一筆 publication_versions（已遮蔽 PII 的內容快照，不修改、不刪除）；每次下架新增一筆
publication_takedowns。

Revision ID: 0013_publications
Revises: 0012_response_actions
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_publications"
down_revision: str | None = "0012_response_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 app/models/publication.py 同步；已上線的值不可改名
LEVELS = ("content", "safety_notice", "none")
STATES = ("published", "withdrawn", "taken_down")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _uuid_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamp(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _jsonb(name: str, *, comment: str | None = None) -> sa.Column[object]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'[]'::jsonb"),
        nullable=False,
        comment=comment,
    )


def upgrade() -> None:
    op.create_table(
        "publications",
        _uuid_pk(),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "version", sa.Integer(), nullable=False, comment="目前版本；對應 publication_versions"
        ),
        sa.Column("policy_version", sa.String(32), nullable=False),
        _jsonb("reasons", comment="最近一次判斷觸發的規則代碼"),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="疑似指控，等使用者在後台判斷",
        ),
        sa.Column("taken_down_at", sa.DateTime(timezone=True), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_publications"),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_publications_event_id_events", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("event_id", name="uq_publications_event_id"),
        sa.CheckConstraint(f"level IN ({_in(LEVELS)})", name="ck_publications_level"),
        sa.CheckConstraint(f"state IN ({_in(STATES)})", name="ck_publications_state"),
        sa.CheckConstraint("version >= 1", name="ck_publications_version"),
        sa.CheckConstraint(
            "state <> 'published' OR level <> 'none'", name="ck_publications_published_level"
        ),
        sa.CheckConstraint(
            "(state = 'taken_down') = (taken_down_at IS NOT NULL)",
            name="ck_publications_taken_down",
        ),
    )
    op.create_index("ix_publications_state", "publications", ["state"])

    op.create_table(
        "publication_versions",
        _uuid_pk(),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="公開規則產生、已遮蔽 PII 的內容；level 為 none 時為 null",
        ),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        _jsonb("reasons"),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_publication_versions"),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publications.id"],
            name="fk_publication_versions_publication_id_publications",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "publication_id", "version", name="uq_publication_versions_publication_id_version"
        ),
        sa.CheckConstraint(f"level IN ({_in(LEVELS)})", name="ck_publication_versions_level"),
        sa.CheckConstraint(
            "(level = 'none') = (content IS NULL)", name="ck_publication_versions_content"
        ),
    )

    op.create_table(
        "publication_takedowns",
        _uuid_pk(),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, comment="下架時公開中的版本"),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_publication_takedowns"),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publications.id"],
            name="fk_publication_takedowns_publication_id_publications",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_publication_takedowns_publication_id", "publication_takedowns", ["publication_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_publication_takedowns_publication_id", table_name="publication_takedowns")
    op.drop_table("publication_takedowns")
    op.drop_table("publication_versions")
    op.drop_index("ix_publications_state", table_name="publications")
    op.drop_table("publications")
