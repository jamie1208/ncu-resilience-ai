"""ORM 基底：固定的約束命名規則、UUID 主鍵與 aware UTC 時間欄位。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有資料表的基底；schema 變更只能透過 Alembic migration。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    """UUID 主鍵：程式端與資料庫端都會產生，原生 SQL 寫入也安全。"""
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def created_at_col(*, index: bool = False) -> Mapped[datetime]:
    """建立時間（timestamptz，資料庫端 now()）。"""
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=index
    )


def updated_at_col() -> Mapped[datetime]:
    """更新時間（timestamptz，ORM 更新時自動刷新）。"""
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
