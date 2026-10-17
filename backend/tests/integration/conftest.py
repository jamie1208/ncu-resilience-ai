"""整合測試共用設定：使用獨立的 `<POSTGRES_DB>_test` 資料庫，不碰開發資料。

需要先啟動資料庫（在 repo 根目錄執行 `make up`）。連不上時測試直接失敗並說明原因。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _test_url() -> URL:
    settings = Settings()
    base = settings.database_url
    return base.set(database=f"{base.database}_test")


async def _ensure_database(url: URL) -> None:
    try:
        conn = await asyncpg.connect(
            user=url.username,
            password=url.password,
            host=url.host,
            port=url.port,
            database="postgres",
            timeout=5,
        )
    except (OSError, asyncpg.PostgresError) as e:
        pytest.fail(
            f"無法連線 PostgreSQL（{url.host}:{url.port}）：{e}。請先在 repo 根目錄執行 make up。"
        )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def test_db_url() -> URL:
    url = _test_url()
    asyncio.run(_ensure_database(url))
    return url


@pytest.fixture(scope="session")
def alembic_cfg(test_db_url: URL) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = test_db_url
    return cfg


@pytest.fixture(scope="module")
def migrated(alembic_cfg: Config) -> Iterator[Config]:
    command.upgrade(alembic_cfg, "head")
    yield alembic_cfg


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
async def session(test_db_url: URL, migrated: Config) -> AsyncIterator[AsyncSession]:
    """每個測試一個 session；結束後清空所有業務資料表。"""
    engine = create_async_engine(test_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    async with engine.begin() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
        ).scalars()
        names = ", ".join(f'"{t}"' for t in tables)
        if names:
            await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    await engine.dispose()
