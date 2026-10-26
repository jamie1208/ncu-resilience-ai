"""個人通知規則（P3-T07）：決定哪些事件要通知使用者本人，以及通知怎麼寫。

個人版的通知只送給使用者本人（ADR-0008），規則依 `config/notification-policy.yaml`，
完全確定性，不呼叫模型：
- 只有 `notify_levels` 中的風險分級才通知，`excluded_event_types` 的類別不通知；
- verified，或有官方來源且未判定 false → 事實模板；unverified、disputed → 一般安全提醒
  （只寫類別、地點、日期與防護建議，不帶事件標題與來源原文）；false → 不通知；
- 標題疑似指控（設定檔的對象＋指控關鍵字）→ 一律一般安全提醒且不寫地點，
  標記 `possible_allegation` 給管理端人工判斷（規格 §6.4）；
- 通知內容一律經 `mask_pii()` 遮蔽。

狀態轉移、冪等與遞送由 P3-T08 的 `app/alerts/service.py` 負責；本模組只做判斷與產生內容。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from string import Template as TextTemplate
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.alerts.pii import mask_pii
from app.domain.states import VerificationStatus

DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "config" / "notification-policy.yaml"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
EVENT_TYPES = (
    "facility",
    "weather",
    "security",
    "traffic",
    "health",
    "fire",
    "academic",
    "infosec",
)
"""S01 event-classifier 的事件類別（none 表示非事件，不在此列）。"""
UNKNOWN_LOCATION = "未明"
UNKNOWN_DATE = "未明"


class Template(StrEnum):
    """通知模板；值即 `templates/<值>.txt`。"""

    FACTUAL = "factual"
    GENERAL_SAFETY = "general_safety"


class EventTypeText(BaseModel):
    """一個事件類別的中文名稱與防護建議。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=20)
    advice: str = Field(min_length=1, max_length=200)


class AllegationRule(BaseModel):
    """標題同時出現任一對象與任一指控關鍵字時，視為疑似指控。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subjects: list[str] = Field(min_length=1)
    accusations: list[str] = Field(min_length=1)

    def matches(self, text: str) -> bool:
        """`text` 是否疑似指控。"""
        return any(s in text for s in self.subjects) and any(a in text for a in self.accusations)


class NotificationPolicy(BaseModel):
    """`config/notification-policy.yaml` 的內容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=32)
    notify_levels: list[str] = Field(min_length=1)
    excluded_event_types: list[str]
    event_types: dict[str, EventTypeText]
    allegation: AllegationRule

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if set(self.event_types) != set(EVENT_TYPES):
            raise ValueError(f"event_types 必須剛好包含：{'、'.join(EVENT_TYPES)}")
        unknown = set(self.excluded_event_types) - {*EVENT_TYPES, "none"}
        if unknown:
            raise ValueError(f"excluded_event_types 有未知類別：{'、'.join(sorted(unknown))}")
        return self


@dataclass(frozen=True)
class NotificationInput:
    """判斷一個事件是否通知所需的資料（由 P3-T08 從資料庫組出）。"""

    event_id: uuid.UUID
    event_type: str
    title: str
    risk_level: str | None
    """最新風險評估的分級；尚未評估為 None。"""
    verification: VerificationStatus
    location_name: str | None
    occurrence_date: date | None
    """台北時間的日期。"""
    has_official_source: bool
    """是否有 S05 判定為官方（claim_type = official）且支持事件的證據。"""


@dataclass(frozen=True)
class NotificationDecision:
    """判斷結果；`reasons` 記錄觸發的規則，供稽核與管理端顯示（不含內容）。"""

    event_id: uuid.UUID
    notify: bool
    template: Template | None
    body: str | None
    reasons: tuple[str, ...]
    policy_version: str


def load_policy(path: Path = DEFAULT_POLICY) -> NotificationPolicy:
    """讀取並驗證通知規則設定檔。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return NotificationPolicy.model_validate(raw)


def evaluate(item: NotificationInput, policy: NotificationPolicy) -> NotificationDecision:
    """依規則判斷是否通知使用者本人，並產生已遮蔽 PII 的通知內容。"""
    if item.event_type not in policy.event_types and item.event_type != "none":
        raise ValueError(f"未知的事件類別：{item.event_type}")

    reasons: list[str] = []
    allegation = policy.allegation.matches(item.title)
    if allegation:
        reasons.append("possible_allegation")

    # 查證為 false 時，不論在哪一關略過都帶出 verification_false：澄清後風險也可能降低，
    # P3-T08 需要它來撤回（而不是過期）已發出的通知（附錄 A06，P3-T10）
    false = item.verification is VerificationStatus.FALSE

    def skip(reason: str) -> NotificationDecision:
        extra = ("verification_false",) if false and reason != "verification_false" else ()
        return NotificationDecision(
            item.event_id, False, None, None, (*reasons, reason, *extra), policy.policy_version
        )

    if item.event_type in policy.excluded_event_types or item.event_type == "none":
        return skip("excluded_event_type")
    if item.risk_level is None:
        return skip("no_risk_assessment")
    if item.risk_level not in policy.notify_levels:
        return skip("below_threshold")
    if false:
        return skip("verification_false")

    official = item.has_official_source and item.verification is not VerificationStatus.VERIFIED
    if official:
        reasons.append("official_source")
    factual = (item.verification is VerificationStatus.VERIFIED or official) and not allegation
    template = Template.FACTUAL if factual else Template.GENERAL_SAFETY
    body = _render(item, policy, template, show_location=not allegation)
    return NotificationDecision(
        item.event_id, True, template, body, tuple(reasons), policy.policy_version
    )


def _render(
    item: NotificationInput,
    policy: NotificationPolicy,
    template: Template,
    *,
    show_location: bool,
) -> str:
    text = policy.event_types[item.event_type]
    location = item.location_name if show_location and item.location_name else UNKNOWN_LOCATION
    values = {
        "label": text.label,
        "advice": text.advice,
        "location": location,
        "date": item.occurrence_date.isoformat() if item.occurrence_date else UNKNOWN_DATE,
        "title": item.title,
        "basis": "已核實" if item.verification is VerificationStatus.VERIFIED else "官方來源",
    }
    raw = (TEMPLATE_DIR / f"{template.value}.txt").read_text(encoding="utf-8")
    return mask_pii(TextTemplate(raw).substitute(values).strip())
