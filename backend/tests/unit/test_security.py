"""P0-T07：角色與權限（規格 §2、§10；docs/architecture/rbac.md）。"""

from __future__ import annotations

import pytest

from app.core.security import (
    ANONYMOUS,
    ROLE_PERMISSIONS,
    SENSITIVE_PERMISSIONS,
    Permission,
    PermissionDeniedError,
    Principal,
    Role,
    has_permission,
    require,
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


def test_visitor_can_only_read_public_and_submit_reports() -> None:
    assert ROLE_PERMISSIONS[Role.VISITOR] == {P.PUBLIC_READ, P.REPORTS_CREATE}
    for perm in ADMIN_ONLY | {P.ALERTS_PUBLISH, P.REPORTS_FOLLOWUP}:
        assert not has_permission(ANONYMOUS, perm)


def test_reporter_can_follow_up_but_not_create_verified_events() -> None:
    reporter = Principal(kind="reporter", subject=None, roles=frozenset({Role.REPORTER}))
    assert has_permission(reporter, P.REPORTS_FOLLOWUP)
    assert not has_permission(reporter, P.EVENTS_VERIFY)
    assert not has_permission(reporter, P.EVENTS_WRITE)
    assert not has_permission(reporter, P.ALERTS_PUBLISH)


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


def test_actor_strings_never_expose_reporter_identity() -> None:
    assert ANONYMOUS.actor == "anonymous"
    assert admin("u-42").actor == "admin:u-42"
    assert service(Role.SERVICE_AI, "ai-worker").actor == "service:ai-worker"
    reporter = Principal(kind="reporter", subject="receipt-token", roles=frozenset({Role.REPORTER}))
    assert reporter.actor == "reporter"
    assert "receipt-token" not in reporter.actor


def test_admin_and_service_principals_need_subject() -> None:
    with pytest.raises(ValueError):
        Principal(kind="admin", subject=None, roles=frozenset({Role.ADMIN}))
    with pytest.raises(ValueError):
        Principal(kind="service", subject="", roles=frozenset({Role.SERVICE_AI}))
