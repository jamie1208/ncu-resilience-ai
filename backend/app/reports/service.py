"""事件小報告（P4-T09）：S06 之後接上 S07 root-cause-analyst、S08 solution-planner。

**P8-T13 起停用（ADR-0020 決策 3）**：管線不再排入產生報告（移除 `report_hook`），手動產生的端點
回 CONFLICT，產生報告的 Celery 任務不再註冊；原因、措施與改善建議只由系列分析（S12）產生。
既有的 `event_reports` 保留唯讀：後台事件頁顯示為「舊版事件小報告（已停用）」，事件被判為不實或
被合併時仍標為失效（`invalidate_reports`）。`generate_report` 保留為歷史行為的紀錄（測試以它建立
既有資料），正式環境沒有任何呼叫路徑。

報告內容（`generate_report`）：
- 可能的影響：最新一次 S06 的五個分項（分數由規則引擎計算），理由取自該次 S06 的輸出，
  evidence_ids 為 S06 引用的證據；沒有風險評估或 S06 沒有引用證據時不呈現。
- 原因：S07 的原因。只有至少一句逐字原句出自官方來源（`is_official_source`）的原因才算 confirmed，
  其他一律改判為 hypothesized（推測，尚未查證），沒有待查事項時補上預設的查證說明。
- 措施：S08 的措施，`cause_basis` 一律依改判後的原因重新填入（模型不能把推測改成確認）；
  指向不存在原因的措施視為一般措施。
- 每一項都必須有 evidence_ids 且全部屬於事件的證據，否則捨棄該項；文字一律經 `mask_pii()`。
- S07／S08 abstain 或失敗時該區塊沒有任何項目；S07 abstain 時 S08 仍以空的原因清單執行。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.run import RunStatus, SkillRunResult
from app.alerts.pii import mask_pii
from app.core.logging import request_id_var
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
    _Context,
    _event_item_ids,
    _evidence,
    _location_name,
)
from app.risk.engine import DIMENSIONS
from app.services.audit import record_audit
from app.verification.rules import is_official_source

S07 = "root-cause-analyst"
S08 = "solution-planner"

INVALID_EVENT_FALSE = "event_false"
HYPOTHESIZED_LABEL = "推測，尚未查證"
CONFIRMED_LABEL = "已確認"
ABSTAIN_MESSAGE = "證據不足，暫不分析"
FAILED_MESSAGE = "分析失敗，可稍後重新產生"
DEFAULT_TO_VERIFY = "向權責單位確認此原因是否屬實。"

Trigger = Literal["auto", "manual"]

# S07／S08 的輸入只接受這四種查證狀態；monitoring、resolved 等視為 unverified（與管線相同）
_VERIFICATION_OF = {
    EventStatus.VERIFIED.value: VerificationStatus.VERIFIED,
    EventStatus.DISPUTED.value: VerificationStatus.DISPUTED,
    EventStatus.FALSE.value: VerificationStatus.FALSE,
}


# ---------- 既有報告 ----------


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

    P8-T13 起正式環境不再呼叫（ADR-0020 決策 3），只保留給既有資料與測試。

    事件不存在時拋出 LookupError。
    """
    require(PIPELINE_PRINCIPAL, Permission.AI_RUN)
    ctx = _Context(session, deps, trace_id or request_id_var.get() or uuid.uuid4().hex)
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


__all__ = [
    "ABSTAIN_MESSAGE",
    "CONFIRMED_LABEL",
    "FAILED_MESSAGE",
    "HYPOTHESIZED_LABEL",
    "INVALID_EVENT_FALSE",
    "generate_report",
    "invalidate_reports",
    "latest_report",
]
