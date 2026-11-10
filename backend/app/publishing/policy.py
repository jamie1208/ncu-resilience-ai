"""公開規則引擎（P5-T10，ADR-0015；P8-T06 起依 ADR-0018、ADR-0020 放寬）。

純函式，不呼叫模型、不讀資料庫；輸入由公開狀態同步（`sync.py`）從事件、系列分析與改善措施組出。
可調整的設定在 `config/publish-policy.yaml`；下列硬性邊界寫在這裡，設定檔無法關閉：

事件（`evaluate`，快報全文）：
1. 不是 false、不是排除類別的事件都公開 AI 摘要（標題、摘要、地點、日期）；只有官方來源證實或
   使用者在後台查證為 true 時標「已查證」，其他一律標「尚未查證」（`verification`、
   `verification_label`），不得呈現為已核實。只有兩份第一手來源支持（P2-T10）不算已查證。
2. 疑似指控（P3-T07 的指控規則，比對所有會公開的文字）照 AI 摘要公開，標記 `needs_review` 與
   `possible_allegation`；同步時必定在通知中心產生公開提醒並附一鍵下架（`remind`，見 sync.py）。
3. 舊小報告（P8-T13 已停用，同步不再提供）：S07 的原因不公開；依據推測原因的 S08 措施與由它建立的
   改善措施不公開，除非原因經人工確認（P7-T07）。
4. 來源只列官方來源（`is_official_source`）的名稱與連結；Facebook、匿名投稿等其他來源不列出，
   只透過 S02 的 AI 摘要呈現，原文與連結永不公開。
5. 所有公開文字一律經 `mask_pii()`。

系列（`evaluate_series`，系列分析頁）：
6. 只列出目前以事件內容公開中的子事件（呼叫端提供，內容取自事件的公開快照）；沒有公開的子事件時
   不公開。件數、時間軸、前後比較只依這些子事件計算（由程式完成）。
7. 規律與可能原因（S12）一律帶「推測，尚未查證」標籤，人工確認（ADR-0017）的原因改標「已確認」；
   標籤由這裡決定，不採用輸入的標籤。引用的子事件換成公開的事件 ID（`related_ids`），件數依公開的
   子事件重算；
   規律少於 2 起公開子事件支持就不公開。逐字證據、證據 ID、`to_verify` 永不公開（證據可能是
   Facebook 或匿名投稿原文）。
8. 提到特定個人、群體、店家或單位（指控規則的對象關鍵字）的規律與原因比照疑似指控：照樣公開並標
   `needs_review` 與 `mentions_party`；推測內容首次公開或改變時必定產生公開提醒（sync.py）。
9. 改善建議公開，但不寫依據的原因（不含 `cause_id`）；突增只公開程式判斷的週期與件數，不公開模型的
   解釋；前後比較只並列平均與固定說明，不公開變化比例。
10. 所有公開文字一律經 `mask_pii()`，並納入指控比對。

改善進度只放措施名稱、狀態、預計完成日與更新時間；負責人與佐證不公開。由舊小報告依據推測原因的
措施建立、且原因沒有人工確認的改善措施，在系列頁同樣不公開（邊界 3）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.alerts.pii import mask_pii
from app.alerts.policy import NotificationPolicy
from app.alerts.policy import load_policy as load_notification_policy
from app.domain.states import VerificationStatus
from app.series.timeline import Comparison, Timeline
from app.verification.rules import is_official_source

DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "config" / "publish-policy.yaml"
PUBLIC_IMPACT_FIELDS = ("dimension", "score", "uncertainty", "reason")
PUBLIC_MEASURE_FIELDS = ("action", "audience", "horizon", "priority", "kind", "responsible_unit")

VERIFIED_LABEL = "已查證"
UNVERIFIED_LABEL = "尚未查證"
SPECULATIVE_LABEL = "推測，尚未查證"
CONFIRMED_LABEL = "已確認"
FEW_EVENTS_NOTE = "要至少 2 起才會找規律"
MIN_PATTERN_EVENTS = 2

# 規則代碼（存入 publications.reasons 與版本快照，已上線的值不可改名）
POSSIBLE_ALLEGATION = "possible_allegation"
MENTIONS_PARTY = "mentions_party"
SPECULATION = "speculation"
NOT_CONFIRMED = "not_confirmed"


class PublicLevel(StrEnum):
    """公開程度；值會存入 publications，已上線的值不可改名。"""

    CONTENT = "content"  # 事件內容（快報全文）或系列分析頁
    SAFETY_NOTICE = "safety_notice"  # 只有中性安全提醒（P8-T06 起不再產生，保留歷史版本）
    NONE = "none"  # 不公開


class ConfirmedBy(StrEnum):
    """verified 的依據。"""

    OFFICIAL = "official"  # 官方來源支持（P2-T10 規則 1）
    USER = "user"  # 使用者在後台查證為 true
    FIRST_HAND = "first_hand"  # 兩份以上第一手來源支持（P2-T10 規則 2），不算已查證


class PublishPolicy(BaseModel):
    """`config/publish-policy.yaml` 的內容；只有可調整的設定，不接受其他欄位。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=32)
    excluded_event_types: list[str]


