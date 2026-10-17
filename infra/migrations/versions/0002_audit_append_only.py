"""audit_logs 只能新增（禁止 UPDATE／DELETE／TRUNCATE），events.status 加上 CHECK 約束。

Revision ID: 0002_audit_append_only
Revises: 0001_core
Create Date: 2026-10-17

狀態值為建立時的快照（app/domain/states.py 的 EventStatus），之後新增狀態需另寫 migration。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_audit_append_only"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_STATUSES = (
    "detected",
    "analyzing",
    "unverified",
    "verified",
    "disputed",
    "false",
    "monitoring",
    "resolved",
    "archived",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION audit_logs_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()
        """
    )
    # TRUNCATE 不會觸發列層級 trigger，需要另外的陳述式層級 trigger
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_append_only()
        """
    )

    values = ", ".join(f"'{s}'" for s in EVENT_STATUSES)
    # op.f() 表示名稱已是最終名稱，避免再套一次 ck_%(table_name)s_ 命名規則
    op.create_check_constraint(op.f("ck_events_status"), "events", f"status IN ({values})")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_events_status"), "events", type_="check")
    op.execute("DROP TRIGGER audit_logs_no_truncate ON audit_logs")
    op.execute("DROP TRIGGER audit_logs_no_update_delete ON audit_logs")
    op.execute("DROP FUNCTION audit_logs_append_only()")
