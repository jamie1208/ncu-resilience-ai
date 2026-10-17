"""非同步資料庫 engine 與 session。

連線一律使用 UTC 時區；session 由 FastAPI 依賴注入取得，請求結束自動關閉。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """建立（並快取）應用程式共用的 AsyncEngine。"""
    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """回傳綁定共用 engine 的 sessionmaker。"""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依賴：每個請求一個 session。"""
    async with get_sessionmaker()() as session:
        yield session
