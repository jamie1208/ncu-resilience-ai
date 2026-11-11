"""系列分析（P8-T05，ADR-0018 決策 1-5、1-8，ADR-0020 決策 2-3、3-2、3-4）：S12 series-analyst。

輸入（`build_payload`）：系列中不是 false 的子事件（依發生日期排序、最近的最多 30 起，代號 E1…），
每起附標題、摘要、日期、代表地點名稱、查證狀態與最多 3 份證據（總數最多 30 份）；程式算出的最近
26 週每週件數與突增週（P6-T03 的 EWMA＋Poisson，代號 b1…）。

驗證（`_process`，模型輸出已通過 Skill Runner 的 schema 與業務驗證）：
- 各區塊引用的子事件代號必須在輸入中，否則整項丟棄（unknown_event）；
- 規律與原因的逐字原句必須出自該項引用的子事件的證據（quote_not_in_events）；
- 規律至少要有 2 起不同的子事件（fewer_than_two_events）；系列不到 2 件時規律區一律 abstain；
- 突增解釋只能對應輸入中的突增週（unknown_burst），週期、件數與期望值由程式填入；
- 「y／x 件」由程式依引用的子事件計算（`event_count`、`total`），模型不寫數字；
- 規律與原因一律標「推測，尚未查證」；提到特定個人、群體、店家或單位（指控規則的對象關鍵字，
  `config/notification-policy.yaml`）時標 `mentions_party`，不擋，交給公開規則（P8-T06）處理；
- 建議指向被丟棄的原因時改為一般建議；文字一律經 `mask_pii()`；
- 名稱：帶任一子事件的地點或日期（數字＋／、月、日）時不採用（location_or_date）。
丟棄的項目記在 `dropped`（區塊、索引、原因），不含內容。

何時分析：
- 每個系列每天（台北時間）最多一次，含失敗與 abstain；受每日費用上限（Runner 回 failed）。
- `stale_series`：今天還沒分析、且從沒分析過或上次分析之後有事件加入或移出的系列。
  管線在事件加入系列後排入分析（`enqueue_series_analysis`）；Celery beat 以新資料最短的擷取間隔
  執行 `analyze_stale_task` 補做過期的系列，也可用 `uv run python -m app.series.analysis stale`
  手動執行。
- 沒有子事件或證據時不呼叫、不寫紀錄。
- 分析成功且名稱有效時，名稱取代系列名稱（title_source ai）；人工改過的名稱（manual）不覆蓋。
  每次分析寫稽核 `series.analyze`（分析 id、狀態、丟棄數、名稱是否改變）。

人工操作：
- `rename_series`：手動改名（title_source manual），稽核 `series.rename`（from／to，沿用 2026-11-09
  正式資料手動改名的格式）。
- `decide_series_cause`：確認或回滾最新一次成功分析中的推測原因（ADR-0017 的規則：確認依據必填、
  最多 200 字、含個資時拒絕；只新增不修改；重新分析後要重新確認），稽核 `series.cause_decide`
  （不含依據文字）。

本模組只 flush、不 commit（`analyze_series` 在呼叫 S12 前後由 `run` 提交執行紀錄）。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.run import RunStatus, SkillRunResult
from app.ai.runner.validate import find_pii
from app.alerts.pii import mask_pii
from app.alerts.policy import NotificationPolicy
from app.alerts.policy import load_policy as load_notification_policy
from app.analytics.timeseries import (
    PeriodCount,
    detect_changes,
    load_locations,
    period_start,
    periods,
    representative,
)
from app.core.security import Principal
from app.domain.states import EventStatus
from app.models.event import Event
from app.models.series import (
    EventSeries,
    EventSeriesMember,
    EventSeriesRemoval,
    SeriesAnalysis,
    SeriesCauseDecision,
)
from app.services.audit import record_audit
from app.worker.celery_app import ANALYZE_SERIES_TASK, ANALYZE_STALE_SERIES_TASK, celery_app

S12 = "series-analyst"
MAX_EVENTS = 30
MAX_EVIDENCE = 30
MAX_EVIDENCE_PER_EVENT = 3
COUNT_WEEKS = 26
TITLE_MAX = 30
BASIS_MAX = 200
SPECULATIVE_LABEL = "推測，尚未查證"
CONFIRMED_LABEL = "已確認"
TAIPEI = ZoneInfo("Asia/Taipei")
_CATEGORIES = frozenset(
    {"facility", "weather", "security", "traffic", "health", "fire", "academic", "infosec"}
)
_DATE_LIKE = re.compile(r"\d{1,4}\s*[/／.-]\s*\d{1,2}|\d+\s*[月日號]|週[一二三四五六日]|星期")

SkillCall = Callable[[Mapping[str, Any]], Awaitable[SkillRunResult]]


class CauseDecision(StrEnum):
    """系列原因的人工決策；值存入資料庫。"""

    CONFIRMED = "confirmed"
    HYPOTHESIZED = "hypothesized"


class AnalysisError(Exception):
    """系列分析相關操作失敗。"""


class AnalysisNotFoundError(AnalysisError):
    """系列、分析或原因不存在。"""


class AnalysisConflictError(AnalysisError):
    """分析不是最新的，或與目前決策衝突。"""


class AnalysisInvalidError(AnalysisError):
    """輸入不合格；`field` 為出問題的欄位。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def taipei_date(moment: datetime) -> date:
    """台北時間的日期。"""
    return moment.astimezone(TAIPEI).date()


