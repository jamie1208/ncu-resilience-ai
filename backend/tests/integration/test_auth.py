"""P3-T01：單一使用者驗證（ADR-0008；docs/architecture/rbac.md）。

argon2 密碼、HttpOnly Secure Cookie session、CSRF；除 health 與登入外沒有任何免登入的端點。
Secure cookie 只會經 HTTPS 送出，所以測試用 https://test。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.main import create_app
from app.models import AuditLog, User, UserSession
from app.services.auth import (
    MAX_FAILED_LOGINS,
    SingleUserError,
    create_or_reset_admin,
)

pytestmark = pytest.mark.anyio

USERNAME = "jamie"
PASSWORD = "a-long-test-passphrase"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
RUNS = "/api/v1/admin/ingestion-runs"
CSRF_HEADER = "X-CSRF-Token"

# 唯一允許免登入的端點（方法, 路徑）
PUBLIC_ROUTES = {("GET", "/api/v1/health"), ("POST", LOGIN)}


@pytest.fixture()
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


@pytest.fixture()
async def admin_user(session: AsyncSession) -> User:
    user = await create_or_reset_admin(session, USERNAME, PASSWORD)
    await session.commit()
    return user


async def login(client: httpx2.AsyncClient) -> str:
    """登入並回傳 CSRF token。"""
    res = await client.post(LOGIN, json={"username": USERNAME, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return str(res.json()["data"]["csrf_token"])


def set_cookie_headers(res: httpx2.Response) -> dict[str, str]:
    """回應中 Set-Cookie 的「名稱 → 完整標頭」（小寫，方便比對屬性）。"""
    out: dict[str, str] = {}
    for raw in res.headers.get_list("set-cookie"):
        name = raw.split("=", 1)[0]
        out[name] = raw.lower()
    return out


# --- 未登入 -----------------------------------------------------------------------------


async def test_health_is_public(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/v1/health")
    assert res.status_code == 200


async def test_every_other_endpoint_requires_login(client: httpx2.AsyncClient) -> None:
    # openapi() 即使未對外提供 /openapi.json 也能列出所有已註冊的路由
    paths = create_app().openapi()["paths"]
    checked = 0
    for template, operations in paths.items():
        path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), template)
        for method in (m.upper() for m in operations):
            if (method, template) in PUBLIC_ROUTES:
                continue
            res = await client.request(method, path, json={})
            assert res.status_code == 401, f"{method} {template} 未登入卻回 {res.status_code}"
            assert res.json()["error"]["code"] == "UNAUTHORIZED"
            checked += 1
    assert checked >= 5  # 管理 API 與 auth/me、logout 都有被檢查到


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_api_docs_are_not_exposed(client: httpx2.AsyncClient, path: str) -> None:
    res = await client.get(path)
    assert res.status_code == 404


async def test_random_or_bearer_credentials_are_rejected(
    client: httpx2.AsyncClient, admin_user: User
) -> None:
    client.cookies.set("ncu_session", "forged-token", domain="test")
    res = await client.get(RUNS, headers={"Authorization": "Bearer anything"})
    assert res.status_code == 401


# --- 登入 -------------------------------------------------------------------------------


async def test_login_sets_httponly_secure_session_cookie(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    res = await client.post(LOGIN, json={"username": USERNAME, "password": PASSWORD})
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    assert body["data"]["username"] == USERNAME
    csrf = body["data"]["csrf_token"]

    cookies = set_cookie_headers(res)
    sess = cookies["ncu_session"]
    assert "httponly" in sess
    assert "secure" in sess
    assert "samesite=strict" in sess
    # CSRF cookie 給前端讀取，所以不是 HttpOnly，但仍限 HTTPS 與同站
    csrf_cookie = cookies["ncu_csrf"]
    assert "httponly" not in csrf_cookie
    assert "secure" in csrf_cookie
    assert "samesite=strict" in csrf_cookie
    assert client.cookies.get("ncu_csrf") == csrf

    # 資料庫只存 token 的雜湊
    token = client.cookies.get("ncu_session")
    assert token
    row = (await session.execute(select(UserSession))).scalar_one()
    assert row.token_hash != token
    assert token not in row.token_hash
    assert row.csrf_hash != csrf

    res = await client.get(RUNS)
    assert res.status_code == 200
    me = await client.get(ME)
    assert me.status_code == 200
    assert me.json()["data"]["username"] == USERNAME


@pytest.mark.parametrize(
    ("username", "password"),
    [(USERNAME, "wrong-password-123"), ("nobody", PASSWORD)],
)
async def test_bad_credentials_get_same_unauthorized_response(
    client: httpx2.AsyncClient, admin_user: User, username: str, password: str
) -> None:
    res = await client.post(LOGIN, json={"username": username, "password": password})
    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["message"] == "帳號或密碼錯誤"
    assert password not in res.text
    assert "ncu_session" not in set_cookie_headers(res)


async def test_inactive_user_cannot_login(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    await session.execute(update(User).values(is_active=False))
    await session.commit()
    res = await client.post(LOGIN, json={"username": USERNAME, "password": PASSWORD})
    assert res.status_code == 401


async def test_repeated_failures_lock_the_account(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    for _ in range(MAX_FAILED_LOGINS):
        res = await client.post(LOGIN, json={"username": USERNAME, "password": "wrong-pass-xx"})
        assert res.status_code == 401
    # 鎖定期間連正確密碼也不行
    res = await client.post(LOGIN, json={"username": USERNAME, "password": PASSWORD})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"

    # 鎖定到期後恢復，成功登入會重設失敗次數
    await session.execute(
        update(User).values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    await login(client)
    user = (await session.execute(select(User))).scalar_one()
    await session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


# --- session 生命週期 -------------------------------------------------------------------


async def test_logout_revokes_session(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    csrf = await login(client)
    token = client.cookies.get("ncu_session")
    res = await client.post(LOGOUT, headers={CSRF_HEADER: csrf})
    assert res.status_code == 200
    assert 'ncu_session=""' in res.headers.get("set-cookie", "") or "max-age=0" in (
        set_cookie_headers(res).get("ncu_session", "")
    )

    # 即使用戶端保留舊 cookie，伺服器端已撤銷
    assert token
    client.cookies.set("ncu_session", token, domain="test")
    res = await client.get(RUNS)
    assert res.status_code == 401
    row = (await session.execute(select(UserSession))).scalar_one()
    await session.refresh(row)
    assert row.revoked_at is not None


async def test_expired_session_is_rejected(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    await login(client)
    await session.execute(
        update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    res = await client.get(RUNS)
    assert res.status_code == 401


async def test_deactivated_user_loses_existing_session(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    await login(client)
    await session.execute(update(User).values(is_active=False))
    await session.commit()
    res = await client.get(RUNS)
    assert res.status_code == 401


# --- CSRF -------------------------------------------------------------------------------


async def test_unsafe_request_without_csrf_is_rejected(
    client: httpx2.AsyncClient, admin_user: User
) -> None:
    await login(client)
    path = f"/api/v1/admin/events/{uuid.uuid4()}/analyze"
    res = await client.post(path)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"

    res = await client.post(path, headers={CSRF_HEADER: "wrong-token"})
    assert res.status_code == 403

    res = await client.post(LOGOUT)
    assert res.status_code == 403


async def test_unsafe_request_with_csrf_passes_auth(
    client: httpx2.AsyncClient, admin_user: User
) -> None:
    csrf = await login(client)
    res = await client.post(
        f"/api/v1/admin/events/{uuid.uuid4()}/analyze", headers={CSRF_HEADER: csrf}
    )
    # 通過驗證與 CSRF，才會因為事件不存在而 404
    assert res.status_code == 404


async def test_csrf_token_is_bound_to_session(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    old_csrf = await login(client)
    await login(client)  # 第二個 session，CSRF token 不同
    res = await client.post(
        f"/api/v1/admin/events/{uuid.uuid4()}/analyze", headers={CSRF_HEADER: old_csrf}
    )
    assert res.status_code == 403


# --- 稽核 -------------------------------------------------------------------------------


async def test_login_logout_and_failures_are_audited_without_secrets(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    await client.post(LOGIN, json={"username": USERNAME, "password": "wrong-pass-xx"})
    csrf = await login(client)
    await client.post(LOGOUT, headers={CSRF_HEADER: csrf})

    stmt = select(AuditLog).where(AuditLog.action.like("auth.%")).order_by(AuditLog.created_at)
    logs = (await session.execute(stmt)).scalars().all()
    actions = [log.action for log in logs]
    assert actions == ["auth.login_failed", "auth.login", "auth.logout"]
    assert logs[0].actor == "anonymous"
    assert logs[1].actor == f"admin:{admin_user.id}"
    for log in logs:
        dumped = str(log.meta)
        assert PASSWORD not in dumped
        assert "wrong-pass-xx" not in dumped
        assert log.request_id


async def test_forbidden_request_by_authenticated_principal_is_audited(
    session: AsyncSession,
) -> None:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    notifier = Principal(
        kind="service", subject="notifier", roles=frozenset({Role.SERVICE_NOTIFIER})
    )
    app.dependency_overrides[get_db_session] = test_session
    app.dependency_overrides[get_principal] = lambda: notifier
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://test") as c:
        res = await c.get(RUNS)
    assert res.status_code == 403

    log = (await session.execute(select(AuditLog))).scalar_one()
    assert log.action == "auth.forbidden"
    assert log.actor == "service:notifier"
    assert log.meta["permission"] == "ingestion_runs:read"


# --- 單一使用者 -------------------------------------------------------------------------


async def test_only_one_user_can_exist(session: AsyncSession, admin_user: User) -> None:
    with pytest.raises(SingleUserError):
        await create_or_reset_admin(session, "someone-else", PASSWORD)


async def test_reset_password_revokes_sessions(
    client: httpx2.AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    await login(client)
    await create_or_reset_admin(session, USERNAME, "a-brand-new-passphrase")
    await session.commit()
    res = await client.get(RUNS)
    assert res.status_code == 401
    res = await client.post(LOGIN, json={"username": USERNAME, "password": PASSWORD})
    assert res.status_code == 401
    res = await client.post(
        LOGIN, json={"username": USERNAME, "password": "a-brand-new-passphrase"}
    )
    assert res.status_code == 200


async def test_password_has_no_length_limit_but_cannot_be_empty(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    with pytest.raises(ValueError):
        await create_or_reset_admin(session, USERNAME, "")
    await create_or_reset_admin(session, USERNAME, "a")
    await session.commit()
    res = await client.post(LOGIN, json={"username": USERNAME, "password": "a"})
    assert res.status_code == 200
