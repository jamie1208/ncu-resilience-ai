"""依改善建議評估效益（P8-T15，ADR-0019、ADR-0020 決策 6）：S14 impact-evaluator。

流程：使用者在後台勾選系列最新一次成功分析（S12）的改善建議 → 組裝現況 → S14 估計改善百分比區間
與改善前後情境 → 業務驗證 → 寫入 `impact_evaluations` → 稽核 `scenario.impact`。

輸入（`build_payload`）：
- `<untrusted_source>`：子事件標題與摘要（E1…，最近 30 起）、可能原因（c1…）、改善建議（m1…），
  一律經 `mask_pii()`；
- `<skill_input>`：系列類別與件數；子事件日期、代表地點、查證狀態；原因狀態（人工確認過的為
  confirmed，其餘 hypothesized）；每項建議是否被選、對象、期程、優先度、種類、針對的原因與改善追蹤
  狀態；最近 26 週的系列週件數與系列基準（`series_baseline`，算法同 P8-T09：最多 52 個完整週，
  進行中的週不列入）、同類別全校的 P8-T09 基準（參考）；不是來自本次建議的追蹤項目各狀態數量。

業務驗證（`check_output`，交給 Skill Runner 的 `check`，不通過就依 Runner 規則重送，最多 3 次，
仍失敗記為 invalid，介面顯示「這次評估不出來」）：
- ok：`0 ≤ low ≤ high ≤ improvement_cap`（`config/impact.yaml`）、`before`／`after` 不得為空、
  `risks` 至少 1 項、`covered_causes` 與 `uncovered_causes` 不重疊且合起來剛好是輸入的全部原因；
  abstain：百分比必須為 null；
- `overlaps` 只能列被選的建議（每組至少 2 項）；`suggestion` 只能是沒被選的建議；
- 有推測原因時，文字不得把推測寫成已確定（`ASSERTIVE`）；
- 個資與 evidence_id 存在由 Runner 的通用驗證檢查。

件數（ADR-0019 決策 2-4）：模型不寫件數；改善前後的件數由 `converted_counts` 以
基準 ×（1 − 百分比）換算；歷史件數少於 `min_observed` 或沒有基準時不顯示。

其他：
- 沒有勾選任何建議時拋出 `ImpactInputError`，不呼叫模型；勾選不存在的建議同樣拒絕；
- 同一系列、同一次分析、同一組選擇已有 ok／abstained 的結果時沿用，不重複呼叫（invalid、failed
  可以重試）；
- 系列分析更新後，舊分析的評估 `is_stale` 為 True；
- 只在管理端，結果不進入公開快照（ADR-0019 決策 1-4）；
- 本模組只 flush、不 commit（`run_skill` 的執行紀錄由 `SqlRunStore` 寫入同一個 session）。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.ai.llm.config import LLMConfig
from app.ai.runner.loader import Skill
from app.ai.runner.run import RunStatus, SqlRunStore, run_skill
from app.ai.runner.validate import Issue
from app.alerts.pii import mask_pii
from app.core.security import Principal
from app.models.impact import ImpactEvaluation
from app.models.response_action import ACTION_STATES, ResponseAction
from app.models.series import EventSeries, SeriesAnalysis
from app.resilience.recurrence import TAIPEI
from app.series.analysis import (
    CONFIRMED_LABEL,
    SPECULATIVE_LABEL,
    CauseDecision,
    current_decisions,
    event_date,
    latest_analysis,
    location_names,
    series_events,
    weekly_counts,
)
from app.services.audit import record_audit
from app.simulations.baseline import HISTORY_WEEKS, history_range, load_baseline

S14 = "impact-evaluator"
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "impact.yaml"
MAX_EVENTS = 30
LIST_LIMIT = 50
DIGITS = 1
REUSABLE = ("ok", "abstained")
_MEASURE_CODE = re.compile(r"^m([1-9][0-9]?)$")

ASSERTIVE = (
    "原因就是",
    "就是因為",
    "已證實",
    "證實是",
    "確定是",
    "確定就是",
    "肯定是",
    "一定是",
    "毫無疑問",
    "元兇",
    "罪魁禍首",
)
"""把推測寫成已確定的說法；有推測原因時，評估文字不得出現（`hypothesized_not_asserted`）。"""


# ---------- 設定 ----------


class ImpactConfig(BaseModel):
    """`config/impact.yaml` 的內容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    improvement_cap: int = Field(ge=0, le=100)
    window_days: int = Field(ge=1, le=730)
    min_observed: int = Field(ge=1)


