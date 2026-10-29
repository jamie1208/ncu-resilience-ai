"""校園韌性成績單 CRI（P6-T05，規格 §15，G6-3）。

CRI = 0.25 Detection + 0.25 Response + 0.25 Recovery + 0.25 Adaptation，各構面 0–100：
- Detection（發現）：發現延遲 = 事件建立的台北日期 − S02 發生日期（天，負值算 0），取中位數；
- Response（回應）：事件建立到第一項改善措施進入「處理」的天數，取中位數；
- Recovery（恢復）：改善措施從第一次「處理」到之後第一次「恢復」的天數，取中位數；
- Adaptation（調適）：改善完成率（改進 ÷ 不含「不追蹤」的措施）與「1 − 重複事件比例」的平均；
  重複事件 = 時間窗內同地點、同類別不只一件的事件（地點不明不列入）。

天數依 `CriParams` 的錨點（best、worst）線性換算：≤ best 為 100、≥ worst 為 0。錨點是本專題
自訂的目標值，不是校方或外部標準。

不假精確：任何指標樣本數少於 `min_samples` 時該構面為 None（顯示 N/A）；四個構面齊全才計算
CRI，不以剩下的構面重新加權。分數只到整數，每個構面附樣本數與資料覆蓋率，報告附時間窗與
來源限制。

時間窗為 as_of（台北日期，含當天）往前 `window_days` 天；只用 as_of 當天結束前已建立的事件與
已發生的狀態變更。查證為不實（false）與類別 none 的事件不列入。公開版（`published_only`）只統計
目前以「事件內容」公開中的事件。純統計參數放在 `CriParams` 預設值，不放設定檔（同 P6-T03）。
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.states import EventStatus
from app.models.event import Event
from app.models.publication import Publication
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.publishing.policy import PublicLevel

TAIPEI = ZoneInfo("Asia/Taipei")
DIMENSIONS = ("detection", "response", "recovery", "adaptation")
INSUFFICIENT = "insufficient_samples"
IGNORED_TYPES = frozenset({"none"})
IGNORED_STATUSES = frozenset({EventStatus.FALSE.value})

REPORTS_NOT_RISK = (
    "回報變多不等於校園變危險：件數反映的是被張貼、被回報、被系統收到的事件，"
    "宣導或來源增加都會讓件數變多。"
)
LIMITATIONS: tuple[str, ...] = (
    REPORTS_NOT_RISK,
    "只涵蓋系統收集的來源（校方公告、社群社團貼文、匿名回報）；"
    "沒有被張貼或回報的事件不在統計內。",
    "發現延遲以 AI 從來源判讀的發生日期計算，發生日期不明的事件不列入。",
    "回應與恢復只計算有建立改善措施的事件；尚未恢復的措施不列入恢復時間。",
    "換算的目標天數是本專題自訂的，不是校方或外部標準；分數只供觀察趨勢，不代表校園安全程度。",
    "任何指標樣本數不足時顯示 N/A，總分只在四個構面都有分數時才計算。",
)
PUBLIC_LIMITATION = "公開版只統計已查證並公開的事件。"

Scope = Literal["all", "published"]


@dataclass(frozen=True)
class CriParams:
    """CRI 的統計參數；錨點為（best, worst）天數。"""

    window_days: int = 180
    min_samples: int = 5
    detection_anchor: tuple[float, float] = (1, 14)
    response_anchor: tuple[float, float] = (1, 30)
    recovery_anchor: tuple[float, float] = (3, 60)
    weights: tuple[float, float, float, float] = field(default=(0.25, 0.25, 0.25, 0.25))

    def __post_init__(self) -> None:
        if self.window_days < 1:
            raise ValueError("window_days 至少 1")
        if self.min_samples < 1:
            raise ValueError("min_samples 至少 1")
        for best, worst in (self.detection_anchor, self.response_anchor, self.recovery_anchor):
            if not 0 <= best < worst:
                raise ValueError("錨點必須 0 ≤ best < worst")
        if abs(sum(self.weights) - 1) > 1e-9:
            raise ValueError("權重總和必須為 1")


@dataclass(frozen=True)
class EventRecord:
    """計算 CRI 所需的一個事件（由 `load_records` 從資料庫組出）。"""

    event_id: uuid.UUID
    event_type: str
    status: str
    location_id: uuid.UUID | None
    occurred_on: date | None
    """S02 抽取的發生日期（台北）；沒有則為 None。"""
    created_at: datetime
    first_handling_at: datetime | None
    """任一項改善措施第一次轉到「處理」的時間。"""


@dataclass(frozen=True)
class ActionRecord:
    """一項改善措施的目前狀態與關鍵時間。"""

    action_id: uuid.UUID
    event_id: uuid.UUID
    state: str
    handling_at: datetime | None
    """第一次轉到「處理」的時間。"""
    recovered_at: datetime | None
    """第一次「處理」之後，第一次轉到「恢復」的時間。"""


@dataclass(frozen=True)
class Metric:
    """構面中的一項指標；`value` 為天數（中位數）或比例，樣本不足時為 None。

    `coverage` = 有資料可算的樣本 ÷ 應列入的數量（`eligible`）。
    """

    key: str
    unit: Literal["days", "ratio"]
    value: float | None
    score: int | None
    sample_size: int
    eligible: int
    coverage: float | None


@dataclass(frozen=True)
class Dimension:
    """一個構面；樣本數、應列入數量與覆蓋率取覆蓋率最低的那項指標。"""

    key: str
    score: int | None
    sample_size: int
    eligible: int
    coverage: float | None
    reason: str | None
    metrics: tuple[Metric, ...]


@dataclass(frozen=True)
class CriReport:
    """校園韌性成績單。"""

    score: int | None
    dimensions: tuple[Dimension, ...]
    window_start: date
    window_end: date
    event_count: int
    action_count: int
    min_samples: int
    weights: tuple[float, float, float, float]
    scope: Scope
    limitations: tuple[str, ...]


def linear_score(days: float, *, best: float, worst: float) -> float:
    """天數線性換算為 0–100：≤ best 為 100、≥ worst 為 0。"""
    if days <= best:
        return 100.0
    if days >= worst:
        return 0.0
    return 100.0 * (worst - days) / (worst - best)


def _days(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / 86400)


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 2) if whole else None


@dataclass(frozen=True)
class _Raw:
    """未四捨五入的指標，供加總時使用。"""

    key: str
    unit: Literal["days", "ratio"]
    value: float | None
    score: float | None
    sample_size: int
    eligible: int

    def out(self) -> Metric:
        return Metric(
            key=self.key,
            unit=self.unit,
            value=round(self.value, 2) if self.value is not None else None,
            score=round(self.score) if self.score is not None else None,
            sample_size=self.sample_size,
            eligible=self.eligible,
            coverage=_ratio(self.sample_size, self.eligible),
        )


def _delay_metric(
    key: str,
    samples: Sequence[float],
    eligible: int,
    anchor: tuple[float, float],
    min_samples: int,
) -> _Raw:
    n = len(samples)
    if n < min_samples:
        return _Raw(key, "days", None, None, n, eligible)
    value = float(median(samples))
    best, worst = anchor
    return _Raw(key, "days", value, linear_score(value, best=best, worst=worst), n, eligible)


def _rate_metric(
    key: str, part: int, whole: int, eligible: int, *, invert: bool, min_samples: int
) -> _Raw:
    if whole < min_samples:
        return _Raw(key, "ratio", None, None, whole, eligible)
    value = part / whole
    return _Raw(key, "ratio", value, 100.0 * (1 - value if invert else value), whole, eligible)


def _dimension(key: str, metrics: Sequence[_Raw]) -> tuple[Dimension, float | None]:
    weakest = min(metrics, key=lambda m: m.sample_size / m.eligible if m.eligible else -1)
    scores = [m.score for m in metrics if m.score is not None]
    raw = sum(scores) / len(scores) if len(scores) == len(metrics) else None
    dimension = Dimension(
        key=key,
        score=round(raw) if raw is not None else None,
        sample_size=min(m.sample_size for m in metrics),
        eligible=weakest.eligible,
        coverage=_ratio(weakest.sample_size, weakest.eligible),
        reason=None if raw is not None else INSUFFICIENT,
        metrics=tuple(m.out() for m in metrics),
    )
    return dimension, raw


def _event_date(e: EventRecord) -> date:
    return e.occurred_on or e.created_at.astimezone(TAIPEI).date()


def compute_cri(
    events: Sequence[EventRecord],
    actions: Sequence[ActionRecord],
    *,
    as_of: date,
    params: CriParams | None = None,
    published_only: bool = False,
) -> CriReport:
    """依時間窗內的事件與改善措施計算成績單；同一輸入永遠得到同一結果。"""
    p = params or CriParams()
    start = as_of - timedelta(days=p.window_days - 1)
    cutoff = datetime.combine(as_of + timedelta(days=1), time.min, TAIPEI)

    def known(moment: datetime | None) -> datetime | None:
        return moment if moment is not None and moment < cutoff else None

    included = [
        e
        for e in events
        if e.status not in IGNORED_STATUSES
        and e.event_type not in IGNORED_TYPES
        and e.created_at < cutoff
        and start <= _event_date(e) <= as_of
    ]
    ids = {e.event_id for e in included}
    acts = [a for a in actions if a.event_id in ids]

    # Detection
    detection_samples = [
        float(max(0, (e.created_at.astimezone(TAIPEI).date() - e.occurred_on).days))
        for e in included
        if e.occurred_on is not None
    ]
    detection = _delay_metric(
        "delay_days", detection_samples, len(included), p.detection_anchor, p.min_samples
    )

    # Response
    response_samples = [
        _days(e.created_at, handled)
        for e in included
        if (handled := known(e.first_handling_at)) is not None
    ]
    response = _delay_metric(
        "delay_days", response_samples, len(included), p.response_anchor, p.min_samples
    )

    # Recovery
    handled_actions = [a for a in acts if known(a.handling_at) is not None]
    recovery_samples = [
        _days(a.handling_at, recovered)
        for a in handled_actions
        if a.handling_at is not None and (recovered := known(a.recovered_at)) is not None
    ]
    recovery = _delay_metric(
        "days", recovery_samples, len(handled_actions), p.recovery_anchor, p.min_samples
    )

    # Adaptation
    tracked = [a for a in acts if a.state != "dropped"]
    improved = sum(1 for a in tracked if a.state == "improved")
    completion = _rate_metric(
        "completion_rate",
        improved,
        len(tracked),
        len(acts),
        invert=False,
        min_samples=p.min_samples,
    )
    located = [e for e in included if e.location_id is not None]
    groups = Counter((e.location_id, e.event_type) for e in located)
    repeated = sum(1 for e in located if groups[(e.location_id, e.event_type)] > 1)
    repeat = _rate_metric(
        "repeat_ratio",
        repeated,
        len(located),
        len(included),
        invert=True,
        min_samples=p.min_samples,
    )

    built = [
        _dimension("detection", [detection]),
        _dimension("response", [response]),
        _dimension("recovery", [recovery]),
        _dimension("adaptation", [completion, repeat]),
    ]
    raws = [raw for _, raw in built]
    overall = (
        None
        if any(r is None for r in raws)
        else round(sum(w * r for w, r in zip(p.weights, raws, strict=True) if r is not None))
    )
    return CriReport(
        score=overall,
        dimensions=tuple(d for d, _ in built),
        window_start=start,
        window_end=as_of,
        event_count=len(included),
        action_count=len(acts),
        min_samples=p.min_samples,
        weights=p.weights,
        scope="published" if published_only else "all",
        limitations=(*LIMITATIONS, PUBLIC_LIMITATION) if published_only else LIMITATIONS,
    )


async def load_records(
    session: AsyncSession, *, start: date, published_only: bool = False
) -> tuple[list[EventRecord], list[ActionRecord]]:
    """讀出可能列入時間窗的事件與其改善措施；時間窗與 as_of 的篩選由 `compute_cri` 完成。"""
    first_handling = (
        select(
            ResponseActionTransition.action_id.label("action_id"),
            func.min(ResponseActionTransition.created_at).label("at"),
        )
        .where(ResponseActionTransition.to_state == "handling")
        .group_by(ResponseActionTransition.action_id)
        .subquery()
    )
    since_at = datetime.combine(start, time.min, TAIPEI)
    query = select(
        Event.id,
        Event.type,
        Event.status,
        Event.location_id,
        Event.occurrence_start_date,
        Event.created_at,
    ).where(
        Event.status.not_in(IGNORED_STATUSES),
        Event.type.not_in(IGNORED_TYPES),
        (Event.occurrence_start_date >= start)
        | (Event.occurrence_start_date.is_(None) & (Event.created_at >= since_at)),
    )
    if published_only:
        query = query.join(Publication, Publication.event_id == Event.id).where(
            Publication.state == "published", Publication.level == PublicLevel.CONTENT.value
        )
    event_rows = (await session.execute(query)).all()
    ids = [row.id for row in event_rows]
    if not ids:
        return [], []

    action_rows = (
        await session.execute(
            select(
                ResponseAction.id,
                ResponseAction.event_id,
                ResponseAction.state,
                first_handling.c.at,
            )
            .outerjoin(first_handling, first_handling.c.action_id == ResponseAction.id)
            .where(ResponseAction.event_id.in_(ids))
        )
    ).all()
    recovered_rows = (
        await session.execute(
            select(ResponseActionTransition.action_id, ResponseActionTransition.created_at)
            .join(ResponseAction, ResponseAction.id == ResponseActionTransition.action_id)
            .where(
                ResponseAction.event_id.in_(ids),
                ResponseActionTransition.to_state == "recovered",
            )
            .order_by(ResponseActionTransition.created_at)
        )
    ).all()
    recovered: dict[uuid.UUID, list[datetime]] = {}
    for row in recovered_rows:
        recovered.setdefault(row.action_id, []).append(row.created_at)

    actions: list[ActionRecord] = []
    first_by_event: dict[uuid.UUID, datetime] = {}
    for row in action_rows:
        handling_at: datetime | None = row.at
        recovered_at = (
            next((t for t in recovered.get(row.id, []) if t >= handling_at), None)
            if handling_at is not None
            else None
        )
        actions.append(ActionRecord(row.id, row.event_id, row.state, handling_at, recovered_at))
        if handling_at is not None:
            current = first_by_event.get(row.event_id)
            first_by_event[row.event_id] = min(current, handling_at) if current else handling_at

    events = [
        EventRecord(
            event_id=row.id,
            event_type=row.type,
            status=row.status,
            location_id=row.location_id,
            occurred_on=row.occurrence_start_date,
            created_at=row.created_at,
            first_handling_at=first_by_event.get(row.id),
        )
        for row in event_rows
    ]
    return events, actions


async def build_report(
    session: AsyncSession,
    *,
    as_of: date | None = None,
    params: CriParams | None = None,
    published_only: bool = False,
) -> CriReport:
    """讀資料庫並計算成績單；`as_of` 預設為台北今天。"""
    p = params or CriParams()
    day = as_of or datetime.now(TAIPEI).date()
    start = day - timedelta(days=p.window_days - 1)
    events, actions = await load_records(session, start=start, published_only=published_only)
    return compute_cri(events, actions, as_of=day, params=p, published_only=published_only)


DIMENSION_LABELS = {
    "detection": "發現",
    "response": "回應",
    "recovery": "恢復",
    "adaptation": "調適",
}


def report_to_dict(report: CriReport) -> dict[str, Any]:
    """API 回應用的成績單（只有彙總數字與說明，不含事件、地點或 ID）。"""
    return {
        "score": report.score,
        "scope": report.scope,
        "window": {
            "start": report.window_start.isoformat(),
            "end": report.window_end.isoformat(),
            "days": (report.window_end - report.window_start).days + 1,
        },
        "event_count": report.event_count,
        "action_count": report.action_count,
        "min_samples": report.min_samples,
        "weights": list(report.weights),
        "dimensions": [
            {
                "key": d.key,
                "label": DIMENSION_LABELS[d.key],
                "score": d.score,
                "sample_size": d.sample_size,
                "eligible": d.eligible,
                "coverage": d.coverage,
                "reason": d.reason,
                "metrics": [asdict(m) for m in d.metrics],
            }
            for d in report.dimensions
        ],
        "limitations": list(report.limitations),
    }
