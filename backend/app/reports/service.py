"""事件小報告（P4-T09）：S06 之後接上 S07 root-cause-analyst、S08 solution-planner。

何時產生（`after_assessment`，由管線在 S06 與通知同步之後呼叫）：
- 事件被查證為不實（status false）→ 不產生，既有報告標為失效（`event_false`）；
- 風險中以上（medium、high、critical）→ 排入產生報告；
- 風險低但事件已有有效報告 → 也排入重算，讓既有報告跟上事件的最新證據；
- 其他情況不產生，可在後台手動觸發（`POST /api/v1/admin/events/{id}/report`）。

報告內容（`generate_report`）：
- 可能的影響：最新一次 S06 的五個分項（分數由規則引擎計算），理由取自該次 S06 的輸出，
  evidence_ids 為 S06 引用的證據；沒有風險評估或 S06 沒有引用證據時不呈現。
- 原因：S07 的原因。只有至少一句逐字原句出自官方來源（`is_official_source`）的原因才算 confirmed，
  其他一律改判為 hypothesized（推測，尚未查證），沒有待查事項時補上預設的查證說明。
- 措施：S08 的措施，`cause_basis` 一律依改判後的原因重新填入（模型不能把推測改成確認）；
  指向不存在原因的措施視為一般措施。
- 每一項都必須有 evidence_ids 且全部屬於事件的證據，否則捨棄該項；文字一律經 `mask_pii()`。
- S07／S08 abstain 或失敗時該區塊沒有任何項目；S07 abstain 時 S08 仍以空的原因清單執行。

設計（AI 自行決定，見 P4-T09 驗收紀錄）：報告是獨立的 Celery 任務，不在分析管線的同一個任務裡
執行：S07、S08 是額外兩次模型呼叫，失敗不影響事件建立與通知；手動觸發與自動產生共用同一個任務。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.run import RunStatus, SkillRunResult
from app.alerts.pii import mask_pii
from app.core.security import Permission, require
from app.domain.states import EventStatus, VerificationStatus
from app.models.ai import AIAnalysisRun, RiskAssessment
from app.models.event import Event
from app.models.report import EventReport
from app.pipeline.analyze_item import (
    MAX_EVENT_EVIDENCE,
    MAX_TITLE_CHARS,
    PIPELINE_PRINCIPAL,
    PipelineDeps,
    ReportHook,
    _Context,
    _event_item_ids,
    _evidence,
    _location_name,
    build_deps,
)
from app.risk.engine import DIMENSIONS
from app.services.audit import record_audit
from app.verification.rules import is_official_source
from app.worker.celery_app import REPORT_EVENT_TASK, celery_app

S07 = "root-cause-analyst"
S08 = "solution-planner"

AUTO_LEVELS = frozenset({"medium", "high", "critical"})
"""風險達到這些分級時自動產生報告。"""
INVALID_EVENT_FALSE = "event_false"
HYPOTHESIZED_LABEL = "推測，尚未查證"
CONFIRMED_LABEL = "已確認"
ABSTAIN_MESSAGE = "證據不足，暫不分析"
FAILED_MESSAGE = "分析失敗，可稍後重新產生"
DEFAULT_TO_VERIFY = "向權責單位確認此原因是否屬實。"

Trigger = Literal["auto", "manual"]
ReportQueue = Callable[[uuid.UUID, str], None]

# S07／S08 的輸入只接受這四種查證狀態；monitoring、resolved 等視為 unverified（與管線相同）
_VERIFICATION_OF = {
    EventStatus.VERIFIED.value: VerificationStatus.VERIFIED,
    EventStatus.DISPUTED.value: VerificationStatus.DISPUTED,
    EventStatus.FALSE.value: VerificationStatus.FALSE,
}


# ---------- 何時產生 ----------


async def latest_report(session: AsyncSession, event_id: uuid.UUID) -> EventReport | None:
    """事件最新的一份報告（含已失效的）；沒有時為 None。"""
    return await session.scalar(
        select(EventReport)
        .where(EventReport.event_id == event_id)
        .order_by(EventReport.created_at.desc(), EventReport.id.desc())
        .limit(1)
    )


async def invalidate_reports(session: AsyncSession, event_id: uuid.UUID, reason: str) -> int:
    """把事件所有尚未失效的報告標為失效（不提交）；回傳筆數。"""
    result = await session.execute(
        update(EventReport)
        .where(EventReport.event_id == event_id, EventReport.invalidated_at.is_(None))
        .values(invalidated_at=datetime.now(UTC), invalidated_reason=reason)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def after_assessment(
    session: AsyncSession,
    event_id: uuid.UUID,
    level: str | None,
    trace_id: str,
    *,
    enqueue: ReportQueue,
) -> bool:
    """S06 之後決定是否排入產生報告；回傳是否已排入。事件不實時改為讓既有報告失效。"""
    event = await session.get(Event, event_id)
    if event is None or event.status == EventStatus.ARCHIVED.value:
        return False
    if event.status == EventStatus.FALSE.value:
        await invalidate_reports(session, event_id, INVALID_EVENT_FALSE)
        await session.commit()
        return False
    current = await latest_report(session, event_id)
    has_valid = current is not None and current.invalidated_at is None
    if level in AUTO_LEVELS or has_valid:
        enqueue(event_id, trace_id)
        return True
    return False


def make_report_hook(enqueue: ReportQueue) -> ReportHook:
    """管線用的 hook：`after_assessment` 綁定排入佇列的函式。"""

    async def hook(
        session: AsyncSession, event_id: uuid.UUID, level: str | None, trace_id: str
    ) -> None:
        await after_assessment(session, event_id, level, trace_id, enqueue=enqueue)

    return hook


def send_to_celery(event_id: uuid.UUID, trace_id: str) -> None:
    """把事件排入產生報告的 Celery 任務。"""
    celery_app.send_task(REPORT_EVENT_TASK, args=[str(event_id), trace_id])


# ---------- 整理 ----------


def _clean(text: Any) -> str:
    return mask_pii(str(text or "")).strip()


def _cited(ids: Any, known: set[str]) -> list[str]:
    """依序去重後的 evidence_ids；含不屬於事件的證據時回傳空清單（整項捨棄）。"""
    cited = list(dict.fromkeys(str(i) for i in ids or []))
    return cited if cited and set(cited) <= known else []


def _impacts(
    assessment: RiskAssessment | None, run: AIAnalysisRun | None, known: set[str]
) -> list[dict[str, Any]]:
    if assessment is None or run is None or not run.result:
        return []
    cited = _cited(run.result.get("evidence_ids"), known)
    dims = run.result.get("dimensions") or {}
    if not cited or not isinstance(dims, Mapping):
        return []
    items = []
    for name in DIMENSIONS:
        stored = assessment.dimensions.get(name)
        if not isinstance(stored, Mapping):
            continue
        given = dims.get(name)
        reason = _clean(given.get("reason")) if isinstance(given, Mapping) else ""
        items.append(
            {
                "dimension": name,
                "score": stored["score"],
                "uncertainty": stored["uncertainty"],
                "reason": reason or None,
                "evidence_ids": cited,
            }
        )
    return items


def _causes(
    output: Mapping[str, Any] | None, known: set[str], official: set[str]
) -> list[dict[str, Any]]:
    if output is None:
        return []
    causes = []
    for raw in output.get("causes") or []:
        cited = _cited(raw.get("evidence_ids"), known)
        description = _clean(raw.get("description"))
        if not cited or not description:
            continue
        quotes = [
            {"evidence_id": str(s["evidence_id"]), "quote": _clean(s.get("quote"))}
            for s in raw.get("evidence_spans") or []
            if str(s.get("evidence_id")) in known and _clean(s.get("quote"))
        ]
        confirmed = raw.get("basis") == "confirmed" and any(
            q["evidence_id"] in official for q in quotes
        )
        to_verify = _clean(raw.get("to_verify"))
        causes.append(
            {
                "cause_id": str(raw["cause_id"]),
                "description": description,
                "basis": "confirmed" if confirmed else "hypothesized",
                "label": CONFIRMED_LABEL if confirmed else HYPOTHESIZED_LABEL,
                "quotes": quotes,
                "to_verify": None if confirmed else (to_verify or DEFAULT_TO_VERIFY),
                "evidence_ids": cited,
            }
        )
    return causes


def _measures(
    output: Mapping[str, Any] | None, known: set[str], causes: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    if output is None:
        return []
    basis = {str(c["cause_id"]): str(c["basis"]) for c in causes}
    measures = []
    for raw in output.get("measures") or []:
        cited = _cited(raw.get("evidence_ids"), known)
        action = _clean(raw.get("action"))
        if not cited or not action:
            continue
        cause_id = raw.get("cause_id")
        cause_id = str(cause_id) if cause_id in basis else None
        unit = _clean(raw.get("responsible_unit")) if raw.get("audience") == "school" else ""
        measures.append(
            {
                "action": action,
                "audience": raw["audience"],
                "horizon": raw["horizon"],
                "priority": raw["priority"],
                "kind": raw["kind"],
                "cause_id": cause_id,
                "cause_basis": basis[cause_id] if cause_id else None,
                "responsible_unit": unit or None,
                "evidence_ids": cited,
            }
        )
    return measures


def _section(result: SkillRunResult) -> tuple[str, str | None, Mapping[str, Any] | None]:
    """（區塊狀態, abstain 原因, 可用的輸出）。"""
    output = result.output
    if result.status is RunStatus.SUCCEEDED and output is not None:
        if output.get("status") == "ok":
            return "ok", None, output
        return "abstained", output.get("abstain_reason"), None
    if result.status is RunStatus.ABSTAINED:
        return "abstained", result.abstain_reason, None
    return "failed", None, None


def _missing(output: Mapping[str, Any] | None, result: SkillRunResult) -> list[str]:
    source = output if output is not None else (result.output or {})
    texts = (_clean(t) for t in source.get("missing_information") or [])
    return [t for t in texts if t]


# ---------- 產生 ----------


def _official_ids(evidence: Iterable[Mapping[str, Any]]) -> set[str]:
    return {str(e["evidence_id"]) for e in evidence if is_official_source(str(e["source_code"]))}


async def generate_report(
    session: AsyncSession,
    event_id: uuid.UUID,
    deps: PipelineDeps,
    *,
    trigger: Trigger,
    trace_id: str | None = None,
) -> EventReport | None:
    """產生事件小報告並寫入；事件不實、已封存或沒有證據時不產生（回傳 None）。

    事件不存在時拋出 LookupError。
    """
    require(PIPELINE_PRINCIPAL, Permission.AI_RUN)
    ctx = _Context(session, deps, trace_id or uuid.uuid4().hex)
    event = await session.get(Event, event_id)
    if event is None:
        raise LookupError(f"事件 {event_id} 不存在")
    if event.status == EventStatus.FALSE.value:
        await invalidate_reports(session, event_id, INVALID_EVENT_FALSE)
        await session.commit()
        return None
    evidence = await _evidence(
        session, await _event_item_ids(session, event_id, MAX_EVENT_EVIDENCE)
    )
    if event.status == EventStatus.ARCHIVED.value or not evidence:
        return None
    known = {str(e["evidence_id"]) for e in evidence}
    official = _official_ids(evidence)

    assessment = await session.scalar(
        select(RiskAssessment)
        .where(RiskAssessment.event_id == event_id)
        .order_by(RiskAssessment.created_at.desc(), RiskAssessment.id.desc())
        .limit(1)
    )
    s06_run = (
        await session.get(AIAnalysisRun, assessment.ai_run_id)
        if assessment is not None and assessment.ai_run_id is not None
        else None
    )
    verification = _VERIFICATION_OF.get(event.status, VerificationStatus.UNVERIFIED)
    summary: dict[str, Any] = {
        "title": event.title[:MAX_TITLE_CHARS],
        "category": event.type,
        "occurrence_start_date": (
            event.occurrence_start_date.isoformat() if event.occurrence_start_date else None
        ),
        "location": await _location_name(session, event),
        "verification_status": verification.value,
    }

    s07 = await ctx.run(S07, {"evidence": evidence, "event": summary}, event_id=event_id)
    causes_status, causes_reason, causes_output = _section(s07)
    causes = _causes(causes_output, known, official)

    level = assessment.level if assessment is not None else None
    planner_causes = [
        {"cause_id": c["cause_id"], "description": c["description"][:150], "basis": c["basis"]}
        for c in causes
    ]
    s08 = await ctx.run(
        S08,
        {"evidence": evidence, "event": {**summary, "risk_level": level}, "causes": planner_causes},
        event_id=event_id,
    )
    measures_status, measures_reason, measures_output = _section(s08)

    report = EventReport(
        event_id=event_id,
        trigger=trigger,
        verification_status=verification.value,
        risk_level=level,
        impacts=_impacts(assessment, s06_run, known),
        causes_status=causes_status,
        causes_abstain_reason=causes_reason,
        causes=causes,
        missing_information=_missing(causes_output, s07),
        measures_status=measures_status,
        measures_abstain_reason=measures_reason,
        measures=_measures(measures_output, known, causes),
        risk_assessment_id=assessment.id if assessment is not None else None,
        root_cause_run_id=s07.run_id,
        solution_run_id=s08.run_id,
        trace_id=ctx.trace_id,
    )
    session.add(report)
    await session.flush()
    await record_audit(
        session,
        PIPELINE_PRINCIPAL,
        "report.generate",
        target=f"event:{event_id}",
        request_id=ctx.trace_id,
        metadata={
            "report_id": report.id,
            "trigger": trigger,
            "causes": causes_status,
            "measures": measures_status,
        },
    )
    await session.commit()
    return report


# ---------- Celery 任務 ----------


async def _in_worker(event_id: str, trace_id: str | None, trigger: Trigger) -> None:
    from app.worker.tasks.ingest import open_sessionmaker  # 避免 API 匯入時載入擷取模組

    deps = build_deps()
    async with open_sessionmaker() as maker, maker() as session:
        await generate_report(
            session, uuid.UUID(event_id), deps, trigger=trigger, trace_id=trace_id
        )


@celery_app.task(name=REPORT_EVENT_TASK)
def generate_report_task(
    event_id: str, trace_id: str | None = None, trigger: Trigger = "auto"
) -> None:
    """Celery 任務：產生事件小報告（trace_id 沿用管線或 API 的 request_id）。"""
    asyncio.run(_in_worker(event_id, trace_id, trigger))


__all__ = [
    "ABSTAIN_MESSAGE",
    "AUTO_LEVELS",
    "CONFIRMED_LABEL",
    "FAILED_MESSAGE",
    "HYPOTHESIZED_LABEL",
    "INVALID_EVENT_FALSE",
    "REPORT_EVENT_TASK",
    "ReportHook",
    "ReportQueue",
    "after_assessment",
    "generate_report",
    "invalidate_reports",
    "latest_report",
    "make_report_hook",
    "send_to_celery",
]
