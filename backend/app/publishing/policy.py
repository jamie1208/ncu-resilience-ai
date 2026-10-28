"""公開規則引擎（P5-T10，ADR-0015）：決定一個事件在公開網站上能公開什麼。

純函式，不呼叫模型、不讀資料庫；輸入由公開 API（P5-T11）從事件、證據與最新的小報告組出。
可調整的門檻在 `config/publish-policy.yaml`；下列硬性邊界寫在這裡，設定檔無法關閉：

1. 事件內容（標題、摘要、公開版小報告、來源）只在查證為 verified，且由官方來源證實或使用者在
   後台查證為 true 時公開；只有兩份第一手來源支持（P2-T10）不算。其他事件只公開 P3-T07 的
   中性「一般安全提醒」（只寫類別、地點、日期與防護建議），風險分級須達門檻；false 不公開。
2. 疑似指控（P3-T07 的指控規則，比對所有會公開的文字）永不自動公開事件內容：只公開不寫地點的
   中性提醒，並標記 `needs_review` 留給使用者在後台判斷。
3. S07 的原因一律不公開（ADR-0015：公開版小報告只放影響與建議）；依據推測原因（hypothesized）
   的 S08 措施，以及由這類措施建立的改善措施（P4-T05）也不公開，避免措施文字間接把推測寫成事實。
4. 來源只列官方來源（`is_official_source`）的名稱與連結；Facebook、匿名投稿等其他來源不列出，
   只透過 S02 的 AI 摘要呈現，原文與連結永不公開。
5. 所有公開文字一律經 `mask_pii()`。

改善進度（P5-T11）只隨事件內容公開，只放措施名稱、狀態、預計完成日與更新時間；負責人與佐證
不公開。措施名稱同樣納入疑似指控比對。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from string import Template as TextTemplate
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.alerts.pii import mask_pii
from app.alerts.policy import (
    TEMPLATE_DIR,
    UNKNOWN_DATE,
    UNKNOWN_LOCATION,
    NotificationPolicy,
    Template,
)
from app.alerts.policy import load_policy as load_notification_policy
from app.domain.states import VerificationStatus
from app.verification.rules import is_official_source

DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "config" / "publish-policy.yaml"
RISK_LEVELS = ("low", "medium", "high", "critical")
PUBLIC_IMPACT_FIELDS = ("dimension", "score", "uncertainty", "reason")
PUBLIC_MEASURE_FIELDS = ("action", "audience", "horizon", "priority", "kind", "responsible_unit")


class PublicLevel(StrEnum):
    """公開程度；值會存入 publications，已上線的值不可改名。"""

    CONTENT = "content"  # 事件內容與公開版小報告
    SAFETY_NOTICE = "safety_notice"  # 只有中性安全提醒
    NONE = "none"  # 不公開


class ConfirmedBy(StrEnum):
    """verified 的依據。"""

    OFFICIAL = "official"  # 官方來源支持（P2-T10 規則 1）
    USER = "user"  # 使用者在後台查證為 true
    FIRST_HAND = "first_hand"  # 兩份以上第一手來源支持（P2-T10 規則 2），不足以公開內容


class PublishPolicy(BaseModel):
    """`config/publish-policy.yaml` 的內容；只有門檻，不接受其他欄位。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=32)
    safety_notice_levels: list[str]
    excluded_event_types: list[str]

    @field_validator("safety_notice_levels")
    @classmethod
    def _known_levels(cls, value: list[str]) -> list[str]:
        unknown = set(value) - set(RISK_LEVELS)
        if unknown:
            raise ValueError(f"safety_notice_levels 有未知分級：{'、'.join(sorted(unknown))}")
        return value


@dataclass(frozen=True)
class PublicSource:
    """事件的一份證據來源。"""

    source_code: str
    source_name: str
    url: str | None


@dataclass(frozen=True)
class ReportInput:
    """最新一份有效的事件小報告（`event_reports` 的 impacts、causes、measures）。"""

    impacts: Sequence[Mapping[str, Any]] = ()
    causes: Sequence[Mapping[str, Any]] = ()
    measures: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class ActionInput:
    """一項改善措施（P4-T05 `response_actions`）；只含可能公開的欄位。"""

    title: str
    state: str
    due_date: date | None
    """台北時間的日期。"""
    updated_at: datetime
    cause_basis: str | None = None
    """由 S08 措施建立時，該措施依據的原因性質；手動建立為 None。"""


@dataclass(frozen=True)
class PublicationInput:
    """判斷一個事件能公開什麼所需的資料。"""

    event_id: uuid.UUID
    event_type: str
    title: str
    summary: str | None
    risk_level: str | None
    """最新風險評估的分級；尚未評估為 None。"""
    verification: VerificationStatus
    confirmed_by: ConfirmedBy | None
    """verification 為 verified 時的依據；其他狀態不採用。"""
    location_name: str | None
    occurrence_date: date | None
    """台北時間的日期。"""
    sources: Sequence[PublicSource] = ()
    report: ReportInput | None = None
    actions: Sequence[ActionInput] = ()


@dataclass(frozen=True)
class PublicationDecision:
    """判斷結果；`content` 是可直接公開、已遮蔽 PII 的內容，`reasons` 記錄觸發的規則。"""

    event_id: uuid.UUID
    level: PublicLevel
    content: dict[str, Any] | None
    reasons: tuple[str, ...]
    needs_review: bool
    policy_version: str


