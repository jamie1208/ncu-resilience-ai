"""依 config/sources.yaml 建立缺少的 sources 資料列（P1-T07）。

用法（repo 根目錄）：`make sources`，或在後端容器內執行 `python -m app.ingestion.sync_sources`。

- 只建立「缺少」的來源：access_policy 為 approved、啟用。sources.yaml 只能登記來源盤點中
  核准的來源（由測試檢查），所以這裡直接標為 approved。
- 已存在的資料列一律不改：管理者停用或改為暫緩的來源，不會被重新打開。
- 以 `admin:cli` 身分執行（需要 `sources:manage`），有建立時寫入一筆稽核紀錄。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import Permission, Principal, Role, require
from app.ingestion.source_settings import SourceSettings, load_source_settings
from app.ingestion.types import AccessPolicy
from app.models.source import Source
from app.services.audit import record_audit

CLI_PRINCIPAL = Principal(kind="admin", subject="cli", roles=frozenset({Role.ADMIN}))
"""在終端機執行管理指令的操作者。"""


async def sync_sources(
    session: AsyncSession,
    principal: Principal,
    settings: Mapping[str, SourceSettings],
) -> list[str]:
    """建立設定檔中有、資料庫中沒有的來源，回傳建立的來源代碼；只 flush 不 commit。"""
    require(principal, Permission.SOURCES_MANAGE)
    existing = set((await session.execute(select(Source.code))).scalars())
    created: list[str] = []
    for code, s in settings.items():
        if code in existing:
            continue
        session.add(
            Source(
                code=code,
                name=s.name or code,
                type=s.type,
                access_policy=AccessPolicy.APPROVED.value,
                enabled=True,
                poll_interval=s.poll_interval,
            )
        )
        created.append(code)
    if created:
        await session.flush()
        await record_audit(
            session, principal, "sources.sync", target="sources", metadata={"created": created}
        )
    return created


async def _main() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            created = await sync_sources(session, CLI_PRINCIPAL, load_source_settings())
    finally:
        await engine.dispose()
    print(f"已建立 {len(created)} 個來源：{', '.join(created) or '無'}")


if __name__ == "__main__":
    asyncio.run(_main())
