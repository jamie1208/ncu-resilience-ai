"""擷取層的資料型別：來源設定與 Adapter 產出的原始項目。

RawItem 的內容一律視為不可信（AGENTS.md）：之後送進模型前必須以 <untrusted_source> 包裹。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, field_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AccessPolicy(StrEnum):
    """來源授權狀態（對應 docs/governance/source-inventory.md 的核准狀態）。"""

    APPROVED = "approved"  # 核准
    PENDING = "pending"  # 待決定
    DEFERRED = "deferred"  # 暫緩
    REJECTED = "rejected"  # 不核准


@dataclass(frozen=True)
class SourceConfig:
    """建立 Adapter 所需的來源設定。`options` 為 Adapter 自訂參數（如 feed URL），唯讀。"""

    code: str
    type: str
    access_policy: AccessPolicy
    enabled: bool
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 資料庫讀出的是一般字串：轉成列舉，無效值在此拋出 ValueError
        object.__setattr__(self, "access_policy", AccessPolicy(self.access_policy))
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


class RawItem(BaseModel):
    """Adapter 擷取到的一篇原始文章；建立後不可修改。時間一律轉為 aware UTC。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_code: NonEmpty
    external_id: str | None = None
    url: str
    title: str | None = None
    content: str
    content_type: Literal["text/plain", "text/html"] = "text/plain"
    published_at: AwareDatetime | None = None
    fetched_at: AwareDatetime

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("url 必須是 http(s) 絕對網址")
        return value

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content 不可為空白")
        return value

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None
