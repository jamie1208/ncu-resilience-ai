"""P0-T07：audit_logs 只能新增，不能修改或刪除（資料庫 trigger），以及稽核服務。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_var
from app.core.security import ANONYMOUS, Principal, Role
from app.models import AuditLog
from app.services.audit import AuditMetadataError, record_audit

pytestmark = pytest.mark.anyio

ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))


async def make_log(session: AsyncSession) -> AuditLog:
    log = await record_audit(
        session,
        ADMIN,
        "source.create",
        target="sources/ncu-announcements",
        request_id=f"req-{uuid.uuid4()}",
        metadata={"fields": ["code", "name"]},
    )
    await session.commit()
    return log


async def expect_append_only(session: AsyncSession, stmt: object) -> None:
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(stmt)  # type: ignore[call-overload]
    await session.rollback()


# --- 資料庫層：trigger --------------------------------------------------------------


async def test_insert_is_allowed(session: AsyncSession) -> None:
    log = await make_log(session)
    got = (await session.execute(select(AuditLog).where(AuditLog.id == log.id))).scalar_one()
    assert got.actor == "admin:u-1"


async def test_update_is_rejected(session: AsyncSession) -> None:
    log_id = (await make_log(session)).id  # rollback 後 ORM 物件會過期，先取出 id
    await expect_append_only(
        session, update(AuditLog).where(AuditLog.id == log_id).values(action="tampered")
    )
    await expect_append_only(
        session, text("UPDATE audit_logs SET actor = 'x' WHERE id = :id").bindparams(id=log_id)
    )


async def test_delete_is_rejected(session: AsyncSession) -> None:
    log = await make_log(session)
    await expect_append_only(session, delete(AuditLog).where(AuditLog.id == log.id))
    await expect_append_only(session, text("DELETE FROM audit_logs"))


async def test_truncate_is_rejected(session: AsyncSession) -> None:
    await make_log(session)
    await expect_append_only(session, text("TRUNCATE audit_logs"))


async def test_orm_modification_is_rejected(session: AsyncSession) -> None:
    log = await make_log(session)
    log.action = "tampered"
    with pytest.raises(DBAPIError, match="append-only"):
        await session.commit()
    await session.rollback()


async def test_rows_survive_rejected_modification(session: AsyncSession) -> None:
    log_id = (await make_log(session)).id
    await expect_append_only(session, text("DELETE FROM audit_logs"))
    got = (await session.execute(select(AuditLog).where(AuditLog.id == log_id))).scalar_one()
    assert got.action == "source.create"


# --- 稽核服務 -----------------------------------------------------------------------


async def test_request_id_defaults_to_current_request(session: AsyncSession) -> None:
    token = request_id_var.set("req-from-middleware")
    try:
        log = await record_audit(session, ANONYMOUS, "report.create", target="reports")
    finally:
        request_id_var.reset(token)
    await session.commit()
    assert log.request_id == "req-from-middleware"
    assert log.actor == "anonymous"
    assert log.meta == {}


async def test_record_does_not_commit(session: AsyncSession) -> None:
    rid = f"req-{uuid.uuid4()}"
    await record_audit(session, ADMIN, "event.verify", target="events/1", request_id=rid)
    await session.rollback()
    rows = (await session.execute(select(AuditLog).where(AuditLog.request_id == rid))).all()
    assert rows == []


@pytest.mark.parametrize(
    "metadata",
    [
        {"password": "x"},
        {"api_key": "x"},
        {"nested": {"token": "x"}},
        {"email": "a@b.c"},
        {"report_text": "原文"},
    ],
)
async def test_metadata_rejects_sensitive_keys(
    session: AsyncSession, metadata: dict[str, object]
) -> None:
    with pytest.raises(AuditMetadataError):
        await record_audit(session, ADMIN, "event.verify", target="events/1", metadata=metadata)


@pytest.mark.parametrize("action", ["", "verify", "Event.Verify", "event verify", "event.verify;"])
async def test_action_format_is_validated(session: AsyncSession, action: str) -> None:
    with pytest.raises(ValueError):
        await record_audit(session, ADMIN, action)
