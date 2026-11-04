"""S09 trend-forecaster 管線（P7-T06）：把 P6-T03 的時序統計交給 S09 解釋。

流程：`refresh_views` → `load_counts` 與 `detect_changes` 組出 `series` → `load_coverage`
算出 `coverage` → 取異常週期中的事件標題與摘要（經 `mask_pii()`）為 `evidence` →
經 Skill Runner 呼叫 S09（結果寫入 ai_analysis_runs）→ 稽核 `trend_forecast.run`。

- 數字（件數、期望值、p 值、覆蓋率）全部由程式計算，模型只解釋 `anomaly` 不為 null 的訊號；
- 覆蓋率 low → insufficient_data、沒有異常 → no_anomaly，兩者都不呼叫模型（與 S09 的
  abstain 規則同序：先看覆蓋率，再看異常）；
- 統計期間：截止日（預設台北昨日）往前 `WINDOW_PERIODS` 期，但不早於最早的核准來源新增日
  所在的週期（系統開始擷取前的 0 件不是真的 0 件）；
- 訊號：每個類別的全校序列與每個（類別, 地點）序列都做突增偵測；最近 `RECENT_PERIODS` 期內
  有突增者優先（p 值小的在前），其餘名額放件數最多的全校序列供對照，最多 6 個；
- 最近一次解讀以稽核紀錄為準；views 只在觸發時重新整理，查詢時以同一截止日重算輸入，
  數字與當次相同。只在管理端使用，不經公開端（ADR-0015）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.ai.llm.config import LLMConfig
from app.ai.runner.loader import Skill
from app.ai.runner.run import RunStatus, SqlRunStore, run_skill
from app.alerts.pii import mask_pii
from app.analytics.coverage import TAIPEI, Coverage, load_coverage, taipei_date
from app.analytics.timeseries import (
    Anomaly,
    CountRow,
    DetectionParams,
    Frequency,
    PeriodCount,
    count_series,
    detect_changes,
    load_counts,
    period_start,
    periods,
    refresh_views,
)
from app.core.security import Principal
from app.ingestion.types import AccessPolicy
from app.models.ai import AIAnalysisRun
from app.models.audit import AuditLog
from app.models.event import Event, EventEvidence, Location
from app.models.source import Source, SourceItem
from app.services.audit import record_audit

SKILL_NAME = "trend-forecaster"
AUDIT_ACTION = "trend_forecast.run"
# 與 skills/trend-forecaster/schemas/input.schema.json 同步
EVENT_TYPES = frozenset(
    {"facility", "weather", "security", "traffic", "health", "fire", "academic", "infosec"}
)
WINDOW_PERIODS: dict[Frequency, int] = {"week": 26, "month": 12}
RECENT_PERIODS = 4
MAX_SIGNALS = 6
MAX_EVIDENCE = 12
MAX_LOCATION_LENGTH = 60
UNKNOWN_SOURCE = "unknown"


class Outcome(StrEnum):
    """一次趨勢解讀的結果。"""

    EXPLAINED = "explained"  # S09 成功解釋
    ABSTAINED = "abstained"  # S09 自己 abstain（例如證據不足）
    FAILED = "failed"  # S09 失敗（無效輸出、費用上限、供應商錯誤）
    NO_ANOMALY = "no_anomaly"  # 沒有異常，不呼叫模型
    INSUFFICIENT_DATA = "insufficient_data"  # 覆蓋率 low，不呼叫模型


_RUN_OUTCOME = {
    RunStatus.SUCCEEDED: Outcome.EXPLAINED,
    RunStatus.ABSTAINED: Outcome.ABSTAINED,
    RunStatus.FAILED: Outcome.FAILED,
}


@dataclass(frozen=True)
class Forecaster:
    """呼叫 S09 需要的相依。"""

    skill: Skill
    provider: LLMProvider
    config: LLMConfig


@dataclass(frozen=True)
class Signal:
    """交給 S09 的一個件數序列。"""

    signal_id: str
    event_type: str
    location: str | None
    """已經過個資遮蔽；None 表示全校合計。"""
    counts: tuple[PeriodCount, ...]
    anomaly: Anomaly | None
    evidence_ids: tuple[str, ...] = ()

    def to_input(self) -> dict[str, Any]:
        """S09 輸入 `series` 的一項。"""
        a = self.anomaly
        return {
            "signal_id": self.signal_id,
            "event_type": self.event_type,
            "location": self.location,
            "counts": [
                {"period_start": c.period_start.isoformat(), "count": c.count} for c in self.counts
            ],
            "anomaly": None
            if a is None
            else {
                "period_start": a.period_start.isoformat(),
                "observed": a.observed,
                "expected": a.expected,
                "p_value": a.p_value,
            },
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class EvidenceItem:
    """異常週期中的一個事件。"""

    event_id: uuid.UUID
    source_code: str
    title: str
    text: str
    published_at: datetime | None

    @property
    def evidence_id(self) -> str:
        """S09 的 evidence_id 即事件 id。"""
        return str(self.event_id)

    def to_input(self) -> dict[str, Any]:
        """S09 輸入 `evidence` 的一項。"""
        return {
            "evidence_id": self.evidence_id,
            "source_code": self.source_code,
            "title": self.title,
            "text": self.text,
            "published_at": None if self.published_at is None else self.published_at.isoformat(),
        }


@dataclass(frozen=True)
class ForecastInput:
    """S09 的完整輸入。"""

    freq: Frequency
    as_of: date
    start: date
    """序列第一期的起日。"""
    signals: tuple[Signal, ...]
    coverage: Coverage
    evidence: tuple[EvidenceItem, ...]

    @property
    def anomalous(self) -> tuple[Signal, ...]:
        """有異常的訊號。"""
        return tuple(s for s in self.signals if s.anomaly is not None)

    def payload(self) -> dict[str, Any]:
        """Skill Runner 的輸入（evidence 由 Runner 包在 `<untrusted_source>` 中）。"""
        return {
            "evidence": [e.to_input() for e in self.evidence],
            "freq": self.freq,
            "as_of": self.as_of.isoformat(),
            "series": [s.to_input() for s in self.signals],
            "coverage": self.coverage.to_input(),
        }


@dataclass(frozen=True)
class TrendForecast:
    """一次趨勢解讀：輸入、結果與 S09 的執行紀錄（沒有呼叫模型時為 None）。"""

    input: ForecastInput
    outcome: Outcome
    ai_run: AIAnalysisRun | None
    created_by: str
    created_at: datetime


# ---------- 訊號 ----------


def window_start(as_of: date, freq: Frequency, earliest: date | None) -> date:
    """序列第一期的起日：往前 `WINDOW_PERIODS` 期，不早於 `earliest` 所在的週期。"""
    start = period_start(as_of, freq)
    for _ in range(WINDOW_PERIODS[freq] - 1):
        start = period_start(start - timedelta(days=1), freq)
    if earliest is not None:
        start = max(start, period_start(earliest, freq))
    return min(start, period_start(as_of, freq))


def build_signals(
    rows: Sequence[CountRow],
    freq: Frequency,
    start: date,
    as_of: date,
    params: DetectionParams | None = None,
) -> list[Signal]:
    """組出最多 `MAX_SIGNALS` 個訊號（不含 evidence_ids），代號依序為 s1…s6。"""
    rows = [r for r in rows if r.event_type in EVENT_TYPES]
    recent = periods(start, as_of, freq)[-RECENT_PERIODS:][0]
    keys: set[tuple[str, str | None]] = {(r.event_type, None) for r in rows}
    keys |= {
        (r.event_type, r.location_name)
        for r in rows
        if r.location_name is not None and len(r.location_name) <= MAX_LOCATION_LENGTH
    }

    flagged: list[tuple[tuple[float, int, str, str], Signal]] = []
    totals: list[tuple[tuple[int, str], Signal]] = []
    for event_type, location in keys:
        series = count_series(rows, freq, start, as_of, event_type=event_type, location=location)
        found = detect_changes(series, params)
        latest = found[-1] if found and found[-1].period_start >= recent else None
        signal = Signal("", event_type, location, tuple(series), latest)
        if latest is not None:
            key = (latest.p_value, -latest.observed, event_type, location or "")
            flagged.append((key, signal))
        elif location is None:
            totals.append(((-sum(c.count for c in series), event_type), signal))

    ordered = [s for _, s in sorted(flagged, key=lambda kv: kv[0])]
    ordered += [s for _, s in sorted(totals, key=lambda kv: kv[0])]
    return [
        Signal(f"s{i + 1}", s.event_type, s.location, s.counts, s.anomaly)
        for i, s in enumerate(ordered[:MAX_SIGNALS])
    ]


def _next_period(start: date, freq: Frequency) -> date:
    return periods(start, start + timedelta(days=31 if freq == "month" else 7), freq)[1]


# ---------- 資料庫 ----------


async def _earliest_source_date(session: AsyncSession) -> date | None:
    first = await session.scalar(
        select(func.min(Source.created_at)).where(
            Source.access_policy == AccessPolicy.APPROVED.value
        )
    )
    return None if first is None else taipei_date(first)


async def _period_events(
    session: AsyncSession, signal: Signal, freq: Frequency
) -> list[tuple[Event, str | None]]:
    """異常週期中屬於這個訊號的事件（排除查證為不實者），依發生日與建立時間排序。"""
    if signal.anomaly is None:
        return []
    first = signal.anomaly.period_start
    occurred = func.coalesce(
        Event.occurrence_start_date, cast(func.timezone("Asia/Taipei", Event.created_at), Date)
    )
    rows = await session.execute(
        select(Event, Location.name)
        .outerjoin(Location, Location.id == Event.location_id)
        .where(
            Event.type == signal.event_type,
            Event.status != "false",
            occurred >= first,
            occurred < _next_period(first, freq),
        )
        .order_by(occurred, Event.created_at, Event.id)
    )
    return [
        (event, name)
        for event, name in rows.all()
        if signal.location is None or (name is not None and mask_pii(name) == signal.location)
    ]


async def _primary_sources(
    session: AsyncSession, event_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, datetime | None]]:
    """每個事件最早連結的來源文章：(來源代碼, 發布時間)。"""
    if not event_ids:
        return {}
    rows = await session.execute(
        select(EventEvidence.event_id, Source.code, SourceItem.published_at)
        .join(SourceItem, SourceItem.id == EventEvidence.source_item_id)
        .join(Source, Source.id == SourceItem.source_id)
        .where(EventEvidence.event_id.in_(event_ids))
        .order_by(EventEvidence.event_id, EventEvidence.created_at, SourceItem.id)
    )
    out: dict[uuid.UUID, tuple[str, datetime | None]] = {}
    for event_id, code, published_at in rows.all():
        out.setdefault(event_id, (code, published_at))
    return out


async def _attach_evidence(
    session: AsyncSession, signals: Sequence[Signal], freq: Frequency
) -> tuple[list[Signal], list[EvidenceItem]]:
    """輪流從各異常訊號取事件，總數最多 `MAX_EVIDENCE` 個；同一事件可屬於多個訊號。"""
    pools: dict[str, list[Event]] = {}
    for s in signals:
        if s.anomaly is not None:
            pools[s.signal_id] = [e for e, _ in await _period_events(session, s, freq)]

    chosen: dict[uuid.UUID, Event] = {}
    depth = 0
    while len(chosen) < MAX_EVIDENCE and any(depth < len(p) for p in pools.values()):
        for pool in pools.values():
            if depth < len(pool) and len(chosen) < MAX_EVIDENCE:
                chosen.setdefault(pool[depth].id, pool[depth])
        depth += 1

    sources = await _primary_sources(session, list(chosen))
    evidence = []
    for event in chosen.values():
        code, published_at = sources.get(event.id, (UNKNOWN_SOURCE, None))
        title = mask_pii(event.title)[:300]
        evidence.append(
            EvidenceItem(
                event_id=event.id,
                source_code=code,
                title=title,
                text=(mask_pii(event.summary) if event.summary else title)[:4000],
                published_at=published_at,
            )
        )
    attached = [
        Signal(
            s.signal_id,
            s.event_type,
            s.location,
            s.counts,
            s.anomaly,
            tuple(str(e.id) for e in pools.get(s.signal_id, []) if e.id in chosen),
        )
        for s in signals
    ]
    return attached, evidence


async def build_input(
    session: AsyncSession,
    *,
    freq: Frequency,
    as_of: date,
    params: DetectionParams | None = None,
) -> ForecastInput:
    """以目前的 materialized views 與 ingestion_runs 組出 S09 輸入（不重新整理 views）。"""
    earliest = await _earliest_source_date(session)
    start = window_start(as_of, freq, earliest)
    rows = await load_counts(session, freq, start=start, end=as_of)
    signals = build_signals(rows, freq, start, as_of, params)
    signals, evidence = await _attach_evidence(session, signals, freq)
    coverage = await load_coverage(session, max(start, earliest or start), as_of)
    return ForecastInput(freq, as_of, start, tuple(signals), coverage, tuple(evidence))


def default_as_of(now: datetime) -> date:
    """預設截止日：台北時間昨日（當天的擷取與事件尚未完整）。"""
    return now.astimezone(TAIPEI).date() - timedelta(days=1)


async def run_forecast(
    session: AsyncSession,
    principal: Principal,
    forecaster: Forecaster,
    *,
    freq: Frequency,
    as_of: date,
    trace_id: str | None = None,
) -> TrendForecast:
    """執行一次趨勢解讀並記錄（flush，不 commit）。"""
    await refresh_views(session)
    data = await build_input(session, freq=freq, as_of=as_of)
    ai_run: AIAnalysisRun | None = None
    if data.coverage.level == "low":
        outcome = Outcome.INSUFFICIENT_DATA
    elif not data.anomalous:
        outcome = Outcome.NO_ANOMALY
    else:
        result = await run_skill(
            forecaster.skill,
            data.payload(),
            provider=forecaster.provider,
            config=forecaster.config,
            store=SqlRunStore(session),
            trace_id=trace_id,
        )
        outcome = _RUN_OUTCOME[result.status]
        ai_run = await session.get(AIAnalysisRun, result.run_id)

    log = await record_audit(
        session,
        principal,
        AUDIT_ACTION,
        target=None if ai_run is None else f"ai_analysis_run:{ai_run.id}",
        metadata={
            "freq": freq,
            "as_of": as_of.isoformat(),
            "outcome": outcome.value,
            "signals": len(data.signals),
            "anomalies": len(data.anomalous),
            "coverage_level": data.coverage.level,
            "coverage_ratio": data.coverage.ratio,
            "ai_run_id": None if ai_run is None else str(ai_run.id),
        },
    )
    return TrendForecast(data, outcome, ai_run, log.actor, log.created_at)


async def latest_forecast(session: AsyncSession) -> TrendForecast | None:
    """最近一次趨勢解讀；從未執行時為 None。"""
    log = await session.scalar(
        select(AuditLog)
        .where(AuditLog.action == AUDIT_ACTION)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    if log is None:
        return None
    meta = log.meta
    freq: Frequency = "month" if meta.get("freq") == "month" else "week"
    data = await build_input(session, freq=freq, as_of=date.fromisoformat(meta["as_of"]))
    ai_id = meta.get("ai_run_id")
    ai_run = None if ai_id is None else await session.get(AIAnalysisRun, uuid.UUID(ai_id))
    return TrendForecast(data, Outcome(meta["outcome"]), ai_run, log.actor, log.created_at)
