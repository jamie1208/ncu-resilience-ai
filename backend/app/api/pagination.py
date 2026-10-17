"""cursor 分頁（規格 §10.1）：cursor 為不透明字串，內容是上一頁最後一筆的（時間, id）。"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime

from app.api.errors import ApiError, ErrorCode

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def encode_cursor(when: datetime, row_id: uuid.UUID) -> str:
    """把排序鍵編碼成 URL 安全的 cursor。"""
    payload = json.dumps({"t": when.astimezone(UTC).isoformat(), "i": str(row_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """解碼 cursor；格式不對時拋出 VALIDATION_ERROR。"""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        when = datetime.fromisoformat(data["t"])
        row_id = uuid.UUID(data["i"])
    except (binascii.Error, ValueError, TypeError, KeyError, UnicodeDecodeError):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor 不正確") from None
    if when.tzinfo is None:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor 不正確")
    return when.astimezone(UTC), row_id