@dataclass(frozen=True)
class PublicSource:
    """事件的一份證據來源。"""

    source_code: str
    source_name: str
    url: str | None


@dataclass(frozen=True)
class ReportInput:
    """舊版事件小報告（P8-T13 停用；同步不再提供，保留給舊資料的規則）。"""

    impacts: Sequence[Mapping[str, Any]] = ()
    causes: Sequence[Mapping[str, Any]] = ()
    measures: Sequence[Mapping[str, Any]] = ()
    confirmed_cause_ids: frozenset[str] = frozenset()
    """這份報告中經人工確認的推測原因代號（P7-T07）。"""


@dataclass(frozen=True)
class ActionInput:
    """一項改善措施（P4-T05 `response_actions`）；只含可能公開的欄位。"""

    title: str
    state: str
    due_date: date | None
    """台北時間的日期。"""
    updated_at: datetime
    cause_basis: str | None = None
    """由舊小報告的 S08 措施建立時，該措施依據的原因性質；其他為 None。"""
    cause_confirmed: bool = False
    """依據的推測原因已在最新一份有效小報告中人工確認（P7-T07）。"""


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
    """判斷結果；`content` 是可直接公開、已遮蔽 PII 的內容，`reasons` 記錄觸發的規則。

    `event_id` 是事件或系列的 ID（系列見 `evaluate_series`）。
    """

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


def _mask(value: Any) -> Any:
    return mask_pii(value) if isinstance(value, str) else value


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


# ---------- 事件 ----------


def evaluate(
    item: PublicationInput,
    policy: PublishPolicy,
    texts: NotificationPolicy | None = None,
) -> PublicationDecision:
    """依規則判斷事件能公開的程度，並產生已遮蔽 PII 的公開內容。

    `texts` 提供類別名稱與指控規則，預設沿用 `config/notification-policy.yaml`。
    """
    texts = texts or _default_texts()
    if item.event_type not in texts.event_types and item.event_type != "none":
        raise ValueError(f"未知的事件類別：{item.event_type}")

    reasons: list[str] = []
    measures = _public_measures(item.report)
    actions = [a for a in item.actions if a.cause_basis != "hypothesized" or a.cause_confirmed]
    public_texts = _public_texts(item, measures) + [a.title for a in actions]
    allegation = any(texts.allegation.matches(t) for t in public_texts)
    if allegation:
        reasons.append(POSSIBLE_ALLEGATION)

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
    if not confirmed:
        reasons.append(NOT_CONFIRMED)
    return decide(PublicLevel.CONTENT, _content(item, texts, measures, actions, confirmed))


