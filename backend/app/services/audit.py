"""稽核紀錄服務：寫入 audit_logs（只能新增，資料庫 trigger 禁止修改與刪除）。

- 與業務寫入在同一個交易中：只 flush，不 commit，交易回滾時稽核紀錄一併回滾。
- request_id：參數 > 目前請求（app.core.logging.request_id_var）> 新產生的 UUID。
- metadata 不得含個資、投稿原文或金鑰。因為寫入後無法刪除，欄位名稱與值都會檢查；
  UUID、aware datetime、Enum 會先轉成 JSON 可接受的值。
- 敏感權限的檢查一律經 `authorize()`，自動留下稽核紀錄。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from itertools import pairwise
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_var
from app.core.security import SENSITIVE_PERMISSIONS, Permission, Principal, require
from app.models import AuditLog

ACTION_PATTERN = re.compile(r"^[a-z][a-z_]*(\.[a-z][a-z_]*)+$")

MAX_METADATA_BYTES = 4096
MAX_STRING_LENGTH = 200

# 欄位名稱拆成小寫分段後（camelCase、底線、連字號、點皆會拆開）的比對規則
_ANY_SEGMENT = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "email",
        "phone",
    }
)
_SEGMENT_PAIRS = frozenset(
    {
        ("api", "key"),
        ("private", "key"),
        ("national", "id"),
        ("session", "id"),
        ("session", "key"),
    }
)
# 只在最後一段時視為原文（report_text、raw_content、body），content_hash、text_length 不受影響
_LAST_SEGMENT = frozenset({"text", "content", "body"})

_SENSITIVE_VALUES = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("手機號碼", re.compile(r"(?<!\d)09\d{2}-?\d{3}-?\d{3}(?!\d)")),
    ("身分證字號", re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?!\d)")),
)


class AuditMetadataError(ValueError):
    """metadata 可能含敏感資料、型別不支援或過大。"""


def _segments(key: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return [s for s in re.split(r"[^a-z0-9]+", spaced.lower()) if s]


def _is_sensitive_key(key: str) -> bool:
    segs = _segments(key)
    if not segs:
        return False
    if _ANY_SEGMENT.intersection(segs) or segs[-1] in _LAST_SEGMENT:
        return True
    return any(pair in _SEGMENT_PAIRS for pair in pairwise(segs))


def _check_string(value: str, path: str) -> str:
    if len(value) > MAX_STRING_LENGTH:
        raise AuditMetadataError(
            f"metadata {path} 超過 {MAX_STRING_LENGTH} 字元，可能是原文；請只記錄識別碼或摘要"
        )
    for label, pattern in _SENSITIVE_VALUES:
        if pattern.search(value):
            raise AuditMetadataError(f"metadata {path} 疑似含{label}，不得寫入稽核紀錄")
    return value


def _normalize(value: Any, path: str) -> Any:
    if isinstance(value, Enum):
        return _normalize(value.value, path)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _check_string(value, path)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise AuditMetadataError(f"metadata {path} 的時間必須帶時區（aware UTC）")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise AuditMetadataError(f"metadata {path} 的欄位名稱必須是字串：{key!r}")
            child_path = f"{path}.{key}" if path else key
            if _is_sensitive_key(key):
                raise AuditMetadataError(
                    f"metadata 欄位 {child_path} 可能含敏感資料，不得寫入稽核紀錄"
                )
            out[key] = _normalize(child, child_path)
        return out
    if isinstance(value, list | tuple):
        return [_normalize(item, f"{path}[{i}]") for i, item in enumerate(value)]
    raise AuditMetadataError(f"metadata {path} 的型別不支援：{type(value).__name__}")


def prepare_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """檢查並正規化 metadata，回傳可直接存入 JSONB 的 dict；不合格時拋出 AuditMetadataError。"""
    normalized: dict[str, Any] = _normalize(dict(metadata or {}), "")
    size = len(json.dumps(normalized, ensure_ascii=False).encode())
    if size > MAX_METADATA_BYTES:
        raise AuditMetadataError(f"metadata 為 {size} bytes，超過上限 {MAX_METADATA_BYTES}")
    return normalized


async def record_audit(
    session: AsyncSession,
    principal: Principal,
    action: str,
    *,
    target: str | None = None,
    request_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AuditLog:
    """新增一筆稽核紀錄並 flush（不 commit）。

    action 格式為 `<資源>.<動作>`，例如 `event.verify`、`alert.retract`。
    """
    if not ACTION_PATTERN.fullmatch(action):
        raise ValueError(f"action 格式應為 <資源>.<動作>（小寫）：{action!r}")
    meta = prepare_metadata(metadata)
    log = AuditLog(
        actor=principal.actor,
        action=action,
        target=target,
        request_id=request_id or request_id_var.get() or str(uuid.uuid4()),
        meta=meta,
    )
    session.add(log)
    await session.flush()
    return log


async def authorize(
    session: AsyncSession,
    principal: Principal,
    permission: Permission,
    *,
    target: str | None = None,
    request_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """檢查權限；敏感權限（SENSITIVE_PERMISSIONS）通過時自動寫入稽核紀錄。

    權限不足時拋出 PermissionDeniedError，不寫入任何紀錄（遭拒的稽核於 P3-T01 加入）。
    """
    require(principal, permission)
    if permission in SENSITIVE_PERMISSIONS:
        await record_audit(
            session,
            principal,
            permission.value.replace(":", "."),
            target=target,
            request_id=request_id,
            metadata=metadata,
        )