def _read_config(path: Path) -> ImpactConfig:
    return ImpactConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@cache
def _default_config() -> ImpactConfig:
    return _read_config(DEFAULT_CONFIG)


def load_impact_config(path: Path | None = None) -> ImpactConfig:
    """讀取設定；預設檔案讀一次後快取。"""
    return _default_config() if path is None else _read_config(path)


# ---------- 純函式 ----------


def measure_code(index: int) -> str:
    """分析 `measures` 第 index 項（從 0 起）的代號。"""
    return f"m{index + 1}"


def measure_index(code: str) -> int | None:
    """代號 → 索引；格式不對時為 None。"""
    match = _MEASURE_CODE.match(code)
    return int(match.group(1)) - 1 if match else None


def output_texts(output: Mapping[str, Any]) -> list[str]:
    """評估輸出中所有自由文字（情境、理由、風險、重疊說明、補強理由）。"""
    texts = [output.get("before"), output.get("after"), output.get("reason")]
    texts += list(output.get("risks") or [])
    texts += [o.get("note") for o in output.get("overlaps") or [] if isinstance(o, Mapping)]
    suggestion = output.get("suggestion")
    if isinstance(suggestion, Mapping):
        texts.append(suggestion.get("reason"))
    return [str(t) for t in texts if t]


def asserts_hypothesis(texts: Iterable[str]) -> bool:
    """文字是否把推測寫成已確定。"""
    return any(phrase in text for text in texts for phrase in ASSERTIVE)


def improvement_valid(output: Mapping[str, Any], cap: int) -> bool:
    """改善百分比與狀態一致：ok 時 `0 ≤ low ≤ high ≤ cap` 且至少 1 項風險；abstain 時為 null。"""
    improvement = output.get("improvement")
    low = improvement.get("low") if isinstance(improvement, Mapping) else None
    high = improvement.get("high") if isinstance(improvement, Mapping) else None
    if output.get("status") != "ok":
        return low is None and high is None
    if not isinstance(low, int) or not isinstance(high, int):
        return False
    return 0 <= low <= high <= cap and bool(output.get("risks"))


@dataclass(frozen=True)
class SeriesBaseline:
    """系列在時間窗內的基準件數與依據。"""

    baseline: float | None
    observed: int
    weeks: int
    history_start: date | None
    history_end: date | None
    window_days: int

    def to_dict(self) -> dict[str, Any]:
        """存進評估紀錄的依據。"""
        return {
            "baseline": self.baseline,
            "observed": self.observed,
            "history_weeks": self.weeks,
            "history_start": self.history_start.isoformat() if self.history_start else None,
            "history_end": self.history_end.isoformat() if self.history_end else None,
            "window_days": self.window_days,
        }


def series_baseline(
    dates: Sequence[date], *, as_of: date, window_days: int, weeks: int = HISTORY_WEEKS
) -> SeriesBaseline:
    """系列基準：歷史期間（同 P8-T09）的件數 ÷ 週數 × 時間窗天數 ÷ 7。"""
    span = history_range(as_of, min(dates, default=None), weeks)
    if span is None:
        return SeriesBaseline(None, 0, 0, None, None, window_days)
    start, end = span
    count = (end - start).days // 7 + 1
    observed = sum(1 for d in dates if start <= d <= end)
    value = round(observed / count * window_days / 7, DIGITS)
    return SeriesBaseline(value, observed, count, start, end, window_days)


