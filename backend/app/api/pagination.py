"""cursor 分頁（規格 §10.1）：cursor 為不透明字串，內容是上一頁最後一筆的（時間, id）。"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

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


def keyset_after(
    time_col: InstrumentedAttribute[datetime], id_col: InstrumentedAttribute[uuid.UUID], cursor: str
) -> ColumnElement[bool]:
    """「新到舊」排序下，位於 cursor 之後的條件（時間較早，或同時間 id 較小）。"""
    after_time, after_id = decode_cursor(cursor)
    return or_(time_col < after_time, and_(time_col == after_time, id_col < after_id))


def split_page[T](
    rows: Sequence[T], limit: int, key: Callable[[T], tuple[datetime, uuid.UUID]]
) -> tuple[list[T], str | None]:
    """以多查一筆（limit + 1）的結果切出本頁，並產生下一頁的 cursor（沒有下一頁為 None）。"""
    page = list(rows[:limit])
    next_cursor = encode_cursor(*key(page[-1])) if len(rows) > limit else None
    return page, next_cursor