def _public_measures(report: ReportInput | None) -> list[Mapping[str, Any]]:
    """依據推測原因的措施不公開，除非該原因經人工確認。"""
    if report is None:
        return []
    return [
        m
        for m in report.measures
        if m.get("cause_basis") != "hypothesized"
        or str(m.get("cause_id")) in report.confirmed_cause_ids
    ]


def _public_texts(item: PublicationInput, measures: Iterable[Mapping[str, Any]]) -> list[str]:
    """所有可能公開的文字，供指控規則比對。"""
    found = [item.title, item.summary or "", item.location_name or ""]
    if item.report is not None:
        found += [str(i.get("reason") or "") for i in item.report.impacts]
    for m in measures:
        found += [str(m.get("action") or ""), str(m.get("responsible_unit") or "")]
    return found


def _verification(item: PublicationInput, confirmed: bool) -> str:
    if confirmed:
        return "verified"
    if item.verification is VerificationStatus.DISPUTED:
        return "disputed"
    return "unverified"


def _content(
    item: PublicationInput,
    texts: NotificationPolicy,
    measures: Sequence[Mapping[str, Any]],
    actions: Sequence[ActionInput],
    confirmed: bool,
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
        "occurrence_date": _iso(item.occurrence_date),
        "risk_level": item.risk_level,
        "verification": _verification(item, confirmed),
        "verification_label": VERIFIED_LABEL if confirmed else UNVERIFIED_LABEL,
        "report": report,
        "sources": sources,
        "progress": [_action(a) for a in actions],
    }


def _action(a: ActionInput) -> dict[str, Any]:
    return {
        "title": mask_pii(a.title),
        "state": a.state,
        "due_date": _iso(a.due_date),
        "updated_at": a.updated_at.isoformat(),
    }


# ---------- 系列 ----------


@dataclass(frozen=True)
class SeriesMemberInput:
    """一起公開中的子事件；文字取自事件目前公開的快照（已經過事件的規則）。"""

    event_id: uuid.UUID
    """只用來對應分析引用的事件，不會公開。"""
    publication_id: uuid.UUID
    title: str
    occurrence_date: date | None
    location: str | None
    verified: bool


@dataclass(frozen=True)
class BurstInput:
    """一個突增週。"""

    period_start: date
    count: int
    expected: float


@dataclass(frozen=True)
class SeriesAnalysisInput:
    """最新一次成功的 S12 分析（`series_analyses`，已通過引用驗證）。"""

    summary: str | None = None
    location_groups: Sequence[Mapping[str, Any]] = ()
    patterns: Sequence[Mapping[str, Any]] = ()
    causes: Sequence[Mapping[str, Any]] = ()
    measures: Sequence[Mapping[str, Any]] = ()
    confirmed_cause_ids: frozenset[str] = frozenset()
    """經人工確認的原因代號（`series.cause_decide`）。"""


@dataclass(frozen=True)
class SeriesPublicationInput:
    """判斷一個系列分析頁能公開什麼所需的資料；件數相關的欄位只依 `members` 計算。"""

    series_id: uuid.UUID
    category: str
    name: str
    members: Sequence[SeriesMemberInput]
    """公開中的子事件，依發生日期（舊到新）排序。"""
    analysis: SeriesAnalysisInput | None = None
    actions: Sequence[ActionInput] = ()
    timeline: Timeline | None = None
    comparisons: Sequence[tuple[str, Comparison]] = ()
    """（措施名稱, 前後比較）。"""
    bursts: Sequence[BurstInput] = ()
    """程式依公開子事件的每週件數判斷的突增週（P6-T03 的 EWMA＋Poisson）。"""


