"""稽核紀錄服務：寫入 audit_logs（只能新增，資料庫 trigger 禁止修改與刪除）。

- 與業務寫入在同一個交易中：本函式只 flush，不 commit，交易回滾時稽核紀錄一併回滾。
- request_id 預設取自目前請求（app.core.logging.request_id_var）。
- metadata 不得含個資、投稿原文或金鑰；可疑欄位名稱一律拒絕。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_var
from app.core.security import Principal
from app.models import AuditLog

ACTION_PATTERN = re.compile(r"^[a-z][a-z_]*(\.[a-z][a-z_]*)+$")

# metadata 欄位名稱（小寫）只要包含以下任一字串就拒絕
FORBIDDEN_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session",
    "email",
    "phone",
    "national_id",
    "text",
    "content",
    "body",
)

MAX_METADATA_BYTES = 4096


class AuditMetadataError(ValueError):
    """metadata 可能含敏感資料或過大。"""


def _check_metadata(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                raise AuditMetadataError(
                    f"metadata 欄位 {path}{key} 可能含敏感資料，不得寫入稽核紀錄"
                )
            _check_metadata(child, f"{path}{key}.")
    elif isinstance(value, list | tuple):
        for item in value:
            _check_metadata(item, path)


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
    meta = dict(metadata or {})
    _check_metadata(meta)
    if len(json.dumps(meta, ensure_ascii=False, default=str).encode()) > MAX_METADATA_BYTES:
        raise AuditMetadataError(f"metadata 超過 {MAX_METADATA_BYTES} bytes")

    log = AuditLog(
        actor=principal.actor,
        action=action,
        target=target,
        request_id=request_id if request_id is not None else request_id_var.get(),
        meta=meta,
    )
    session.add(log)
    await session.flush()
    return log
