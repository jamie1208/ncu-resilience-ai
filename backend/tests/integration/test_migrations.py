"""P0-T05：migration 可往返（G0-2），且 ORM models 與 migration 一致。"""

from __future__ import annotations

import asyncio
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import URL, Connection, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Base

CORE_TABLES = {
    "sources",
    "ingestion_runs",
    "source_items",
    "source_item_versions",
    "events",
    "event_evidence",
    "locations",
    "audit_logs",
}


def _run(url: URL, fn: Any) -> Any:
    async def go() -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(fn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _schema(conn: Connection) -> dict[str, list[tuple[str, str, bool]]]:
    insp = inspect(conn)
    return {
        t: sorted((c["name"], str(c["type"]), bool(c["nullable"])) for c in insp.get_columns(t))
        for t in insp.get_table_names()
    }


def test_upgrade_downgrade_upgrade_roundtrip(alembic_cfg: Config, test_db_url: URL) -> None:
    command.upgrade(alembic_cfg, "head")
    first = _run(test_db_url, _schema)
    assert set(first) >= CORE_TABLES

    command.downgrade(alembic_cfg, "base")
    after_down = _run(test_db_url, _schema)
    assert set(after_down) <= {"alembic_version"}

    command.upgrade(alembic_cfg, "head")
    assert _run(test_db_url, _schema) == first


def test_models_match_migrations(alembic_cfg: Config, test_db_url: URL) -> None:
    command.upgrade(alembic_cfg, "head")

    def diff(conn: Connection) -> list[Any]:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return list(compare_metadata(ctx, Base.metadata))

    assert _run(test_db_url, diff) == []
