"""標記紀錄模型（P1-T09，ADR-0009）。

`data/labeling/label.schema.json` 由本模組產生，重新產生的指令：

    cd backend && uv run python -m app.labeling.models > ../data/labeling/label.schema.json

一致性規則（違反時拋出 ValidationError）：
- 與校園無關（`is_campus_relevant = false`）的文章只能是非事件（`category = none`）。
- 非事件不能有查證結果、事件分組、相關事件或高風險。
- 事件（`category ≠ none`）必須有查證結果與事件分組。
- `related_groups` 不能包含自己的 `event_group`。

刻意不提供自由文字欄位（例如備註），避免個資進入標記檔。
"""

from __future__ import annotations

import json
import uuid
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.states import VerificationStatus
from app.ingestion.normalize import to_utc

SCHEMA_VERSION: Literal[1] = 1


class Category(StrEnum):
    """事件類別（S03 event-classifier；使用者 2026-10-18 決定的 9 類）。"""

    FACILITY = "facility"  # 設施與營運：停水停電、施工、網路或系統中斷、設備故障
    WEATHER = "weather"  # 天然災害與氣象：颱風、豪雨、地震、高溫
    SECURITY = "security"  # 治安與財物：失竊、詐騙、可疑人士、騷擾
    TRAFFIC = "traffic"  # 交通與停車：車禍、道路封閉、拖吊、接駁車異動
    HEALTH = "health"  # 健康與食安：食物中毒、傳染病、實驗室化學品
    FIRE = "fire"  # 火災與緊急意外：火警、瓦斯外洩、人員受傷
    ACADEMIC = "academic"  # 教務與課務異動：停課、補課、考試異動
    INFOSEC = "infosec"  # 資訊安全：帳號盜用、釣魚信
    NONE = "none"  # 非事件：一般公告、活動宣傳、學術新聞、徵才、閒聊


class ClaimType(StrEnum):
    """文章的陳述性質（S05 證據強度的依據）。"""

    OFFICIAL = "official"  # 官方公告
    FIRST_HAND = "first_hand"  # 第一手陳述（親身經歷、親眼所見）
    HEARSAY = "hearsay"  # 轉述（聽說、轉貼）
    RUMOR = "rumor"  # 謠言或玩笑


GroupId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]


def _unique[T](values: list[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("不可重複")
    return values


class LabelRecord(BaseModel):
    """一篇文章的人工確認標記；存於 `data/labeled/labels.jsonl`，每行一筆。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    item_id: uuid.UUID = Field(description="source_items.id")
    source_code: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] = Field(
        description="標記時的內容版本（source_item_versions.content_hash）"
    )
    published_at: AwareDatetime | None = Field(description="原文發布時間，用於依時間切分")
    is_campus_relevant: bool
    category: Category
    claim_type: ClaimType
    verification: VerificationStatus | None = Field(
        description="依目前蒐集到的全部證據判斷；非事件為 null"
    )
    event_group: GroupId | None = Field(description="同一事件的文章使用同一個代號；非事件為 null")
    related_groups: Annotated[list[GroupId], AfterValidator(_unique)] = Field(
        default_factory=list, description="相關但不確定是同一件事的事件代號"
    )
    high_risk: bool = False
    mentions_person: bool = Field(default=False, description="是否點名可辨識的個人或店家")
    tags: Annotated[list[Tag], AfterValidator(_unique)] = Field(default_factory=list, max_length=10)
    labeler: Literal["human"] = "human"
    labeled_at: AwareDatetime

    @field_validator("tags")
    @classmethod
    def _no_separators(cls, value: list[str]) -> list[str]:
        if any(sep in tag for tag in value for sep in (",", ";", "\n")):
            raise ValueError("標籤不可包含逗號、分號或換行")
        return value

    @field_validator("published_at", "labeled_at")
    @classmethod
    def _utc(cls, value: AwareDatetime | None) -> AwareDatetime | None:
        return to_utc(value) if value is not None else None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.is_campus_relevant and self.category is not Category.NONE:
            raise ValueError("與校園無關的文章，category 只能是 none")
        if self.category is Category.NONE:
            if self.verification is not None or self.event_group is not None:
                raise ValueError("非事件不能有 verification 或 event_group")
            if self.related_groups or self.high_risk:
                raise ValueError("非事件不能有 related_groups 或 high_risk")
        else:
            if self.verification is None or self.event_group is None:
                raise ValueError("事件必須有 verification 與 event_group")
            if self.event_group in self.related_groups:
                raise ValueError("related_groups 不能包含自己的 event_group")
        return self


def schema_text() -> str:
    """`data/labeling/label.schema.json` 應有的內容。"""
    schema = LabelRecord.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(schema_text(), end="")