def event_date(event: Event) -> date:
    """事件日期：S02 的發生日期，沒有時用建立時間的台北日期（與統計、再發生偵測一致）。"""
    return event.occurrence_start_date or taipei_date(event.created_at)


def _clean(value: Any) -> str:
    return mask_pii(str(value or "")).strip()


# ---------- 資料 ----------


async def series_events(session: AsyncSession, series_id: uuid.UUID) -> list[Event]:
    """系列中不是 false 的子事件，依事件日期（同日依建立時間）由舊到新。"""
    rows = await session.scalars(
        select(Event)
        .join(EventSeriesMember, EventSeriesMember.event_id == Event.id)
        .where(EventSeriesMember.series_id == series_id, Event.status != EventStatus.FALSE.value)
    )
    return sorted(rows, key=lambda e: (event_date(e), e.created_at, str(e.id)))


async def location_names(session: AsyncSession, events: Iterable[Event]) -> dict[uuid.UUID, str]:
    """事件 id → 代表地點名稱（沒有地點的事件不在結果中）。"""
    locations = await load_locations(session)
    out = {}
    for event in events:
        if event.location_id is None:
            continue
        rep = representative(event.location_id, locations)
        if rep is not None:
            out[event.id] = rep.name
    return out


def weekly_counts(
    dates: Sequence[date], today: date, weeks: int = COUNT_WEEKS
) -> list[PeriodCount]:
    """最近 `weeks` 週（含本週）的每週件數。"""
    last = period_start(today, "week")
    first = last - timedelta(weeks=weeks - 1)
    counts: dict[date, int] = {}
    for d in dates:
        start = period_start(d, "week")
        counts[start] = counts.get(start, 0) + 1
    return [PeriodCount(p, counts.get(p, 0)) for p in periods(first, last, "week")]


@dataclass(frozen=True)
class _Prepared:
    payload: dict[str, Any]
    codes: dict[str, Event]
    owners: dict[str, set[str]]
    """evidence_id → 引用它的子事件代號。"""
    bursts: dict[str, dict[str, Any]]
    locations: dict[str, str]
    """代號 → 地點名稱。"""