def converted_counts(row: ImpactEvaluation) -> dict[str, Any]:
    """程式換算的改善前後件數：基準 ×（1 − 百分比）；件數不足或沒有百分比時不換算。"""
    counts = dict(row.counts or {})
    baseline = counts.get("baseline")
    shown = baseline is not None and int(counts.get("observed") or 0) >= int(
        counts.get("min_observed") or 1
    )
    after_low = after_high = None
    if (
        shown
        and baseline is not None
        and row.improvement_low is not None
        and row.improvement_high is not None
    ):
        after_low = round(float(baseline) * (1 - row.improvement_high / 100), DIGITS)
        after_high = round(float(baseline) * (1 - row.improvement_low / 100), DIGITS)
    return {**counts, "shown": shown, "after_low": after_low, "after_high": after_high}


# ---------- 錯誤 ----------


class ImpactError(Exception):
    """效益評估相關操作失敗。"""


class ImpactNotFoundError(ImpactError):
    """系列或評估不存在。"""


class ImpactConflictError(ImpactError):
    """系列還沒有可用的分析，或指定的分析不是最新的。"""


class ImpactInputError(ImpactError):
    """輸入不合格；`field` 為出問題的欄位。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


# ---------- 輸入組裝 ----------


@dataclass(frozen=True)
class Evaluator:
    """呼叫 S14 需要的相依。"""

    skill: Skill
    provider: LLMProvider
    config: LLMConfig


@dataclass(frozen=True)
class _Context:
    payload: dict[str, Any]
    causes: dict[str, dict[str, Any]]
    """cause_id → 原因（含 decision 與標籤），保存結果時補上描述。"""
    measures: list[Mapping[str, Any]]
    selected: frozenset[str]
    counts: dict[str, Any]

    @property
    def unselected(self) -> frozenset[str]:
        return frozenset(measure_code(i) for i in range(len(self.measures))) - self.selected

    @property
    def has_hypothesis(self) -> bool:
        return any(c["decision"] != CauseDecision.CONFIRMED.value for c in self.causes.values())


def _clean(value: Any, limit: int) -> str:
    return mask_pii(str(value or "")).strip()[:limit]


def _selection(codes: Iterable[str]) -> list[str]:
    unique = {str(c).strip() for c in codes}
    return sorted(unique, key=lambda c: (measure_index(c) is None, measure_index(c) or 0, c))


async def build_payload(
    session: AsyncSession,
    series: EventSeries,
    analysis: SeriesAnalysis,
    selected: Collection[str],
    *,
    as_of: date,
    config: ImpactConfig,
) -> _Context:
    """組裝 S14 的輸入（見模組說明）。"""
    events = await series_events(session, series.id)
    recent = events[-MAX_EVENTS:]
    names = await location_names(session, recent)
    codes = {e.id: f"E{i}" for i, e in enumerate(recent, start=1)}
    evidence: list[dict[str, Any]] = []
    items = []
    for event in recent:
        code = codes[event.id]
        evidence.append(
            {
                "evidence_id": code,
                "source_code": "series-event",
                "title": _clean(event.title, 300) or None,
                "text": _clean(event.summary or event.title, 2000) or "（沒有摘要）",
            }
        )
        items.append(
            {
                "event_id": code,
                "occurrence_start_date": (
                    event.occurrence_start_date.isoformat() if event.occurrence_start_date else None
                ),
                "location": _clean(names[event.id], 100) if event.id in names else None,
                "verification_status": (
                    event.status if event.status in ("verified", "disputed") else "unverified"
                ),
            }
        )

    decisions = await current_decisions(session, analysis.id)
    causes: dict[str, dict[str, Any]] = {}
    cause_items = []
    for raw in analysis.causes or []:
        cause_id = str(raw.get("cause_id"))
        decision = decisions.get(cause_id)
        confirmed = decision is not None and decision.decision == CauseDecision.CONFIRMED.value
        status = CauseDecision.CONFIRMED if confirmed else CauseDecision.HYPOTHESIZED
        description = _clean(raw.get("description"), 300)
        causes[cause_id] = {
            "cause_id": cause_id,
            "description": description,
            "decision": status.value,
            "label": CONFIRMED_LABEL if confirmed else SPECULATIVE_LABEL,
        }
        evidence.append(
            {
                "evidence_id": cause_id,
                "source_code": "series-cause",
                "title": None,
                "text": description or "（沒有描述）",
            }
        )
        cause_items.append(
            {
                "cause_id": cause_id,
                "status": status.value,
                "event_ids": [
                    codes[uuid.UUID(e)] for e in raw.get("event_ids") or [] if uuid.UUID(e) in codes
                ],
            }
        )

    actions = list(
        await session.scalars(select(ResponseAction).where(ResponseAction.series_id == series.id))
    )
    tracked = {
        a.measure_index: a.state
        for a in actions
        if a.analysis_id == analysis.id and a.measure_index is not None
    }
    others = {state: 0 for state in ACTION_STATES}
    for a in actions:
        if not (a.analysis_id == analysis.id and a.measure_index is not None):
            others[a.state] += 1
    measure_items = []
    for i, raw in enumerate(analysis.measures or []):
        code = measure_code(i)
        evidence.append(
            {
                "evidence_id": code,
                "source_code": "series-measure",
                "title": None,
                "text": _clean(raw.get("action"), 2000) or "（沒有內容）",
            }
        )
        measure_items.append(
            {
                "measure_id": code,
                "selected": code in selected,
                "audience": raw.get("audience"),
                "horizon": raw.get("horizon"),
                "priority": raw.get("priority"),
                "kind": raw.get("kind"),
                "cause_id": raw.get("cause_id") if raw.get("cause_id") in causes else None,
                "tracking_state": tracked.get(i),
            }
        )

    dates = [event_date(e) for e in events]
    base = series_baseline(dates, as_of=as_of, window_days=config.window_days)
    category = await load_baseline(
        session, as_of=as_of, window_days=config.window_days, event_type=series.category
    )
    counts = {
        **base.to_dict(),
        "category_baseline": category.baseline,
        "min_observed": config.min_observed,
    }
    payload = {
        "evidence": evidence,
        "series": {
            "category": series.category,
            "title": _clean(series.title, 300),
            "event_count": len(events),
        },
        "events": items,
        "causes": cause_items,
        "measures": measure_items,
        "counts": {
            "weekly_counts": [
                {"period_start": c.period_start.isoformat(), "count": c.count}
                for c in weekly_counts(dates, as_of)
            ],
            "window_days": config.window_days,
            "baseline": base.baseline,
            "observed": base.observed,
            "history_weeks": base.weeks,
            "category_baseline": category.baseline,
        },
        "tracking": others,
    }
    return _Context(payload, causes, list(analysis.measures or []), frozenset(selected), counts)


# ---------- 業務驗證 ----------


def check_output(output: Mapping[str, Any], ctx: _Context, cap: int) -> list[Issue]:
    """S14 輸出的業務驗證（見模組說明）；問題不含輸出內容。"""
    issues: list[Issue] = []

    def bad(detail: str) -> None:
        issues.append(Issue("business", detail))

    ok = output.get("status") == "ok"
    if not improvement_valid(output, cap):
        bad(f"改善百分比必須在 0 到 {cap} 之間、下限不大於上限，ok 時至少 1 項風險")
    if ok:
        if not output.get("before") or not output.get("after"):
            bad("ok 時必須有改善前與改善後情境")
        covered = {str(c) for c in output.get("covered_causes") or []}
        uncovered = {str(c) for c in output.get("uncovered_causes") or []}
        if (covered | uncovered) - set(ctx.causes):
            bad("covered_causes／uncovered_causes 引用了不存在的原因")
        if covered & uncovered:
            bad("同一個原因不能同時涵蓋與未涵蓋")
        if (covered | uncovered) != set(ctx.causes):
            bad("covered_causes 與 uncovered_causes 合起來必須是全部原因")
    for i, overlap in enumerate(output.get("overlaps") or []):
        ids = {str(m) for m in overlap.get("measure_ids") or []}
        if len(ids) < 2 or not ids <= ctx.selected:
            bad(f"overlaps/{i}：只能列 2 項以上被選的建議")
    suggestion = output.get("suggestion")
    suggested = suggestion.get("measure_id") if isinstance(suggestion, Mapping) else None
    if suggested is not None and str(suggested) not in ctx.unselected:
        bad("suggestion 只能是沒有被選的建議")
    if ctx.has_hypothesis and asserts_hypothesis(output_texts(output)):
        bad("推測原因不得寫成已確定")
    return issues


def _result(output: Mapping[str, Any], ctx: _Context) -> dict[str, Any]:
    def cause_list(key: str) -> list[dict[str, Any]]:
        ids = [str(c) for c in output.get(key) or []]
        return [ctx.causes[c] for c in sorted(set(ids)) if c in ctx.causes]

    def action(code: str) -> str | None:
        index = measure_index(code)
        if index is None or index >= len(ctx.measures):
            return None
        return _clean(ctx.measures[index].get("action"), 300)

    suggestion = output.get("suggestion") or {}
    suggested = suggestion.get("measure_id")
    return {
        "before": _clean(output.get("before"), 300) or None,
        "after": _clean(output.get("after"), 300) or None,
        "reason": _clean(output.get("reason"), 300) or None,
        "covered_causes": cause_list("covered_causes"),
        "uncovered_causes": cause_list("uncovered_causes"),
        "overlaps": [
            {
                "measure_ids": _selection(o.get("measure_ids") or []),
                "note": _clean(o.get("note"), 150),
            }
            for o in output.get("overlaps") or []
        ],
        "risks": [_clean(r, 150) for r in output.get("risks") or [] if str(r).strip()],
        "suggestion": (
            {
                "measure_id": str(suggested),
                "action": action(str(suggested)),
                "reason": _clean(suggestion.get("reason"), 150) or None,
            }
            if suggested is not None
            else None
        ),
        "evidence_ids": [str(e) for e in output.get("evidence_ids") or []],
    }


# ---------- 評估 ----------


@dataclass(frozen=True)
class ImpactOutcome:
    """一次評估請求的結果；`reused` 表示沿用了同一次分析、同一組選擇的既有結果。"""

    evaluation: ImpactEvaluation
    reused: bool


async def evaluate_impact(
    session: AsyncSession,
    principal: Principal,
    series_id: uuid.UUID,
    measure_ids: Sequence[str],
    evaluator: Evaluator,
    *,
    analysis_id: uuid.UUID | None = None,
    config: ImpactConfig | None = None,
    as_of: date | None = None,
    trace_id: str | None = None,
) -> ImpactOutcome:
    """評估所選建議的效益並保存（flush，不 commit）。

    沒有勾選或勾選不存在的建議時拋出 `ImpactInputError`；系列不存在拋出 `ImpactNotFoundError`；
    系列沒有成功的分析、或 `analysis_id` 不是最新一次時拋出 `ImpactConflictError`。
    """
    selected = _selection(measure_ids)
    if not selected:
        raise ImpactInputError("measure_ids", "請至少選一項改善建議")
    config = config or load_impact_config()
    series = await session.get(EventSeries, series_id)
    if series is None:
        raise ImpactNotFoundError("找不到系列")
    analysis = await latest_analysis(session, series_id)
    if analysis is None:
        raise ImpactConflictError("系列還沒有可用的分析，無法評估")
    if analysis_id is not None and analysis_id != analysis.id:
        raise ImpactConflictError("系列分析已經更新，請重新整理後再選擇建議")
    count = len(analysis.measures or [])
    if any((measure_index(c) is None or (measure_index(c) or 0) >= count) for c in selected):
        raise ImpactInputError("measure_ids", "選擇了不存在的改善建議")
    key = ",".join(selected)

    existing = await session.scalar(
        select(ImpactEvaluation)
        .where(
            ImpactEvaluation.series_id == series_id,
            ImpactEvaluation.analysis_id == analysis.id,
            ImpactEvaluation.selection_key == key,
            ImpactEvaluation.status.in_(REUSABLE),
        )
        .order_by(ImpactEvaluation.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return ImpactOutcome(existing, reused=True)

    today = as_of or datetime.now(TAIPEI).date()
    ctx = await build_payload(session, series, analysis, selected, as_of=today, config=config)
    cap = config.improvement_cap
    run = await run_skill(
        evaluator.skill,
        ctx.payload,
        provider=evaluator.provider,
        config=evaluator.config,
        store=SqlRunStore(session),
        trace_id=trace_id,
        check=lambda output: check_output(output, ctx, cap),
    )
    output = run.output
    row = ImpactEvaluation(
        series_id=series_id,
        analysis_id=analysis.id,
        selection_key=key,
        selected=selected,
        status="failed",
        cap=cap,
        counts=ctx.counts,
        ai_run_id=run.run_id,
        created_by=principal.actor,
    )
    if run.status is RunStatus.SUCCEEDED and output is not None:
        improvement = output["improvement"]
        row.status = "ok"
        row.improvement_low, row.improvement_high = improvement["low"], improvement["high"]
        row.result = _result(output, ctx)
    elif run.status is RunStatus.ABSTAINED and output is not None:
        row.status = "abstained"
        row.abstain_reason = str(output.get("abstain_reason") or "insufficient_evidence")[:64]
        row.result = None
    elif run.status is RunStatus.ABSTAINED:
        row.status, row.abstain_reason = "invalid", (run.abstain_reason or "invalid_output")[:64]
    else:
        row.abstain_reason = (run.error_type or "failed")[:64]
    session.add(row)
    await session.flush()
    await record_audit(
        session,
        principal,
        "scenario.impact",
        target=f"impact_evaluation:{row.id}",
        request_id=trace_id[:64] if trace_id else None,
        metadata={
            "series_id": str(series_id),
            "analysis_id": str(analysis.id),
            "selected": len(selected),
            "status": row.status,
        },
    )
    return ImpactOutcome(row, reused=False)


# ---------- 查詢 ----------


async def latest_analysis_ids(
    session: AsyncSession, series_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """系列 → 最新一次成功分析的 id。"""
    out = {}
    for series_id in set(series_ids):
        latest = await latest_analysis(session, series_id)
        if latest is not None:
            out[series_id] = latest.id
    return out


def is_stale(row: ImpactEvaluation, latest: Mapping[uuid.UUID, uuid.UUID]) -> bool:
    """評估所依據的分析已不是系列最新一次成功的分析。"""
    return latest.get(row.series_id) != row.analysis_id


async def get_evaluation(session: AsyncSession, evaluation_id: uuid.UUID) -> ImpactEvaluation:
    """一次評估；不存在拋出 `ImpactNotFoundError`。"""
    row = await session.get(ImpactEvaluation, evaluation_id)
    if row is None:
        raise ImpactNotFoundError("找不到評估")
    return row


async def list_evaluations(
    session: AsyncSession, *, series_id: uuid.UUID | None = None
) -> list[ImpactEvaluation]:
    """最近的評估（最多 50 筆，新到舊），可依系列篩選。"""
    stmt = select(ImpactEvaluation).order_by(
        ImpactEvaluation.created_at.desc(), ImpactEvaluation.id
    )
    if series_id is not None:
        stmt = stmt.where(ImpactEvaluation.series_id == series_id)
    return list((await session.scalars(stmt.limit(LIST_LIMIT))).all())


__all__ = [
    "ASSERTIVE",
    "S14",
    "Evaluator",
    "ImpactConfig",
    "ImpactConflictError",
    "ImpactError",
    "ImpactInputError",
    "ImpactNotFoundError",
    "ImpactOutcome",
    "SeriesBaseline",
    "asserts_hypothesis",
    "build_payload",
    "check_output",
    "converted_counts",
    "evaluate_impact",
    "get_evaluation",
    "improvement_valid",
    "is_stale",
    "latest_analysis_ids",
    "list_evaluations",
    "load_impact_config",
    "measure_code",
    "measure_index",
    "output_texts",
    "series_baseline",
]