def evaluate_series(
    item: SeriesPublicationInput,
    policy: PublishPolicy,
    texts: NotificationPolicy | None = None,
) -> PublicationDecision:
    """依規則判斷系列分析頁能公開的程度，並產生已遮蔽 PII 的公開內容。"""
    texts = texts or _default_texts()
    if item.category not in texts.event_types and item.category != "none":
        raise ValueError(f"未知的事件類別：{item.category}")
    reasons: list[str] = []

    def decide(content: dict[str, Any] | None, review: bool = False) -> PublicationDecision:
        level = PublicLevel.CONTENT if content is not None else PublicLevel.NONE
        return PublicationDecision(
            item.series_id, level, content, tuple(reasons), review, policy.policy_version
        )

    if item.category == "none" or item.category in policy.excluded_event_types:
        reasons.append("excluded_event_type")
        return decide(None)
    if not item.members:
        reasons.append("no_public_events")
        return decide(None)

    content, mentions = _series_content(item, texts)
    if any(texts.allegation.matches(t) for t in series_texts(content)):
        reasons.append(POSSIBLE_ALLEGATION)
    if mentions:
        reasons.append(MENTIONS_PARTY)
    if content["patterns"] or any(c["decision"] != "confirmed" for c in content["causes"]):
        reasons.append(SPECULATION)
    return decide(content, POSSIBLE_ALLEGATION in reasons or MENTIONS_PARTY in reasons)


def series_texts(content: Mapping[str, Any]) -> list[str]:
    """系列公開內容中所有的文字（指控比對、測試用）。"""
    found = [str(content.get("name") or ""), str(content.get("summary") or "")]
    found += [str(e.get("title") or "") for e in content.get("events") or []]
    found += [str(e.get("location") or "") for e in content.get("events") or []]
    found += [str(g.get("label") or "") for g in content.get("location_groups") or []]
    for key in ("patterns", "causes"):
        found += [str(x.get("description") or "") for x in content.get(key) or []]
    for m in content.get("measures") or []:
        found += [str(m.get("action") or ""), str(m.get("responsible_unit") or "")]
    found += [str(a.get("title") or "") for a in content.get("progress") or []]
    found += [str(c.get("title") or "") for c in content.get("comparisons") or []]
    found += [str(m.get("title") or "") for m in (content.get("timeline") or {}).get("markers", [])]
    return found


def review_parts(content: Mapping[str, Any] | None, reasons: Iterable[str]) -> list[Any]:
    """需要使用者注意的公開內容（公開提醒比對用）：疑似指控時是全部文字，否則是推測的規律與原因。

    兩個版本的結果不同時，代表需要提醒的內容第一次公開或改變了。
    """
    if content is None:
        return []
    codes = set(reasons)
    if content.get("kind") == "series":
        parts: list[Any] = []
        if POSSIBLE_ALLEGATION in codes:
            parts.append(sorted(series_texts(content)))
        parts += [
            ["pattern", p.get("description"), p.get("label")] for p in content.get("patterns") or []
        ]
        parts += [
            ["cause", c.get("description"), c.get("label")] for c in content.get("causes") or []
        ]
        return parts
    if POSSIBLE_ALLEGATION not in codes:
        return []
    return [content.get("title"), content.get("summary"), content.get("location")]


def _cited(raw: Mapping[str, Any], public: Mapping[str, str]) -> list[str]:
    """引用的事件換成公開的事件 ID；不公開的子事件不列入。"""
    return [public[str(e)] for e in raw.get("event_ids") or [] if str(e) in public]