def load_policy(path: Path = DEFAULT_POLICY) -> PublishPolicy:
    """讀取並驗證公開規則設定檔。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return PublishPolicy.model_validate(raw)


@cache
def _default_texts() -> NotificationPolicy:
    return load_notification_policy()


def evaluate(
    item: PublicationInput,
    policy: PublishPolicy,
    texts: NotificationPolicy | None = None,
) -> PublicationDecision:
    """依規則判斷事件能公開的程度，並產生已遮蔽 PII 的公開內容。

    `texts` 提供類別名稱、防護建議與指控規則，預設沿用 `config/notification-policy.yaml`。
    """
    texts = texts or _default_texts()
    if item.event_type not in texts.event_types and item.event_type != "none":
        raise ValueError(f"未知的事件類別：{item.event_type}")

    reasons: list[str] = []
    measures = _public_measures(item.report)
    actions = [a for a in item.actions if a.cause_basis != "hypothesized"]
    public_texts = _public_texts(item, measures) + [a.title for a in actions]
    allegation = any(texts.allegation.matches(t) for t in public_texts)
    if allegation:
        reasons.append("possible_allegation")

    def decide(level: PublicLevel, content: dict[str, Any] | None) -> PublicationDecision:
        return PublicationDecision(
            item.event_id, level, content, tuple(reasons), allegation, policy.policy_version
        )

    if item.event_type == "none" or item.event_type in policy.excluded_event_types:
        reasons.append("excluded_event_type")
        return decide(PublicLevel.NONE, None)
    if item.verification is VerificationStatus.FALSE:
        reasons.append("verification_false")
        return decide(PublicLevel.NONE, None)

    confirmed = item.verification is VerificationStatus.VERIFIED and item.confirmed_by in (
        ConfirmedBy.OFFICIAL,
        ConfirmedBy.USER,
    )
    if confirmed and not allegation:
        return decide(PublicLevel.CONTENT, _content(item, texts, measures, actions))
    if not confirmed:
        reasons.append("not_confirmed")

    if item.risk_level is None:
        reasons.append("no_risk_assessment")
        return decide(PublicLevel.NONE, None)
    if item.risk_level not in policy.safety_notice_levels:
        reasons.append("below_threshold")
        return decide(PublicLevel.NONE, None)
    return decide(PublicLevel.SAFETY_NOTICE, _notice(item, texts, show_location=not allegation))


def _public_measures(report: ReportInput | None) -> list[Mapping[str, Any]]:
    """依據推測原因的措施不公開。"""
    if report is None:
        return []
    return [m for m in report.measures if m.get("cause_basis") != "hypothesized"]


def _public_texts(item: PublicationInput, measures: Iterable[Mapping[str, Any]]) -> list[str]:
    """所有可能公開的文字，供指控規則比對。"""
    found = [item.title, item.summary or ""]
    if item.report is not None:
        found += [str(i.get("reason") or "") for i in item.report.impacts]
    for m in measures:
        found += [str(m.get("action") or ""), str(m.get("responsible_unit") or "")]
    return found


def _mask(value: Any) -> Any:
    return mask_pii(value) if isinstance(value, str) else value


def _content(
    item: PublicationInput,
    texts: NotificationPolicy,
    measures: Sequence[Mapping[str, Any]],
    actions: Sequence[ActionInput],
) -> dict[str, Any]:
    report = None
    if item.report is not None:
        report = {
            "impacts": [
                {k: _mask(i.get(k)) for k in PUBLIC_IMPACT_FIELDS} for i in item.report.impacts
            ],
            "measures": [{k: _mask(m.get(k)) for k in PUBLIC_MEASURE_FIELDS} for m in measures],
        }
    sources: list[dict[str, Any]] = []
    for s in item.sources:
        entry = {"name": mask_pii(s.source_name), "url": s.url}
        if is_official_source(s.source_code) and entry not in sources:
            sources.append(entry)
    return {
        "kind": PublicLevel.CONTENT.value,
        "event_type": item.event_type,
        "label": texts.event_types[item.event_type].label,
        "title": mask_pii(item.title),
        "summary": mask_pii(item.summary) if item.summary else None,
        "location": mask_pii(item.location_name) if item.location_name else None,
        "occurrence_date": item.occurrence_date.isoformat() if item.occurrence_date else None,
        "risk_level": item.risk_level,
        "report": report,
        "sources": sources,
        "progress": [
            {
                "title": mask_pii(a.title),
                "state": a.state,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "updated_at": a.updated_at.isoformat(),
            }
            for a in actions
        ],
    }


def _notice(
    item: PublicationInput, texts: NotificationPolicy, *, show_location: bool
) -> dict[str, Any]:
    """P3-T07 的中性「一般安全提醒」模板：只寫類別、地點、日期與防護建議。"""
    text = texts.event_types[item.event_type]
    location = item.location_name if show_location and item.location_name else UNKNOWN_LOCATION
    values = {
        "label": text.label,
        "advice": text.advice,
        "location": location,
        "date": item.occurrence_date.isoformat() if item.occurrence_date else UNKNOWN_DATE,
    }
    raw = (TEMPLATE_DIR / f"{Template.GENERAL_SAFETY.value}.txt").read_text(encoding="utf-8")
    return {
        "kind": PublicLevel.SAFETY_NOTICE.value,
        "event_type": item.event_type,
        "label": text.label,
        "notice": mask_pii(TextTemplate(raw).substitute(values).strip()),
    }
