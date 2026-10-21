"""API 的依賴注入：資料庫 session、請求者身分（cookie session＋CSRF）、權限檢查。

驗證流程（P3-T01，docs/architecture/rbac.md）：
1. 由 HttpOnly cookie `ncu_session` 找出有效 session；沒有或無效時為 ANONYMOUS（零權限）。
2. 已登入且為寫入方法時，`X-CSRF-Token` 標頭必須符合該 session 的 CSRF token，否則 403。
3. 需要權限的端點用 `require_permission()`；已登入卻權限不足時寫入 `auth.forbidden` 稽核。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError, ErrorCode
from app.core.security import (
    ANONYMOUS,
    Permission,
    PermissionDeniedError,
    Principal,
    require,
    token_matches,
)
from app.db.session import get_sessionmaker
from app.models.user import User, UserSession
from app.services.audit import record_audit
from app.services.auth import principal_for, resolve_session

SESSION_COOKIE = "ncu_session"
CSRF_COOKIE = "ncu_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """每個請求一個 session，請求結束自動關閉。"""
    async with get_sessionmaker()() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@dataclass(frozen=True)
class AuthContext:
    """目前請求的驗證結果；未登入時 `user_session` 與 `user` 為 None。"""

    principal: Principal
    user_session: UserSession | None = None
    user: User | None = None


async def get_auth_context(request: Request, session: DbSession) -> AuthContext:
    """由 session cookie 辨識使用者，並對寫入方法檢查 CSRF。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return AuthContext(ANONYMOUS)
    found = await resolve_session(session, token, now=datetime.now(UTC))
    if found is None:
        return AuthContext(ANONYMOUS)
    user_session, user = found
    if request.method not in SAFE_METHODS and not token_matches(
        request.headers.get(CSRF_HEADER), user_session.csrf_hash
    ):
        raise ApiError(ErrorCode.FORBIDDEN, "CSRF 驗證失敗")
    return AuthContext(principal_for(user), user_session, user)


async def get_principal(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> Principal:
    """請求者身分；未登入為 ANONYMOUS。"""
    return context.principal


async def require_login(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """未登入時回 401；用於整個管理端 router 與 auth/me、logout。"""
    if principal.kind == "anonymous":
        raise ApiError(ErrorCode.UNAUTHORIZED, "需要登入")
    return principal


def require_permission(
    permission: Permission,
) -> Callable[..., Awaitable[Principal]]:
    """產生依賴：沒有 `permission` 時拋出 PermissionDeniedError（匿名 401、其他 403）。

    已登入（或服務帳號）卻權限不足時，另外以獨立交易寫入 `auth.forbidden` 稽核。
    """

    async def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        session: DbSession,
    ) -> Principal:
        try:
            require(principal, permission)
        except PermissionDeniedError:
            if principal.kind != "anonymous":
                await record_audit(
                    session,
                    principal,
                    "auth.forbidden",
                    target=request.url.path[:300],
                    metadata={"permission": permission.value},
                )
                await session.commit()
            raise
        return principal

    return dependency
