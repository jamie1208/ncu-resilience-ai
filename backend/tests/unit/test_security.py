"""P0-T07：角色與權限（規格 §2、§10；docs/architecture/rbac.md）。

P3-T01（ADR-0008，使用者 2026-10-21 同意改寫）：單一使用者，移除訪客（公開閱讀）與匿名投稿者角色。
"""

from __future__ import annotations

import pytest

from app.core.security import (
    ANONYMOUS,
    ROLE_PERMISSIONS,
    SENSITIVE_PERMISSIONS,
    Permission,
    PermissionDeniedError,
    Principal,
    PrincipalKind,
    Role,
    has_permission,
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    require,
    token_matches,
    verify_password,
)

P = Permission

ADMIN_ONLY = {
    P.EVENTS_READ_ALL,
    P.EVENTS_VERIFY,
    P.EVENTS_ANALYZE,
    P.ALERTS_RETRACT,
    P.SOURCES_MANAGE,
    P.INGESTION_RUNS_READ,
    P.SCENARIOS_CREATE,
    P.AUDIT_LOGS_READ,
    P.REPORTS_READ_INTERNAL,
}


def admin(subject: str = "u-1") -> Principal:
    return Principal(kind="admin", subject=subject, roles=frozenset({Role.ADMIN}))


def service(role: Role, name: str) -> Principal:
    return Principal(kind="service", subject=name, roles=frozenset({role}))


def test_every_permission_belongs_to_some_role() -> None:
    granted = set().union(*ROLE_PERMISSIONS.values())
    assert granted == set(Permission)


def test_anonymous_has_no_permissions() -> None:
    # 個人版沒有公開頁面與投稿：未登入者什麼都不能做（ADR-0008）
    assert ANONYMOUS.roles == frozenset()
    assert ANONYMOUS.permissions == frozenset()
    for perm in Permission:
        assert not has_permission(ANONYMOUS, perm)


def test_no_public_or_reporter_roles_remain() -> None:
    assert {r.value for r in Role} == {
        "admin",
        "service_ingestion",
        "service_ai",
        "service_notifier",
    }
    values = {p.value for p in Permission}
    assert not values & {"public:read", "reports:create", "reports:followup"}


def test_admin_has_management_permissions_but_cannot_publish_directly() -> None:
    a = admin()
    for perm in ADMIN_ONLY:
        assert has_permission(a, perm)
    # 公開發布只能經 alerts/policy.py 的確定性規則（由通知服務執行）
    assert not has_permission(a, P.ALERTS_PUBLISH)


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Role.SERVICE_INGESTION, {P.SOURCES_READ, P.INGESTION_WRITE}),
        (Role.SERVICE_AI, {P.EVENTS_READ_ALL, P.EVENTS_WRITE, P.AI_RUN}),
        (Role.SERVICE_NOTIFIER, {P.ALERTS_READ_ALL, P.ALERTS_PUBLISH}),
    ],
)
def test_service_accounts_have_least_privilege(role: Role, allowed: set[Permission]) -> None:
    assert ROLE_PERMISSIONS[role] == allowed
    assert not ROLE_PERMISSIONS[role] & ADMIN_ONLY - allowed


def test_only_notifier_can_publish_alerts() -> None:
    holders = {r for r, perms in ROLE_PERMISSIONS.items() if P.ALERTS_PUBLISH in perms}
    assert holders == {Role.SERVICE_NOTIFIER}


def test_sensitive_permissions_are_admin_only() -> None:
    assert {P.AUDIT_LOGS_READ, P.REPORTS_READ_INTERNAL} <= SENSITIVE_PERMISSIONS
    for perm in SENSITIVE_PERMISSIONS:
        holders = {r for r, perms in ROLE_PERMISSIONS.items() if perm in perms}
        assert holders == {Role.ADMIN}


def test_require_raises_with_details() -> None:
    with pytest.raises(PermissionDeniedError) as info:
        require(ANONYMOUS, P.AUDIT_LOGS_READ)
    assert info.value.permission is P.AUDIT_LOGS_READ
    assert info.value.actor == "anonymous"
    require(admin(), P.AUDIT_LOGS_READ)


def test_actor_strings() -> None:
    assert ANONYMOUS.actor == "anonymous"
    assert admin("u-42").actor == "admin:u-42"
    assert service(Role.SERVICE_AI, "ai-worker").actor == "service:ai-worker"


def test_admin_and_service_principals_need_subject() -> None:
    with pytest.raises(ValueError):
        Principal(kind="admin", subject=None, roles=frozenset({Role.ADMIN}))
    with pytest.raises(ValueError):
        Principal(kind="service", subject="", roles=frozenset({Role.SERVICE_AI}))


# --- P0-T12：身分種類與角色必須一致、權限快取 ------------------------------------------


@pytest.mark.parametrize(
    ("kind", "subject", "roles"),
    [
        ("anonymous", None, {Role.ADMIN}),
        ("anonymous", None, {Role.SERVICE_NOTIFIER}),
        ("admin", "u-1", {Role.SERVICE_AI}),
        ("service", "ai-worker", {Role.ADMIN}),
        ("admin", "u-1", set()),
        ("service", "ai-worker", set()),
    ],
)
def test_kind_and_roles_must_be_consistent(
    kind: PrincipalKind, subject: str | None, roles: set[Role]
) -> None:
    with pytest.raises(ValueError):
        Principal(kind=kind, subject=subject, roles=frozenset(roles))


def test_valid_kind_role_combinations() -> None:
    Principal(kind="anonymous", subject=None, roles=frozenset())
    Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
    Principal(
        kind="service",
        subject="worker",
        roles=frozenset({Role.SERVICE_INGESTION, Role.SERVICE_AI}),
    )


def test_permissions_are_computed_once() -> None:
    a = admin()
    assert a.permissions is a.permissions
    assert a.permissions == ROLE_PERMISSIONS[Role.ADMIN]
    assert "permissions" not in repr(a)


# --- P3-T01：密碼雜湊與 session／CSRF token -------------------------------------------


def test_password_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert "correct horse" not in hashed
    assert verify_password(hashed, "correct horse battery staple")
    assert not verify_password(hashed, "wrong password")
    assert not password_needs_rehash(hashed)


def test_same_password_gets_different_salt() -> None:
    assert hash_password("same-password-123") != hash_password("same-password-123")


def test_verify_password_rejects_malformed_hash() -> None:
    assert not verify_password("not-a-hash", "anything")


def test_tokens_are_random_and_only_hashes_are_stored() -> None:
    a, b = new_token(), new_token()
    assert a != b
    assert len(a) >= 43  # 32 bytes urlsafe base64
    digest = hash_token(a)
    assert len(digest) == 64
    assert a not in digest
    assert hash_token(a) == digest
    assert token_matches(a, digest)
    assert not token_matches(b, digest)
    assert not token_matches(None, digest)
    assert not token_matches("", digest)