async def _prepare(
    session: AsyncSession, series: EventSeries, events: Sequence[Event], today: date
) -> _Prepared | None:
    from app.pipeline.analyze_item import _event_item_ids, _evidence  # 管線匯入系列模組

    recent = list(events)[-MAX_EVENTS:]
    per_event = max(1, min(MAX_EVIDENCE_PER_EVENT, MAX_EVIDENCE // len(recent)))
    names = await location_names(session, recent)
    codes: dict[str, Event] = {}
    owners: dict[str, set[str]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    items = []
    for i, event in enumerate(recent, start=1):
        code = f"E{i}"
        codes[code] = event
        ids: list[str] = []
        for ev in await _evidence(session, await _event_item_ids(session, event.id, per_event)):
            evidence_id = str(ev["evidence_id"])
            if evidence_id not in evidence and len(evidence) >= MAX_EVIDENCE:
                continue
            evidence.setdefault(evidence_id, ev)
            owners.setdefault(evidence_id, set()).add(code)
            ids.append(evidence_id)
        items.append(
            {
                "event_id": code,
                "title": event.title[:300],
                "summary": event.summary[:1000] if event.summary else None,
                "occurrence_start_date": (
                    event.occurrence_start_date.isoformat() if event.occurrence_start_date else None
                ),
                "location": names[event.id][:100] if event.id in names else None,
                "verification_status": (
                    event.status if event.status in ("verified", "disputed") else "unverified"
                ),
                "evidence_ids": ids,
            }
        )
    if not evidence or series.category not in _CATEGORIES:
        return None
    counts = weekly_counts([event_date(e) for e in events], today)
    bursts = {
        f"b{i}": {
            "burst_id": f"b{i}",
            "period_start": a.period_start.isoformat(),
            "count": a.observed,
            "expected": round(a.expected, 3),
        }
        for i, a in enumerate(detect_changes(counts)[-6:], start=1)
    }
    payload = {
        "evidence": list(evidence.values()),
        "series": {
            "category": series.category,
            "title": series.title[:300],
            "event_count": len(events),
        },
        "events": items,
        "weekly_counts": [
            {"period_start": c.period_start.isoformat(), "count": c.count} for c in counts
        ],
        "bursts": list(bursts.values()),
    }
    locations = {code: names[e.id] for code, e in codes.items() if e.id in names}
    return _Prepared(payload, codes, owners, bursts, locations)


# ---------- 驗證 ----------


class _Checker:
    def __init__(self, prepared: _Prepared, total: int, policy: NotificationPolicy) -> None:
        self.p = prepared
        self.total = total
        self.subjects = policy.allegation.subjects
        self.dropped: list[dict[str, Any]] = []

    def drop(self, section: str, index: int, reason: str) -> None:
        self.dropped.append({"section": section, "index": index, "reason": reason})

    def events(self, section: str, index: int, raw: Any, *, minimum: int = 1) -> list[str] | None:
        codes = list(dict.fromkeys(str(c) for c in raw or []))
        if any(c not in self.p.codes for c in codes) or not codes:
            self.drop(section, index, "unknown_event")
            return None
        if len(codes) < minimum:
            self.drop(section, index, "fewer_than_two_events")
            return None
        return codes

    def quotes(
        self, section: str, index: int, raw: Any, codes: Sequence[str]
    ) -> list[dict[str, str]] | None:
        spans = [s for s in raw or [] if isinstance(s, Mapping)]
        allowed = set(codes)
        if not spans or any(not (self.p.owners.get(str(s.get("evidence_id"))) or set()) & allowed
                            for s in spans):  # fmt: skip
            self.drop(section, index, "quote_not_in_events")
            return None
        return [
            {"evidence_id": str(s["evidence_id"]), "quote": _clean(s.get("quote"))} for s in spans
        ]

    def ids(self, codes: Sequence[str]) -> list[str]:
        return [str(self.p.codes[c].id) for c in codes]

    def mentions(self, *texts: str) -> bool:
        return any(s in t for s in self.subjects for t in texts)

    def title(self, raw: Any) -> str | None:
        title = _clean(raw)[:TITLE_MAX]
        if not title:
            return None
        places = {name for name in self.p.locations.values() if len(name) >= 2}
        if _DATE_LIKE.search(title) or any(p in title for p in places):
            self.drop("title", 0, "location_or_date")
            return None
        return title


def _process(
    output: Mapping[str, Any], prepared: _Prepared, policy: NotificationPolicy
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total = len(prepared.codes)
    c = _Checker(prepared, total, policy)
    bursts = []
    for i, raw in enumerate(output.get("burst_explanations") or []):
        burst = prepared.bursts.get(str(raw.get("burst_id")))
        if burst is None:
            c.drop("burst_explanations", i, "unknown_burst")
            continue
        codes = c.events("burst_explanations", i, raw.get("event_ids"))
        if codes is None:
            continue
        bursts.append(
            {
                **burst,
                "explanation": _clean(raw.get("explanation")),
                "event_ids": c.ids(codes),
                "event_count": len(codes),
            }
        )
    groups = []
    for i, raw in enumerate(output.get("location_groups") or []):
        codes = c.events("location_groups", i, raw.get("event_ids"))
        if codes is None:
            continue
        groups.append(
            {
                "label": _clean(raw.get("label")),
                "reason": _clean(raw.get("reason")),
                "event_ids": c.ids(codes),
                "event_count": len(codes),
                "total": total,
            }
        )
    patterns = []
    for i, raw in enumerate(output.get("patterns") or []):
        codes = c.events("patterns", i, raw.get("event_ids"), minimum=2)
        quotes = c.quotes("patterns", i, raw.get("evidence_spans"), codes) if codes else None
        if codes is None or quotes is None:
            continue
        description = _clean(raw.get("description"))
        patterns.append(
            {
                "pattern_id": str(raw["pattern_id"]),
                "description": description,
                "label": SPECULATIVE_LABEL,
                "event_ids": c.ids(codes),
                "event_count": len(codes),
                "total": total,
                "quotes": quotes,
                "evidence_ids": [str(e) for e in raw.get("evidence_ids") or []],
                "mentions_party": c.mentions(description),
            }
        )
    causes = []
    for i, raw in enumerate(output.get("causes") or []):
        codes = c.events("causes", i, raw.get("event_ids"))
        quotes = c.quotes("causes", i, raw.get("evidence_spans"), codes) if codes else None
        if codes is None or quotes is None:
            continue
        description, to_verify = _clean(raw.get("description")), _clean(raw.get("to_verify"))
        causes.append(
            {
                "cause_id": str(raw["cause_id"]),
                "description": description,
                "label": SPECULATIVE_LABEL,
                "to_verify": to_verify or None,
                "event_ids": c.ids(codes),
                "event_count": len(codes),
                "total": total,
                "quotes": quotes,
                "evidence_ids": [str(e) for e in raw.get("evidence_ids") or []],
                "mentions_party": c.mentions(description, to_verify),
            }
        )
    kept = {x["cause_id"] for x in causes}
    measures = []
    for raw in output.get("measures") or []:
        action = _clean(raw.get("action"))
        if not action:
            continue
        cause_id = str(raw["cause_id"]) if raw.get("cause_id") in kept else None
        unit = _clean(raw.get("responsible_unit")) if raw.get("audience") != "students" else ""
        measures.append(
            {
                "action": action,
                "audience": raw["audience"],
                "horizon": raw["horizon"],
                "priority": raw["priority"],
                "kind": raw["kind"],
                "cause_id": cause_id,
                "responsible_unit": unit or None,
                "evidence_ids": [str(e) for e in raw.get("evidence_ids") or []],
            }
        )
    if total < 2:
        pattern_status, pattern_reason, patterns = "abstained", "too_few_events", []
    elif patterns:
        pattern_status, pattern_reason = "ok", None
    elif output.get("pattern_status") == "abstain":
        pattern_status, pattern_reason = "abstained", output.get("pattern_abstain_reason")
    else:
        pattern_status, pattern_reason = "abstained", "no_valid_pattern"
    sections = {
        "title": c.title(output.get("title")),
        "summary": _clean(output.get("reason")) or None,
        "bursts": bursts,
        "location_groups": groups,
        "pattern_status": pattern_status,
        "pattern_abstain_reason": pattern_reason,
        "patterns": patterns,
        "causes": causes,
        "measures": measures,
    }
    return sections, c.dropped


# ---------- 分析 ----------


async def latest_analysis(
    session: AsyncSession, series_id: uuid.UUID, *, ok_only: bool = True
) -> SeriesAnalysis | None:
    """最新一次（成功的）分析。"""
    query = select(SeriesAnalysis).where(SeriesAnalysis.series_id == series_id)
    if ok_only:
        query = query.where(SeriesAnalysis.status == "ok")
    return await session.scalar(
        query.order_by(SeriesAnalysis.created_at.desc(), SeriesAnalysis.id.desc()).limit(1)
    )


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    day = taipei_date(now)
    start = datetime.combine(day, datetime.min.time(), TAIPEI)
    return start, start + timedelta(days=1)


async def analyzed_today(session: AsyncSession, series_id: uuid.UUID, now: datetime) -> bool:
    """今天（台北時間）是否已分析過（含失敗與 abstain）。"""
    start, end = _day_bounds(now)
    found = await session.scalar(
        select(SeriesAnalysis.id)
        .where(
            SeriesAnalysis.series_id == series_id,
            SeriesAnalysis.created_at >= start,
            SeriesAnalysis.created_at < end,
        )
        .limit(1)
    )
    return found is not None


async def analyze_series(
    session: AsyncSession,
    series_id: uuid.UUID,
    *,
    run: SkillCall,
    principal: Principal,
    now: datetime | None = None,
    trace_id: str | None = None,
    policy: NotificationPolicy | None = None,
    force: bool = False,
) -> SeriesAnalysis | None:
    """分析系列並寫入結果；今天已分析、系列不存在、沒有事件或證據時不呼叫（回傳 None）。

    `force` 略過「今天已分析」的限制，只供 P8-T17 的語氣重跑命令使用（管線與 beat 不傳）。
    """
    now = now or datetime.now(UTC)
    series = await session.get(EventSeries, series_id)
    if series is None or (not force and await analyzed_today(session, series_id, now)):
        return None
    events = await series_events(session, series_id)
    prepared = await _prepare(session, series, events, taipei_date(now)) if events else None
    if prepared is None:
        return None
    result = await run(prepared.payload)
    output = result.output if result.status is RunStatus.SUCCEEDED else None
    trace = trace_id or uuid.uuid4().hex
    row = SeriesAnalysis(
        series_id=series_id,
        status="failed",
        pattern_status="failed",
        events=[{"code": code, "event_id": str(e.id)} for code, e in prepared.codes.items()],
        ai_run_id=result.run_id,
        trace_id=trace[:64],
        created_at=now,
    )
    if output is not None and output.get("status") == "ok":
        sections, dropped = _process(output, prepared, policy or load_notification_policy())
        for key, value in sections.items():
            setattr(row, key, value)
        row.status, row.dropped = "ok", dropped
    elif output is not None or result.status is RunStatus.ABSTAINED:
        source = output or {}
        row.status = row.pattern_status = "abstained"
        row.abstain_reason = str(
            source.get("abstain_reason") or result.abstain_reason or "insufficient_evidence"
        )[:64]
    session.add(row)
    await session.flush()
    changed = False
    if row.status == "ok" and row.title and series.title_source != "manual":
        changed = series.title != row.title
        series.title, series.title_source = row.title, "ai"
    await record_audit(
        session,
        principal,
        "series.analyze",
        target=f"series:{series_id}",
        request_id=trace[:64],
        metadata={
            "analysis_id": str(row.id),
            "status": row.status,
            "dropped": len(row.dropped or []),
            "title_changed": changed,
        },
    )
    await session.flush()
    return row


async def stale_series(session: AsyncSession, *, now: datetime | None = None) -> list[uuid.UUID]:
    """今天還沒分析，且從沒分析過或上次分析之後有事件加入、移出的系列（最久沒更新的在前）。"""
    now = now or datetime.now(UTC)
    start, end = _day_bounds(now)
    last = (
        select(SeriesAnalysis.series_id, func.max(SeriesAnalysis.created_at).label("at"))
        .group_by(SeriesAnalysis.series_id)
        .subquery()
    )
    joined = (
        select(EventSeriesMember.series_id, func.max(EventSeriesMember.joined_at).label("at"))
        .group_by(EventSeriesMember.series_id)
        .subquery()
    )
    removed = (
        select(EventSeriesRemoval.series_id, func.max(EventSeriesRemoval.created_at).label("at"))
        .where(EventSeriesRemoval.series_id.is_not(None))
        .group_by(EventSeriesRemoval.series_id)
        .subquery()
    )
    rows = await session.execute(
        select(EventSeries.id)
        .join(joined, joined.c.series_id == EventSeries.id)
        .outerjoin(last, last.c.series_id == EventSeries.id)
        .outerjoin(removed, removed.c.series_id == EventSeries.id)
        .where(
            (last.c.at.is_(None))
            | (
                ~((last.c.at >= start) & (last.c.at < end))
                & ((joined.c.at > last.c.at) | (removed.c.at > last.c.at))
            )
        )
        .order_by(last.c.at.asc().nulls_first(), EventSeries.id)
    )
    return list(rows.scalars())


# ---------- 人工操作 ----------


async def rename_series(
    session: AsyncSession, principal: Principal, series_id: uuid.UUID, title: str
) -> EventSeries:
    """手動改名；之後 S12 不再覆蓋名稱。"""
    cleaned = (title or "").strip()
    if not cleaned:
        raise AnalysisInvalidError("title", "請填寫系列名稱")
    if len(cleaned) > 300:
        raise AnalysisInvalidError("title", "系列名稱超過 300 字")
    if find_pii(cleaned):
        raise AnalysisInvalidError("title", "系列名稱疑似含個資")
    series = await session.scalar(
        select(EventSeries).where(EventSeries.id == series_id).with_for_update()
    )
    if series is None:
        raise AnalysisNotFoundError("找不到系列")
    previous = series.title
    series.title, series.title_source = cleaned, "manual"
    await session.flush()
    await record_audit(
        session,
        principal,
        "series.rename",
        target=f"series:{series_id}",
        metadata={"from": previous, "to": cleaned},
    )
    return series


async def current_decisions(
    session: AsyncSession, analysis_id: uuid.UUID
) -> dict[str, SeriesCauseDecision]:
    """分析中各原因目前的人工決策（最新一筆）。"""
    rows = await session.scalars(
        select(SeriesCauseDecision)
        .where(SeriesCauseDecision.analysis_id == analysis_id)
        .order_by(SeriesCauseDecision.decided_at, SeriesCauseDecision.id)
    )
    return {row.cause_id: row for row in rows}


async def decide_series_cause(
    session: AsyncSession,
    principal: Principal,
    series_id: uuid.UUID,
    analysis_id: uuid.UUID,
    cause_id: str,
    to: CauseDecision,
    *,
    basis: str | None = None,
) -> SeriesCauseDecision:
    """人工確認（confirmed）或回滾（hypothesized）最新一次分析的推測原因。"""
    analysis = await session.get(SeriesAnalysis, analysis_id)
    if analysis is None or analysis.series_id != series_id:
        raise AnalysisNotFoundError("找不到系列分析")
    latest = await latest_analysis(session, series_id)
    if latest is None or latest.id != analysis.id:
        raise AnalysisConflictError("只能確認最新一次分析的原因，請重新整理後再操作")
    if not any(str(c.get("cause_id")) == cause_id for c in analysis.causes):
        raise AnalysisNotFoundError("找不到原因")
    note: str | None = None
    if to is CauseDecision.CONFIRMED:
        note = (basis or "").strip()
        if not note:
            raise AnalysisInvalidError("basis", "確認原因必須填寫確認依據")
        if len(note) > BASIS_MAX:
            raise AnalysisInvalidError("basis", f"確認依據超過 {BASIS_MAX} 字")
        if find_pii(note):
            raise AnalysisInvalidError("basis", "確認依據疑似含個資")
    current = (await current_decisions(session, analysis.id)).get(cause_id)
    previous = CauseDecision(current.decision) if current else CauseDecision.HYPOTHESIZED
    if previous is to:
        raise AnalysisConflictError(
            "原因已經人工確認" if to is CauseDecision.CONFIRMED else "原因尚未人工確認，不需回滾"
        )
    decision = SeriesCauseDecision(
        analysis_id=analysis.id,
        cause_id=cause_id,
        decision=to.value,
        basis=note,
        decided_by=principal.actor,
        decided_at=datetime.now(UTC),
    )
    session.add(decision)
    await session.flush()
    await record_audit(
        session,
        principal,
        "series.cause_decide",
        target=f"series:{series_id}",
        metadata={
            "analysis_id": str(analysis.id),
            "cause_id": cause_id,
            "from": previous.value,
            "to": to.value,
        },
    )
    return decision


# ---------- Celery ----------


def enqueue_series_analysis(series_id: uuid.UUID, trace_id: str) -> None:
    """把系列排入分析（管線在事件加入系列後呼叫）。"""
    celery_app.send_task(ANALYZE_SERIES_TASK, args=[str(series_id), trace_id])


async def _in_worker(series_ids: Sequence[str] | None, trace_id: str | None) -> int:
    from app.ai.runner.run import SqlRunStore, run_skill
    from app.pipeline.analyze_item import PIPELINE_PRINCIPAL, build_deps
    from app.worker.tasks.ingest import open_sessionmaker

    deps = build_deps()
    trace = trace_id or f"series-{uuid.uuid4().hex[:12]}"
    count = 0
    async with open_sessionmaker() as maker, maker() as session:

        async def call(payload: Mapping[str, Any]) -> SkillRunResult:
            result = await run_skill(
                deps.skills[S12],
                payload,
                provider=deps.provider,
                config=deps.config,
                store=SqlRunStore(session),
                trace_id=trace,
            )
            await session.commit()
            return result

        targets = (
            [uuid.UUID(s) for s in series_ids]
            if series_ids is not None
            else await stale_series(session)
        )
        for series_id in targets:
            done = await analyze_series(
                session, series_id, run=call, principal=PIPELINE_PRINCIPAL, trace_id=trace
            )
            await session.commit()
            count += done is not None
    return count


@celery_app.task(name=ANALYZE_SERIES_TASK)
def analyze_series_task(series_id: str, trace_id: str | None = None) -> None:
    """Celery 任務：分析一個系列（今天已分析過就略過）。"""
    asyncio.run(_in_worker([series_id], trace_id))


@celery_app.task(name=ANALYZE_STALE_SERIES_TASK)
def analyze_stale_task() -> None:
    """Celery 任務：分析今天還沒分析、且有變動的系列。"""
    asyncio.run(_in_worker(None, None))


def main(argv: Sequence[str] | None = None) -> int:
    """命令列：`python -m app.series.analysis stale`（分析今天還沒分析、且有變動的系列）。"""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["stale"]:
        print("用法：python -m app.series.analysis stale")
        return 2
    print(f"已分析 {asyncio.run(_in_worker(None, None))} 個系列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CONFIRMED_LABEL",
    "S12",
    "SPECULATIVE_LABEL",
    "AnalysisConflictError",
    "AnalysisError",
    "AnalysisInvalidError",
    "AnalysisNotFoundError",
    "CauseDecision",
    "SkillCall",
    "analyze_series",
    "analyze_series_task",
    "analyze_stale_task",
    "analyzed_today",
    "current_decisions",
    "decide_series_cause",
    "enqueue_series_analysis",
    "event_date",
    "latest_analysis",
    "location_names",
    "rename_series",
    "series_events",
    "stale_series",
    "taipei_date",
    "weekly_counts",
]