def _series_content(
    item: SeriesPublicationInput, texts: NotificationPolicy
) -> tuple[dict[str, Any], bool]:
    """公開內容，以及公開的規律與原因是否提到特定個人、群體、店家或單位。"""
    public = {str(m.event_id): str(m.publication_id) for m in item.members}
    total = len(item.members)
    analysis = item.analysis or SeriesAnalysisInput()
    subjects = texts.allegation.subjects
    mentions = False

    def flag(raw: Mapping[str, Any]) -> None:
        nonlocal mentions
        text = str(raw.get("description") or "")
        mentions = mentions or bool(raw.get("mentions_party")) or any(s in text for s in subjects)

    groups = []
    for g in analysis.location_groups:
        ids = _cited(g, public)
        if ids:
            groups.append(
                {
                    "label": mask_pii(str(g.get("label") or "")),
                    "related_ids": ids,
                    "event_count": len(ids),
                    "total": total,
                }
            )
    patterns = []
    if total >= MIN_PATTERN_EVENTS:
        for p in analysis.patterns:
            ids = _cited(p, public)
            if len(ids) < MIN_PATTERN_EVENTS:
                continue
            flag(p)
            patterns.append(
                {
                    "description": mask_pii(str(p.get("description") or "")),
                    "label": SPECULATIVE_LABEL,
                    "related_ids": ids,
                    "event_count": len(ids),
                    "total": total,
                }
            )
    causes = []
    for c in analysis.causes:
        ids = _cited(c, public)
        if not ids:
            continue
        flag(c)
        confirmed = str(c.get("cause_id")) in analysis.confirmed_cause_ids
        causes.append(
            {
                "description": mask_pii(str(c.get("description") or "")),
                "label": CONFIRMED_LABEL if confirmed else SPECULATIVE_LABEL,
                "decision": "confirmed" if confirmed else "speculative",
                "related_ids": ids,
                "event_count": len(ids),
                "total": total,
            }
        )
    timeline = item.timeline
    dates = [m.occurrence_date for m in item.members if m.occurrence_date is not None]
    content = {
        "kind": "series",
        "event_type": item.category,
        "label": texts.event_types[item.category].label,
        "name": mask_pii(item.name),
        "summary": mask_pii(analysis.summary) if analysis.summary else None,
        "event_count": total,
        "first_date": _iso(min(dates)) if dates else None,
        "latest_date": _iso(max(dates)) if dates else None,
        "events": [
            {
                "id": str(m.publication_id),
                "ordinal": i,
                "title": mask_pii(m.title),
                "occurrence_date": _iso(m.occurrence_date),
                "location": mask_pii(m.location) if m.location else None,
                "verification": "verified" if m.verified else "unverified",
                "verification_label": VERIFIED_LABEL if m.verified else UNVERIFIED_LABEL,
            }
            for i, m in enumerate(item.members, start=1)
        ],
        "timeline": {
            "granularity": timeline.granularity if timeline else "week",
            "points": [
                {
                    "period_start": p.period_start.isoformat(),
                    "count": p.count,
                    "moving_average": p.moving_average,
                }
                for p in (timeline.points if timeline else ())
            ],
            "markers": [
                {"title": mask_pii(m.title), "kind": m.kind, "on": m.on.isoformat()}
                for m in (timeline.markers if timeline else ())
            ],
        },
        "bursts": [
            {
                "period_start": b.period_start.isoformat(),
                "count": b.count,
                "expected": round(b.expected, 2),
            }
            for b in item.bursts
        ],
        "location_groups": groups,
        "pattern_status": "ok" if patterns else "abstained",
        "pattern_note": FEW_EVENTS_NOTE if total < MIN_PATTERN_EVENTS else None,
        "patterns": patterns,
        "causes": causes,
        "measures": [
            {k: _mask(m.get(k)) for k in PUBLIC_MEASURE_FIELDS} for m in analysis.measures
        ],
        "progress": [
            _action(a) for a in item.actions if a.cause_basis != "hypothesized" or a.cause_confirmed
        ],
        "comparisons": [
            {
                "title": mask_pii(title),
                "start": c.start.isoformat(),
                "status": c.status,
                "before_events": c.before_events,
                "after_events": c.after_events,
                "before_weekly_average": c.before_weekly_average,
                "after_weekly_average": c.after_weekly_average,
                "message": c.message,
                "caveat": c.caveat,
            }
            for title, c in item.comparisons
        ],
    }
    return content, mentions
