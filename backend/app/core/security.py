"""角色、權限與身分（介面先行；規格 §2、§10，見 docs/architecture/rbac.md）。

本模組只定義「誰能做什麼」。密碼、session、CSRF 等驗證實作於 P3-T01，
透過 `Authenticator` 介面產生 `Principal`。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol


class Role(StrEnum):
    """角色（規格 §2）。系統服務帳號依最小權限拆成三個。"""

    VISITOR = "visitor"
    REPORTER = "reporter"
    ADMIN = "admin"
    SERVICE_INGESTION = "service_ingestion"
    SERVICE_AI = "service_ai"
    SERVICE_NOTIFIER = "service_notifier"


class Permission(StrEnum):
    """權限，格式為 `<資源>:<動作>`。"""

    PUBLIC_READ = "public:read"
    REPORTS_CREATE = "reports:create"
    REPORTS_FOLLOWUP = "reports:followup"
    REPORTS_READ_INTERNAL = "reports:read_internal"
    EVENTS_READ_ALL = "events:read_all"
    EVENTS_WRITE = "events:write"
    EVENTS_VERIFY = "events:verify"
    EVENTS_ANALYZE = "events:analyze"
    ALERTS_READ_ALL = "alerts:read_all"
    ALERTS_PUBLISH = "alerts:publish"
    ALERTS_RETRACT = "alerts:retract"
    SOURCES_READ = "sources:read"
    SOURCES_MANAGE = "sources:manage"
    INGESTION_WRITE = "ingestion:write"
    INGESTION_RUNS_READ = "ingestion_runs:read"
    AI_RUN = "ai:run"
    SCENARIOS_CREATE = "scenarios:create"
    AUDIT_LOGS_READ = "audit_logs:read"


P = Permission

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = {
    Role.VISITOR: frozenset({P.PUBLIC_READ, P.REPORTS_CREATE}),
    # 匿名投稿者只以收據碼補件，不是帳號；無權建立已核實事件或觸發公開
    Role.REPORTER: frozenset({P.PUBLIC_READ, P.REPORTS_CREATE, P.REPORTS_FOLLOWUP}),
    # 管理者不能直接發布：公開發布只能經 app/alerts/policy.py 的確定性規則
    Role.ADMIN: frozenset(
        {
            P.PUBLIC_READ,
            P.REPORTS_READ_INTERNAL,
            P.EVENTS_READ_ALL,
            P.EVENTS_VERIFY,
            P.EVENTS_ANALYZE,
            P.ALERTS_READ_ALL,
            P.ALERTS_RETRACT,
            P.SOURCES_READ,
            P.SOURCES_MANAGE,
            P.INGESTION_RUNS_READ,
            P.SCENARIOS_CREATE,
            P.AUDIT_LOGS_READ,
        }
    ),
    Role.SERVICE_INGESTION: frozenset({P.SOURCES_READ, P.INGESTION_WRITE}),
    Role.SERVICE_AI: frozenset({P.EVENTS_READ_ALL, P.EVENTS_WRITE, P.AI_RUN}),
    Role.SERVICE_NOTIFIER: frozenset({P.ALERTS_READ_ALL, P.ALERTS_PUBLISH}),
}

# 即使只是讀取也必須寫入稽核紀錄的權限
SENSITIVE_PERMISSIONS: frozenset[Permission] = frozenset(
    {P.AUDIT_LOGS_READ, P.REPORTS_READ_INTERNAL}
)

PrincipalKind = Literal["anonymous", "reporter", "admin", "service"]


@dataclass(frozen=True)
class Principal:
    """請求的身分。`subject` 為管理者帳號 ID 或服務名稱；投稿者的收據碼不得放進 actor。"""

    kind: PrincipalKind
    subject: str | None
    roles: frozenset[Role] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.kind in ("admin", "service") and not self.subject:
            raise ValueError(f"{self.kind} 身分必須有 subject")

    @property
    def actor(self) -> str:
        """寫入稽核紀錄的行為者字串；匿名與投稿者不含任何可識別資訊。"""
        if self.kind in ("admin", "service"):
            return f"{self.kind}:{self.subject}"
        return self.kind

    @property
    def permissions(self) -> frozenset[Permission]:
        """此身分所有角色的權限聯集。"""
        return frozenset().union(*(ROLE_PERMISSIONS[r] for r in self.roles))


ANONYMOUS = Principal(kind="anonymous", subject=None, roles=frozenset({Role.VISITOR}))


class PermissionDeniedError(Exception):
    """權限不足；API 層對應錯誤碼 FORBIDDEN（未登入為 UNAUTHORIZED）。"""

    def __init__(self, actor: str, permission: Permission) -> None:
        self.actor = actor
        self.permission = permission
        super().__init__(f"{actor} 沒有權限 {permission}")


def has_permission(principal: Principal, permission: Permission) -> bool:
    """`principal` 是否擁有 `permission`。"""
    return permission in principal.permissions


def require(principal: Principal, permission: Permission) -> None:
    """沒有權限時拋出 PermissionDeniedError。"""
    if not has_permission(principal, permission):
        raise PermissionDeniedError(principal.actor, permission)


class Authenticator(Protocol):
    """由請求的標頭與 cookie 辨識身分（P3-T01 實作；無有效憑證時回傳 ANONYMOUS）。"""

    async def authenticate(
        self, headers: Mapping[str, str], cookies: Mapping[str, str]
    ) -> Principal:
        """回傳請求者的 Principal。"""
        ...
