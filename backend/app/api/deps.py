"""API 的依賴注入：資料庫 session、請求者身分、權限檢查。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import ANONYMOUS, Permission, Principal, require
from app.db.session import get_sessionmaker


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """每個請求一個 session，請求結束自動關閉。"""
    async with get_sessionmaker()() as session:
        yield session


async def get_principal() -> Principal:
    """請求者身分。

    登入（`Authenticator`）於 P3-T01 實作；在那之前一律為匿名，所以管理 API 都會回 401。
    """
    return ANONYMOUS


def require_permission(
    permission: Permission,
) -> Callable[[Principal], Awaitable[Principal]]:
    """產生依賴：沒有 `permission` 時拋出 PermissionDeniedError（匿名 401、其他 403）。"""

    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        require(principal, permission)
        return principal

    return dependency
