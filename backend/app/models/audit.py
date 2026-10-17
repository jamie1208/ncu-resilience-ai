"""稽核紀錄 audit_logs（規格 §9：append-only）。

禁止 UPDATE／DELETE 的資料庫 trigger 於 P0-T07 加入；寫入服務見 app/services/audit.py（P0-T07）。
規格欄位 `timestamp` 對應本表的 `created_at`。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class AuditLog(Base):
    """一筆稽核紀錄。`meta` 不得包含個資、投稿原文或金鑰。"""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100), index=True)
    target: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # Python 屬性名避開 DeclarativeBase 保留的 metadata，資料庫欄位仍為 metadata
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = created_at_col(index=True)
