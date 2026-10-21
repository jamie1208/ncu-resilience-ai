"""角色、權限、身分與驗證用的密碼學工具（規格 §2、§10，見 docs/architecture/rbac.md）。

個人版只有一位使用者（ADR-0008）：沒有訪客（公開閱讀）與匿名投稿者角色，未登入者沒有任何權限。
密碼以 argon2id 雜湊；session 與 CSRF token 只以 SHA-256 雜湊存進資料庫
（P3-T01；資料庫操作見 app/services/auth.py，請求驗證見 app/api/deps.py）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


class Role(StrEnum):
    """角色（規格 §2）。系統服務帳號依最小權限拆成三個。"""

    ADMIN = "admin"
    SERVICE_INGESTION = "service_ingestion"
    SERVICE_AI = "service_ai"
    SERVICE_NOTIFIER = "service_notifier"


class Permission(StrEnum):
    """權限，格式為 `<資源>:<動作>`。"""

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
    # 管理者（使用者本人）不能直接發布：通知只能經 app/alerts/policy.py 的確定性規則
    Role.ADMIN: frozenset(
        {
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

PrincipalKind = Literal["anonymous", "admin", "service"]

# 每種身分可持有的角色；避免例如 anonymous 卻帶 ADMIN 角色，使稽核的行為者失真
KIND_ROLES: Mapping[PrincipalKind, frozenset[Role]] = {
    "anonymous": frozenset(),
    "admin": frozenset({Role.ADMIN}),
    "service": frozenset({Role.SERVICE_INGESTION, Role.SERVICE_AI, Role.SERVICE_NOTIFIER}),
}


@dataclass(frozen=True)
class Principal:
    """請求的身分。`subject` 為使用者帳號 ID 或服務名稱；未登入者沒有角色也沒有權限。"""

    kind: PrincipalKind
    subject: str | None
    roles: frozenset[Role] = field(default_factory=frozenset)
    _permissions: frozenset[Permission] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.kind in ("admin", "service") and not self.subject:
            raise ValueError(f"{self.kind} 身分必須有 subject")
        if self.kind != "anonymous" and not self.roles:
            raise ValueError(f"{self.kind} 身分至少需要一個角色")
        extra = self.roles - KIND_ROLES[self.kind]
        if extra:
            names = ", ".join(sorted(extra))
            raise ValueError(f"{self.kind} 身分不可持有角色：{names}")
        # frozen dataclass：建立時算一次權限聯集
        perms = frozenset().union(*(ROLE_PERMISSIONS[r] for r in self.roles))
        object.__setattr__(self, "_permissions", perms)

    @property
    def actor(self) -> str:
        """寫入稽核紀錄的行為者字串；未登入者一律為 `anonymous`。"""
        if self.kind in ("admin", "service"):
            return f"{self.kind}:{self.subject}"
        return self.kind

    @property
    def permissions(self) -> frozenset[Permission]:
        """此身分所有角色的權限聯集（建立時計算）。"""
        return self._permissions


ANONYMOUS = Principal(kind="anonymous", subject=None, roles=frozenset())


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


# --- 密碼與 token（P3-T01） ---------------------------------------------------------------

# argon2id，參數採 argon2-cffi 預設（RFC 9106 低記憶體建議組）
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """回傳 argon2id 雜湊（含隨機 salt）。"""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """密碼是否符合雜湊；雜湊格式錯誤也回傳 False。"""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """雜湊參數是否已過時（登入成功時順手升級）。"""
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    """產生 session／CSRF 用的隨機 token（32 bytes，URL-safe）。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """token 的 SHA-256 hex；資料庫只存這個值，外洩也無法還原 cookie。"""
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str | None, token_hash: str) -> bool:
    """`token` 的雜湊是否等於 `token_hash`（常數時間比較）。"""
    if not token:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)
