"""登入、登出與目前使用者：/api/v1/auth/*（P3-T01；ADR-0008：單一使用者）。

除 `POST /auth/login` 與 health 外，所有端點都需要登入。
session cookie：HttpOnly、Secure、SameSite=Strict、Path=/api；
CSRF cookie：Secure、SameSite=Strict、Path=/，不是 HttpOnly，讓前端讀出後放進 `X-CSRF-Token`。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthContext,
    DbSession,
    get_auth_context,
    require_login,
)
from app.api.errors import ApiError, ErrorCode
from app.api.responses import envelope
from app.core.config import Settings, get_settings
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_PATH = "/api"


class LoginRequest(BaseModel):
    """登入請求；密碼不會寫入日誌、稽核或錯誤回應。"""

    username: str = Field(min_length=1, max_length=auth_service.MAX_USERNAME_LENGTH)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """驗證帳密；成功時設定 session 與 CSRF cookie，並回傳 CSRF token。"""
    ttl = timedelta(hours=settings.session_ttl_hours)
    try:
        result = await auth_service.login(
            session, body.username, body.password, now=datetime.now(UTC), ttl=ttl
        )
    except auth_service.AccountLockedError:
        await session.commit()
        raise ApiError(ErrorCode.RATE_LIMITED, "登入失敗次數過多，請稍後再試") from None
    except auth_service.InvalidCredentialsError:
        await session.commit()  # 保存失敗次數與稽核
        raise ApiError(ErrorCode.UNAUTHORIZED, "帳號或密碼錯誤") from None
    await session.commit()

    max_age = int(ttl.total_seconds())
    response.set_cookie(
        SESSION_COOKIE,
        result.session_token,
        max_age=max_age,
        path=SESSION_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        result.csrf_token,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=False,
        samesite="strict",
    )
    return envelope(
        {
            "username": result.user.username,
            "csrf_token": result.csrf_token,
            "expires_at": result.expires_at.isoformat(),
        }
    )


@router.post("/logout", dependencies=[Depends(require_login)])
async def logout(
    response: Response,
    session: DbSession,
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict[str, Any]:
    """撤銷目前的 session 並清除 cookie（需要 CSRF token）。"""
    if context.user_session is None or context.user is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "需要登入")
    await auth_service.logout(session, context.user_session, context.user)
    await session.commit()
    response.delete_cookie(
        SESSION_COOKIE, path=SESSION_PATH, secure=True, httponly=True, samesite="strict"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=True, samesite="strict")
    return envelope({"logged_out": True})


@router.get("/me", dependencies=[Depends(require_login)])
async def me(context: Annotated[AuthContext, Depends(get_auth_context)]) -> dict[str, Any]:
    """目前登入的使用者。"""
    if context.user is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "需要登入")
    return envelope({"username": context.user.username, "role": context.user.role})
