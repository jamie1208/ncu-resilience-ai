"""單一使用者的帳號與登入 session（P3-T01；ADR-0008）。

所有函式只 flush、不 commit，由呼叫端（API 或 scripts/admin/create_admin.py）決定交易邊界。
密碼、session token、CSRF token 都不寫入日誌與稽核紀錄。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    ANONYMOUS,
    Principal,
    Role,
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    verify_password,
)
from app.models.user import User, UserSession
from app.services.audit import record_audit

MAX_USERNAME_LENGTH = 64
# 連續失敗達上限即鎖定，鎖定期間連正確密碼也拒絕
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)


class SingleUserError(Exception):
    """已經有另一位使用者；個人版只允許一位（ADR-0008）。"""


class InvalidCredentialsError(Exception):
    """帳號不存在、已停用或密碼錯誤（對外訊息一律相同）。"""


class AccountLockedError(Exception):
    """連續登入失敗過多，暫時鎖定。"""


@dataclass(frozen=True)
class LoginResult:
    """登入成功：token 只在這裡出現一次，資料庫只存雜湊。"""

    user: User
    session_token: str
    csrf_token: str
    expires_at: datetime


def principal_for(user: User) -> Principal:
    """使用者對應的 Principal（角色固定 admin）。"""
    return Principal(kind="admin", subject=str(user.id), roles=frozenset({Role.ADMIN}))


@cache
def _dummy_hash() -> str:
    # 帳號不存在時也跑一次 argon2 驗證，避免以回應時間探測帳號
    return hash_password(new_token())


async def create_or_reset_admin(session: AsyncSession, username: str, password: str) -> User:
    """建立唯一的使用者，或重設其密碼（同時解鎖並撤銷所有 session）。

    已有另一個帳號名稱的使用者時拋出 SingleUserError；密碼為空時拋出 ValueError。
    """
    username = username.strip()
    if not username or len(username) > MAX_USERNAME_LENGTH:
        raise ValueError(f"帳號長度需為 1–{MAX_USERNAME_LENGTH} 字元")
    # 不限制密碼長度（使用者 2026-10-21 決定）；只拒絕空密碼，否則會建立無法登入的帳號
    if not password:
        raise ValueError("密碼不可為空")

    user = (await session.execute(select(User))).scalar_one_or_none()
    if user is not None and user.username != username:
        raise SingleUserError("已經有一位使用者；個人版只允許一位，請改用該帳號重設密碼")

    if user is None:
        user = User(username=username, password_hash=hash_password(password))
        session.add(user)
        await session.flush()
        action = "user.create"
    else:
        user.password_hash = hash_password(password)
        user.is_active = True
        user.failed_login_count = 0
        user.locked_until = None
        await session.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        await session.flush()
        action = "user.password_reset"
    await record_audit(session, principal_for(user), action, target=f"users/{user.id}")
    return user


async def login(
    session: AsyncSession, username: str, password: str, *, now: datetime, ttl: timedelta
) -> LoginResult:
    """驗證帳密並建立 session。

    失敗時更新失敗次數並寫稽核後拋出 InvalidCredentialsError／AccountLockedError；
    呼叫端仍須 commit，失敗次數與稽核才會保存。
    """
    user = (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()

    if user is not None and user.locked_until is not None and user.locked_until > now:
        await record_audit(session, ANONYMOUS, "auth.login_locked", target=f"users/{user.id}")
        raise AccountLockedError

    if user is None:
        verify_password(_dummy_hash(), password)
        ok = False
    else:
        ok = verify_password(user.password_hash, password) and user.is_active

    if user is None or not ok:
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.failed_login_count = 0
                user.locked_until = now + LOCKOUT
        await record_audit(session, ANONYMOUS, "auth.login_failed", target="auth/login")
        raise InvalidCredentialsError

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    session_token, csrf_token = new_token(), new_token()
    expires_at = now + ttl
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(session_token),
            csrf_hash=hash_token(csrf_token),
            expires_at=expires_at,
        )
    )
    await session.flush()
    await record_audit(session, principal_for(user), "auth.login", target=f"users/{user.id}")
    return LoginResult(user, session_token, csrf_token, expires_at)


async def resolve_session(
    session: AsyncSession, token: str, *, now: datetime
) -> tuple[UserSession, User] | None:
    """由 cookie 的 token 找出有效 session：未撤銷、未過期、使用者仍啟用。"""
    row = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == hash_token(token),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
                User.is_active.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1]


async def logout(session: AsyncSession, user_session: UserSession, user: User) -> None:
    """撤銷目前的 session 並寫稽核。"""
    user_session.revoked_at = func.now()
    await session.flush()
    await record_audit(session, principal_for(user), "auth.logout", target=f"users/{user.id}")
