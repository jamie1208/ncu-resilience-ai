"""執行 Skill（P2-T04）。

流程：輸入驗證 → 組提示 → 費用檢查 → 呼叫模型 → 輸出驗證 → 重試或 abstain → 寫紀錄。

使用者 2026-10-19 決定：
- 最多 3 次呼叫，無效輸出與暫時性失敗共用：
  - 無效輸出（JSON、schema、業務規則、evidence、個資，或輸出未完成）立即重送；3 次都無效 → abstain；
  - 暫時性失敗（逾時、限流、5xx）間隔 2、4 秒後重送（有 Retry-After 依它）；最後仍失敗 → failed；
- 不可重試的錯誤（金鑰、請求錯誤）直接 failed；模型拒答 → abstain（model_refusal）；
- 送出前檢查每次與每日費用上限，超過就不送 → failed（cost_limit）；
- 紀錄不存提示詞、來源原文與無效的原始輸出。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import (
    LLMError,
    LLMIncompleteError,
    LLMProvider,
    LLMRefusalError,
    Usage,
)
from app.ai.llm.config import LLMConfig
from app.ai.llm.cost import CostLedger, CostLimitExceededError, call_cost, guard_call
from app.ai.runner.loader import Skill
from app.ai.runner.prompt import build_messages
from app.ai.runner.validate import Issue, parse_output, validate_output
from app.models.ai import AIAnalysisRun

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 4.0)
TAIPEI = ZoneInfo("Asia/Taipei")


class RunStatus(StrEnum):
    """ai_analysis_runs.status。"""

    SUCCEEDED = "succeeded"
    ABSTAINED = "abstained"
    FAILED = "failed"


class SkillInputError(ValueError):
    """輸入不符合 Skill 的輸入 schema；這是呼叫端的錯誤，不呼叫模型、不寫紀錄。"""


@dataclass(frozen=True)
class RunRecord:
    """要寫進 ai_analysis_runs 的一筆紀錄（不含提示詞與來源原文）。"""

    skill_name: str
    skill_version: str
    schema_version: str
    provider: str
    model_id: str
    status: RunStatus
    result: dict[str, Any] | None
    abstain_reason: str | None
    error_type: str | None
    validation_issues: tuple[str, ...]
    attempts: int
    evidence_ids: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    latency_ms: int
    trace_id: str
    event_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True)
class SkillRunResult:
    """執行結果。`output` 只在通過驗證時有值（succeeded，或模型自己 abstain）。"""

    run_id: uuid.UUID
    status: RunStatus
    output: dict[str, Any] | None
    abstain_reason: str | None
    error_type: str | None
    attempts: int
    cost_usd: float


class RunStore(Protocol):
    """執行紀錄的儲存：寫入紀錄、查詢當日已花費。"""

    async def spent_today_usd(self, now: datetime) -> float:
        """`now` 所在的台北日已花費的費用（USD）。"""
        ...

    async def save(self, record: RunRecord) -> uuid.UUID:
        """寫入一筆紀錄，回傳 id。"""
        ...


class SqlRunStore:
    """寫入 ai_analysis_runs；只 flush，交易由呼叫端提交。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def spent_today_usd(self, now: datetime) -> float:
        """依台北時間的日界線加總 cost_usd。"""
        local = now.astimezone(TAIPEI)
        start = datetime.combine(local.date(), time.min, tzinfo=TAIPEI).astimezone(UTC)
        total = await self._session.scalar(
            select(func.coalesce(func.sum(AIAnalysisRun.cost_usd), 0)).where(
                AIAnalysisRun.created_at >= start
            )
        )
        return float(total or 0)

    async def save(self, record: RunRecord) -> uuid.UUID:
        """新增一筆並 flush，回傳資料庫產生的 id。"""
        row = AIAnalysisRun(
            event_id=record.event_id,
            skill_name=record.skill_name,
            skill_version=record.skill_version,
            schema_version=record.schema_version,
            provider=record.provider,
            model_id=record.model_id,
            status=record.status.value,
            result=record.result,
            abstain_reason=record.abstain_reason,
            error_type=record.error_type,
            validation_issues=list(record.validation_issues),
            attempts=record.attempts,
            evidence_ids=list(record.evidence_ids),
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_input_tokens=record.cached_input_tokens,
            cost_usd=Decimal(str(round(record.cost_usd, 6))),
            latency_ms=record.latency_ms,
            trace_id=record.trace_id,
            created_at=record.created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id


@dataclass
class _Tally:
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model_id: str | None = None

    def add(self, usage: Usage | None, cost: float) -> None:
        if usage is not None:
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
            self.cached_input_tokens += usage.cached_input_tokens
        self.cost_usd += cost


def _schema_name(skill: Skill) -> str:
    return skill.name.replace("-", "_") + "_output"


async def run_skill(
    skill: Skill,
    payload: Mapping[str, Any],
    *,
    provider: LLMProvider,
    config: LLMConfig,
    store: RunStore,
    model: str | None = None,
    event_id: uuid.UUID | None = None,
    trace_id: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> SkillRunResult:
    """執行一次 Skill 並寫入紀錄；輸入不合 schema 時拋出 `SkillInputError`（不寫紀錄）。"""
    errors = sorted(
        Draft202012Validator(skill.input_schema).iter_errors(payload), key=lambda e: e.path
    )
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path) or "(根)"
        raise SkillInputError(
            f"{skill.name} 輸入不符合 input.schema.json：/{where} {errors[0].validator}"
        )

    model_id = model or config.default_model
    try:
        price = config.price(model_id)
    except ValueError as exc:
        raise ValueError(f"模型 {model_id!r} 沒有設定價格") from exc

    evidence_ids = tuple(str(e["evidence_id"]) for e in payload.get("evidence") or [])
    evidence_texts = {
        str(e["evidence_id"]): "\n".join(str(e[k]) for k in ("title", "text") if e.get(k))
        for e in payload.get("evidence") or []
    }
    messages = build_messages(skill, payload)
    trace = trace_id or uuid.uuid4().hex
    now = clock()
    ledger = CostLedger(
        daily_usd=config.limits.daily_usd, spent_usd=await store.spent_today_usd(now)
    )
    tally = _Tally()

    status = RunStatus.FAILED
    output: dict[str, Any] | None = None
    abstain_reason: str | None = None
    error_type: str | None = None
    issues: list[Issue] = []

    while tally.attempts < MAX_ATTEMPTS:
        try:
            estimate = guard_call(
                messages,
                skill.output_schema,
                price,
                max_output_tokens=config.max_output_tokens,
                per_call_usd=config.limits.per_call_usd,
            )
            ledger.check(estimate)
        except CostLimitExceededError:
            status, error_type = RunStatus.FAILED, "cost_limit"
            break

        tally.attempts += 1
        try:
            result = await provider.generate_structured(
                messages,
                skill.output_schema,
                schema_name=_schema_name(skill),
                model=model_id,
                timeout=config.timeout_seconds,
                max_output_tokens=config.max_output_tokens,
            )
        except LLMIncompleteError as exc:
            cost = call_cost(exc.usage, price) if exc.usage else 0.0
            tally.add(exc.usage, cost)
            ledger.record(cost)
            issues = [Issue("incomplete", "輸出未完成")]
            status, abstain_reason = RunStatus.ABSTAINED, "invalid_output"
            continue
        except LLMRefusalError as exc:
            cost = call_cost(exc.usage, price) if exc.usage else 0.0
            tally.add(exc.usage, cost)
            ledger.record(cost)
            status, abstain_reason, error_type = RunStatus.ABSTAINED, "model_refusal", None
            break
        except LLMError as exc:
            cost = call_cost(exc.usage, price) if exc.usage else 0.0
            tally.add(exc.usage, cost)
            ledger.record(cost)
            status, error_type, abstain_reason = RunStatus.FAILED, type(exc).__name__, None
            if not exc.retryable or tally.attempts >= MAX_ATTEMPTS:
                break
            delay = (
                exc.retry_after
                if exc.retry_after is not None
                else BACKOFF_SECONDS[min(tally.attempts - 1, len(BACKOFF_SECONDS) - 1)]
            )
            await sleep(delay)
            continue

        cost = call_cost(result.usage, price)
        tally.add(result.usage, cost)
        tally.latency_ms += result.latency_ms
        tally.model_id = result.model
        ledger.record(cost)

        parsed, issues = parse_output(result.text)
        if parsed is not None:
            issues = validate_output(parsed, skill, evidence_ids, evidence_texts=evidence_texts)
        if parsed is not None and not issues:
            output = parsed
            if parsed.get("status") == "abstain":
                status, abstain_reason = RunStatus.ABSTAINED, str(parsed.get("abstain_reason"))
            else:
                status, abstain_reason = RunStatus.SUCCEEDED, None
            error_type = None
            break
        status, abstain_reason, error_type = RunStatus.ABSTAINED, "invalid_output", None

    record = RunRecord(
        skill_name=skill.name,
        skill_version=skill.version,
        schema_version=skill.schema_version,
        provider=provider.name,
        model_id=tally.model_id or model_id,
        status=status,
        result=output,
        abstain_reason=abstain_reason,
        error_type=error_type,
        validation_issues=tuple(dict.fromkeys(i.code for i in issues)) if output is None else (),
        attempts=tally.attempts,
        evidence_ids=evidence_ids,
        input_tokens=tally.input_tokens,
        output_tokens=tally.output_tokens,
        cached_input_tokens=tally.cached_input_tokens,
        cost_usd=tally.cost_usd,
        latency_ms=tally.latency_ms,
        trace_id=trace,
        event_id=event_id,
        created_at=now,
    )
    run_id = await store.save(record)
    return SkillRunResult(
        run_id=run_id,
        status=status,
        output=output,
        abstain_reason=abstain_reason,
        error_type=error_type,
        attempts=tally.attempts,
        cost_usd=tally.cost_usd,
    )
