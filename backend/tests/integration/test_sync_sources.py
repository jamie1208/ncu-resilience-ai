"""P1-T07：依 config/sources.yaml 建立缺少的 sources 資料列。"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import PermissionDeniedError, Principal, Role
from app.ingestion.source_settings import SourceSettings
from app.ingestion.sync_sources import CLI_PRINCIPAL, sync_sources
from app.models.audit import AuditLog
from app.models.source import Source

pytestmark = pytest.mark.anyio

SETTINGS = {
    "ncu-announcements": SourceSettings(
        type="rpage", name="中央大學校園公告", poll_interval=1800, options={"list_url": "x"}
    ),
    "ncu-sec": SourceSettings(type="rpage", options={"list_url": "y"}),
}
INGESTION = Principal(
    kind="service", subject="ingestion", roles=frozenset({Role.SERVICE_INGESTION})
)


async def sources(session: AsyncSession) -> dict[str, Source]:
    rows = (await session.execute(select(Source))).scalars().all()
    return {s.code: s for s in rows}


async def test_creates_missing_sources_as_approved_and_enabled(session: AsyncSession) -> None:
    async with session.begin():
        created = await sync_sources(session, CLI_PRINCIPAL, SETTINGS)
    assert created == ["ncu-announcements", "ncu-sec"]

    rows = await sources(session)
    ann = rows["ncu-announcements"]
    assert (ann.name, ann.type, ann.access_policy, ann.enabled, ann.poll_interval) == (
        "中央大學校園公告",
        "rpage",
        "approved",
        True,
        1800,
    )
    # 沒有名稱與間隔時：名稱用代碼，間隔留空（由派工使用設定檔或預設值）
    assert (rows["ncu-sec"].name, rows["ncu-sec"].poll_interval) == ("ncu-sec", None)


async def test_sync_is_idempotent_and_keeps_manual_changes(session: AsyncSession) -> None:
    async with session.begin():
        await sync_sources(session, CLI_PRINCIPAL, SETTINGS)
    async with session.begin():
        row = (await sources(session))["ncu-sec"]
        row.enabled = False  # 管理者停用
        row.access_policy = "deferred"

    async with session.begin():
        created = await sync_sources(session, CLI_PRINCIPAL, SETTINGS)
    assert created == []
    row = (await sources(session))["ncu-sec"]
    assert (row.enabled, row.access_policy) == (False, "deferred")


async def test_sync_writes_one_audit_log_when_something_is_created(
    session: AsyncSession,
) -> None:
    async with session.begin():
        await sync_sources(session, CLI_PRINCIPAL, SETTINGS)
    async with session.begin():
        await sync_sources(session, CLI_PRINCIPAL, SETTINGS)
    logs = (await session.execute(select(AuditLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].actor == "admin:cli"
    assert logs[0].action == "sources.sync"
    assert logs[0].meta == {"created": ["ncu-announcements", "ncu-sec"]}


async def test_sync_requires_sources_manage(session: AsyncSession) -> None:
    with pytest.raises(PermissionDeniedError):
        async with session.begin():
            await sync_sources(session, INGESTION, SETTINGS)
    assert await sources(session) == {}
