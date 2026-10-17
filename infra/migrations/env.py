"""Alembic 執行環境（async，asyncpg）。

連線 URL 優先取 `config.attributes["database_url"]`（測試用），否則由 Settings 組成。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.models import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> URL:
    url = config.attributes.get("database_url")
    return url if isinstance(url, URL) else Settings().database_url


def run_migrations_offline() -> None:
    """產生 SQL 而不連線（alembic upgrade --sql）。"""
    context.configure(
        url=_url().render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """連線資料庫並執行 migration。"""
    engine = create_async_engine(_url(), connect_args={"server_settings": {"timezone": "UTC"}})
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
