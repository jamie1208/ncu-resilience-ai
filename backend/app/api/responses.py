"""統一回應格式 `{data, meta, error}`（規格 §10.1）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import request_id_var


def meta(**extra: Any) -> dict[str, Any]:
    """回應的 meta：request_id、aware UTC 時間戳，加上分頁等額外欄位。"""
    return {
        "request_id": request_id_var.get(),
        "timestamp": datetime.now(UTC).isoformat(),
        **extra,
    }


def envelope(data: Any, **extra_meta: Any) -> dict[str, Any]:
    """成功回應：`{data, meta, error: null}`。"""
    return {"data": data, "meta": meta(**extra_meta), "error": None}


def error_envelope(
    code: str, message: str, details: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """錯誤回應：`{data: null, meta, error: {code, message, details?}}`。"""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"data": None, "meta": meta(), "error": error}
