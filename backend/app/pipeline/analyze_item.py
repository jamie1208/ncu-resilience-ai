"""分析管線（P2-T13）：S01 → S02 → S03 → S04 → S05 → S06。

`analyze_item`（一篇來源文章）：
1. S01 relevance-filter：與校園無關 → 結束（irrelevant）。
2. S02 event-extractor：不是事件 → 結束（not_event）。
3. S03 event-classifier：類別為 none → 結束（not_event）。
4. 候選召回（`app/ai/embeddings.py`）＋ S04 event-linker：依距離逐一判斷候選事件，
   第一個 same_event 就併入該事件（linked）；都不是時建立新事件（created），
   related_event 的候選另寫 event_relations。
5. S05 evidence-verifier：模型只給每份證據的立場，最終狀態由 `app/verification/rules.py` 決定，
   事件狀態一律經狀態機。
6. S06 risk-assessor：模型只給五維分數，加權與分級由 `app/risk/engine.py` 計算，
   risk_assessments 每次新增一筆（保留歷史）。

事件說明（P4-T08）：S02 的 `summary` 在建立或併入事件時寫入 events。同事件多篇來源時，
以證據最強（官方來源優先）、其次最新（發布時間，沒有時用擷取時間）的一篇為準；
新文章沒有比目前的出處更強或更新、或沒有摘要時不覆寫。寫入前再經 `mask_pii()` 遮蔽
（Runner 已拒絕含個資的輸出，這是第二道防線）。

`analyze_event`（`POST /api/v1/admin/events/{id}/analyze` 的重跑）：以事件目前的證據重跑 S05、S06。
文章已屬於某個事件時，`analyze_item` 不重建事件，改為重跑該事件（冪等）。

設計（AI 自行決定，見 P2-T13 驗收紀錄）：
- 一個 Celery 任務依序執行六個步驟，不拆成多個 Celery 任務串接：步驟之間傳遞的是來源原文，
  不讓原文進入 Redis；而且 worker 只有一個行程，拆開也不會更快。
- 每次 Skill 執行後提交交易：已花費的執行紀錄（每日費用上限依它計算）不會因後續例外而消失。
- S01–S03 在事件產生前執行，事件建立或併入後補上它們的 event_id，方便由事件回溯。
- 任一步驟 abstain 或 failed 時停在該步驟（stopped）；S04 的單一候選判斷失敗只略過該候選；
  S05、S06 失敗不影響事件已建立的結果。
- 結束時寫一筆稽核紀錄（pipeline.analyze_item／pipeline.analyze_event），request_id = trace_id；
  只記結果代碼與 id，不記原文。

事件小報告（P4-T09）：S06 與通知同步之後呼叫 `PipelineDeps.report_hook`（正式環境為
`app.reports.service.after_assessment`），由它決定是否排入產生報告（S07、S08）或讓報告失效。
沒有設定 hook 時不處理報告。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import (
    Candidate,
    embed_texts,
    embedding_text,
    find_candidate_events,
)
from app.ai.llm.base import EmbeddingProvider, LLMError, LLMProvider
from app.ai.llm.config import (
    LLMConfig,
    LLMConfigError,
    create_embedding_provider,
    create_provider,
    load_llm_config,
)
from app.ai.llm.cost import CostLimitExceededError
from app.ai.runner.loader import Skill, discover_skills
from app.ai.runner.run import RunStatus, SkillRunResult, SqlRunStore, run_skill
from app.alerts.pii import mask_pii
from app.alerts.service import sync_event_alert
from app.core.config import get_settings
from app.core.security import Permission, Principal, Role, require
from app.domain.states import EventStatus, StateTransitionError, VerificationStatus, transition
from app.models.ai import AIAnalysisRun, EventRelation
from app.models.event import Event, EventEvidence, Location
from app.models.source import AnalysisStatus, Source, SourceItem, SourceItemVersion
from app.risk.engine import RiskPolicy, assessment_record, load_policy
from app.services.audit import record_audit
from app.verification.rules import (
    assessments_from_output,
    decide,
    event_status_for,
    is_official_source,
)
from app.worker.celery_app import (
    ANALYZE_DISPATCH_TASK,
    ANALYZE_EVENT_TASK,
    ANALYZE_ITEM_TASK,
    celery_app,
)

S01 = "relevance-filter"
S02 = "event-extractor"
S03 = "event-classifier"
S04 = "event-linker"
S05 = "evidence-verifier"
S06 = "risk-assessor"
EMBEDDING_STEP = "embedding"

MAX_CANDIDATES = 3
"""每篇文章最多交給 S04 判斷幾個候選事件（每個候選一次模型呼叫）。"""
MAX_EVENT_EVIDENCE = 6
"""S05、S06 一次最多看幾份證據（輸入 schema 上限）；超過時取最新的。"""
MAX_CANDIDATE_EVIDENCE = 3
"""S04 的候選事件最多帶幾份證據（輸入 schema 上限）。"""
MAX_TEXT_CHARS = 20000
MAX_TITLE_CHARS = 300
MAX_LOCATION_CHARS = 50
UNKNOWN_LOCATION = "不明"
UNTITLED = "未命名事件"
INITIAL_SUPPORT = "mentions"
TAIPEI = ZoneInfo("Asia/Taipei")

ANALYSIS_WINDOW_DAYS = 14
"""只分析發布（沒有發布時間時以擷取時間）在幾天內的文章；更舊的標為 skipped（P4-T07）。"""
MAX_ANALYSIS_ATTEMPTS = 3
"""同一篇文章分析失敗幾次後標為 failed；費用上限造成的中止不計。"""
DISPATCH_BATCH = 3
"""排隊中（未逾時）的文章最多幾篇；掃描只補到這個數量。"""
REQUEUE_AFTER = timedelta(minutes=60)
"""排入後超過這段時間仍是 queued，視為任務遺失、重新排入。"""
COST_LIMIT_ERRORS = frozenset({"cost_limit", CostLimitExceededError.__name__})

PIPELINE_PRINCIPAL = Principal(
    kind="service", subject="ai-pipeline", roles=frozenset({Role.SERVICE_AI})
)


class Outcome(StrEnum):
    """管線的結果。"""

    IRRELEVANT = "irrelevant"  # S01：與校園無關
    NOT_EVENT = "not_event"  # S02 不是事件，或 S03 類別為 none
    CREATED = "created"  # 建立新事件
    LINKED = "linked"  # 併入既有事件（S04 same_event）
    ANALYZED = "analyzed"  # 重跑既有事件的 S05、S06
    STOPPED = "stopped"  # S01–S03 或 embedding 沒有結果（abstain、failed）
    SKIPPED = "skipped"  # 事件已封存或沒有證據


ReportHook = Callable[[AsyncSession, uuid.UUID, str | None, str], Awaitable[None]]
"""S06 之後的報告處理：（session, event_id, 風險分級, trace_id）。"""


@dataclass(frozen=True)
class PipelineDeps:
    """管線的相依：模型、設定、Skills、風險政策與事件小報告的 hook。"""

    provider: LLMProvider
    embedder: EmbeddingProvider
    config: LLMConfig
    skills: Mapping[str, Skill]
    policy: RiskPolicy
    max_candidates: int = MAX_CANDIDATES
    report_hook: ReportHook | None = None


@dataclass(frozen=True)
class Step:
    """一次 Skill 執行（或 embedding）的結果摘要。"""

    name: str
    status: str
    run_id: uuid.UUID | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    """管線結果；`steps` 依執行順序列出，`stopped_at` 是中止的步驟。"""

    outcome: Outcome
    event_id: uuid.UUID | None
    trace_id: str
    steps: tuple[Step, ...]
    stopped_at: str | None = None
    verification: VerificationStatus | None = None
    risk_level: str | None = None


@dataclass
class _Context:
    session: AsyncSession
    deps: PipelineDeps
    trace_id: str
    steps: list[Step] = field(default_factory=list)

    async def run(
        self, skill_name: str, payload: Mapping[str, Any], *, event_id: uuid.UUID | None = None
    ) -> SkillRunResult:
        """執行一個 Skill 並提交（執行紀錄不因後續例外而消失）。"""
        result = await run_skill(
            self.deps.skills[skill_name],
            payload,
            provider=self.deps.provider,
            config=self.deps.config,
            store=SqlRunStore(self.session),
            event_id=event_id,
            trace_id=self.trace_id,
        )
        await self.session.commit()
        self.steps.append(Step(skill_name, result.status.value, result.run_id, result.error_type))
        return result


# ---------- 證據 ----------


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


async def _evidence(session: AsyncSession, item_ids: Sequence[uuid.UUID]) -> list[dict[str, Any]]:
    """文章的最新版本轉成 Skill 輸入的證據（順序同 `item_ids`；沒有版本的文章略過）。

    evidence_id 是文章 id（32 位十六進位），可以直接回溯 source_items。
    標題已包含在 normalized_text 的第一段，所以 title 留空。
    """
    if not item_ids:
        return []
    latest = (
        select(SourceItemVersion.item_id, SourceItemVersion.normalized_text)
        .ext(distinct_on(SourceItemVersion.item_id))
        .where(SourceItemVersion.item_id.in_(list(item_ids)))
        .order_by(SourceItemVersion.item_id, SourceItemVersion.captured_at.desc())
        .subquery()
    )
    rows = (
        await session.execute(
            select(SourceItem.id, SourceItem.published_at, Source.code, latest.c.normalized_text)
            .join(Source, Source.id == SourceItem.source_id)
            .join(latest, latest.c.item_id == SourceItem.id)
        )
    ).all()
    by_id = {
        item_id: {
            "evidence_id": item_id.hex,
            "source_code": code,
            "title": None,
            "text": text[:MAX_TEXT_CHARS],
            "published_at": _iso(published_at),
        }
        for item_id, published_at, code, text in rows
    }
    return [by_id[i] for i in item_ids if i in by_id]


async def _event_item_ids(
    session: AsyncSession, event_id: uuid.UUID, limit: int
) -> list[uuid.UUID]:
    """事件最新的 `limit` 份證據，依加入時間由舊到新。"""
    rows = await session.scalars(
        select(EventEvidence.source_item_id)
        .where(EventEvidence.event_id == event_id)
        .order_by(EventEvidence.created_at.desc(), EventEvidence.source_item_id)
        .limit(limit)
    )
    return list(reversed(list(rows)))


async def _location_name(session: AsyncSession, event: Event) -> str | None:
    if event.location_id is None:
        return None
    location = await session.get(Location, event.location_id)
    return location.name[:MAX_LOCATION_CHARS] if location else None


async def _location_id(session: AsyncSession, name: str | None) -> uuid.UUID | None:
    """S02 的地點標準名稱對應到 locations（沒有就新增）；「不明」或空白不建立。"""
    if not name or name == UNKNOWN_LOCATION:
        return None
    existing = await session.scalar(select(Location.id).where(Location.name == name).limit(1))
    if existing is not None:
        return existing
    location = Location(name=name)
    session.add(location)
    await session.flush()
    return location.id


def _parse_date(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


# ---------- 事件說明（P4-T08） ----------


async def _summary_rank(session: AsyncSession, item_id: uuid.UUID) -> tuple[bool, datetime] | None:
    """文章作為事件說明出處的強弱：（是否官方來源, 發布時間或擷取時間）；越大越優先。"""
    row = (
        await session.execute(
            select(Source.code, SourceItem.published_at, SourceItem.fetched_at)
            .join(Source, Source.id == SourceItem.source_id)
            .where(SourceItem.id == item_id)
        )
    ).one_or_none()
    if row is None:
        return None
    code, published_at, fetched_at = row
    return is_official_source(code), published_at or fetched_at


async def _update_summary(
    session: AsyncSession,
    event: Event,
    summary: Any,
    *,
    item_id: uuid.UUID,
    run_id: uuid.UUID | None,
) -> None:
    """S02 的摘要比目前的出處更強或更新時寫入事件；沒有摘要或文字、證據為空時不動。"""
    if not isinstance(summary, Mapping):
        return
    text = str(summary.get("text") or "").strip()
    evidence_ids = [str(e) for e in summary.get("evidence_ids") or []]
    if not text or not evidence_ids:
        return
    if event.summary is not None and event.summary_source_item_id is not None:
        current = await _summary_rank(session, event.summary_source_item_id)
        candidate = await _summary_rank(session, item_id)
        if current is not None and (candidate is None or candidate <= current):
            return
    event.summary = mask_pii(text)
    event.summary_evidence_ids = evidence_ids
    event.summary_source_item_id = item_id
    event.summary_ai_run_id = run_id
    event.summary_updated_at = datetime.now(UTC)


# ---------- 查證與風險（S05、S06） ----------


_VERIFICATION_OF = {
    EventStatus.UNVERIFIED: VerificationStatus.UNVERIFIED,
    EventStatus.VERIFIED: VerificationStatus.VERIFIED,
    EventStatus.DISPUTED: VerificationStatus.DISPUTED,
    EventStatus.FALSE: VerificationStatus.FALSE,
}


async def _verify_and_assess(
    ctx: _Context, event: Event
) -> tuple[VerificationStatus | None, str | None]:
    """以事件的證據執行 S05、S06；回傳查證結果與風險分級（沒有結果時為 None）。"""
    session = ctx.session
    evidence = await _evidence(
        session, await _event_item_ids(session, event.id, MAX_EVENT_EVIDENCE)
    )
    summary = {
        "title": event.title[:MAX_TITLE_CHARS],
        "category": event.type,
        "occurrence_start_date": (
            event.occurrence_start_date.isoformat() if event.occurrence_start_date else None
        ),
        "location": await _location_name(session, event),
    }

    verified = await ctx.run(S05, {"evidence": evidence, "event": summary}, event_id=event.id)
    current = EventStatus(event.status)
    if current is EventStatus.ANALYZING:  # 新事件：分析完成後先進入 unverified
        current = transition(current, EventStatus.UNVERIFIED)
    decision_status: VerificationStatus | None = None
    output = verified.output
    if verified.status is RunStatus.SUCCEEDED and output is not None:
        assessments = assessments_from_output(output, evidence)
        suggestion = output.get("suggested_status")
        decision = decide(
            assessments,
            suggestion=VerificationStatus(suggestion) if suggestion else None,
        )
        decision_status = decision.status
        # 不合法的轉移（例：已解決的事件不能直接改判）維持原狀態，等管理者處理
        with contextlib.suppress(StateTransitionError):
            current = event_status_for(current, decision.status) or current
        stances = {a.evidence_id: a.stance.value for a in assessments}
        for link in await session.scalars(
            select(EventEvidence).where(EventEvidence.event_id == event.id)
        ):
            if link.source_item_id.hex in stances:
                link.support_type = stances[link.source_item_id.hex]
    event.status = current.value
    await session.commit()

    verification = decision_status or _VERIFICATION_OF.get(current, VerificationStatus.UNVERIFIED)
    summary["verification_status"] = verification.value
    assessed = await ctx.run(S06, {"evidence": evidence, "event": summary}, event_id=event.id)
    level: str | None = None
    if (
        assessed.status is RunStatus.SUCCEEDED
        and assessed.output is not None
        and assessed.output.get("status") == "ok"
    ):
        row = assessment_record(
            event.id, assessed.output, ctx.deps.policy, ai_run_id=assessed.run_id
        )
        session.add(row)
        level = row.level
        await session.commit()
    # 查證與風險更新後同步個人通知（P3-T08）
    await sync_event_alert(session, event.id, request_id=ctx.trace_id)
    await session.commit()
    if ctx.deps.report_hook is not None:  # 事件小報告（P4-T09）
        await ctx.deps.report_hook(session, event.id, level, ctx.trace_id)
    return decision_status, level


# ---------- 管線 ----------


def _trace(trace_id: str | None) -> str:
    return trace_id or uuid.uuid4().hex


async def _finish(
    ctx: _Context,
    action: str,
    target: str,
    result: PipelineResult,
) -> PipelineResult:
    """寫入稽核紀錄並提交。"""
    await record_audit(
        ctx.session,
        PIPELINE_PRINCIPAL,
        action,
        target=target,
        request_id=ctx.trace_id,
        metadata={
            "outcome": result.outcome.value,
            "event_id": result.event_id,
            "stopped_at": result.stopped_at,
            "verification": result.verification,
            "risk_level": result.risk_level,
            "runs": sum(1 for s in result.steps if s.run_id is not None),
        },
    )
    await ctx.session.commit()
    return result


async def analyze_event(
    session: AsyncSession,
    event_id: uuid.UUID,
    deps: PipelineDeps,
    *,
    trace_id: str | None = None,
) -> PipelineResult:
    """以事件目前的證據重跑 S05、S06；事件不存在時拋出 LookupError。"""
    require(PIPELINE_PRINCIPAL, Permission.AI_RUN)
    ctx = _Context(session, deps, _trace(trace_id))
    return await _finish(
        ctx, "pipeline.analyze_event", f"event:{event_id}", await _analyze_event(ctx, event_id)
    )


async def _analyze_event(ctx: _Context, event_id: uuid.UUID) -> PipelineResult:
    event = await ctx.session.get(Event, event_id)
    if event is None:
        raise LookupError(f"事件 {event_id} 不存在")
    has_evidence = await ctx.session.scalar(
        select(EventEvidence.source_item_id).where(EventEvidence.event_id == event_id).limit(1)
    )
    if event.status == EventStatus.ARCHIVED.value or has_evidence is None:
        return PipelineResult(Outcome.SKIPPED, event.id, ctx.trace_id, tuple(ctx.steps))
    verification, level = await _verify_and_assess(ctx, event)
    return PipelineResult(
        Outcome.ANALYZED,
        event.id,
        ctx.trace_id,
        tuple(ctx.steps),
        verification=verification,
        risk_level=level,
    )


async def analyze_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    deps: PipelineDeps,
    *,
    trace_id: str | None = None,
) -> PipelineResult:
    """對一篇來源文章執行完整管線；文章不存在或沒有內容版本時拋出 LookupError。"""
    require(PIPELINE_PRINCIPAL, Permission.AI_RUN)
    ctx = _Context(session, deps, _trace(trace_id))
    result = await _analyze_item(ctx, item_id)
    await _mark_analysis(session, item_id, result)
    return await _finish(ctx, "pipeline.analyze_item", f"source_item:{item_id}", result)


async def _mark_analysis(session: AsyncSession, item_id: uuid.UUID, result: PipelineResult) -> None:
    """依管線結果更新文章的分析狀態（P4-T07）。

    完成或模型 abstain → done；費用上限 → 保持 pending、不計次數；其他失敗計一次，
    達 `MAX_ANALYSIS_ATTEMPTS` 次 → failed，否則回到 pending 等下一次掃描。
    """
    last = result.steps[-1] if result.steps else None
    item = await session.get(SourceItem, item_id, with_for_update=True)
    if item is None:
        return
    item.analysis_queued_at = None
    if result.outcome is not Outcome.STOPPED or (
        last is not None and last.status == RunStatus.ABSTAINED.value
    ):
        item.analysis_status = AnalysisStatus.DONE
    elif last is not None and (last.error_type or last.status) in COST_LIMIT_ERRORS:
        item.analysis_status = AnalysisStatus.PENDING
    else:
        item.analysis_attempts += 1
        item.analysis_status = (
            AnalysisStatus.FAILED
            if item.analysis_attempts >= MAX_ANALYSIS_ATTEMPTS
            else AnalysisStatus.PENDING
        )


async def dispatch_pending_items(
    session: AsyncSession,
    *,
    now: datetime,
    daily_usd: float,
    limit: int = DISPATCH_BATCH,
    window_days: int = ANALYSIS_WINDOW_DAYS,
    requeue_after: timedelta = REQUEUE_AFTER,
) -> list[uuid.UUID]:
    """挑出待分析的文章標為 queued 並回傳 id（新的先做）；只 flush 不 commit（P4-T07）。

    1. 超過時間窗的 pending 文章標為 skipped。
    2. 台北日已花費達每日上限時不排（隔天再排）。
    3. 未逾時的 queued 文章已有 `limit` 篇時不排；否則補到 `limit` 篇，逾時的 queued 一併重排。
    """
    reference = func.coalesce(SourceItem.published_at, SourceItem.fetched_at)
    await session.execute(
        update(SourceItem)
        .where(
            SourceItem.analysis_status == AnalysisStatus.PENDING,
            reference < now - timedelta(days=window_days),
        )
        .values(analysis_status=AnalysisStatus.SKIPPED)
    )
    if await SqlRunStore(session).spent_today_usd(now) >= daily_usd:
        return []
    stale_before = now - requeue_after
    in_flight = await session.scalar(
        select(func.count())
        .select_from(SourceItem)
        .where(
            SourceItem.analysis_status == AnalysisStatus.QUEUED,
            SourceItem.analysis_queued_at >= stale_before,
        )
    )
    room = limit - int(in_flight or 0)
    if room <= 0:
        return []
    ids = list(
        await session.scalars(
            select(SourceItem.id)
            .where(
                or_(
                    SourceItem.analysis_status == AnalysisStatus.PENDING,
                    and_(
                        SourceItem.analysis_status == AnalysisStatus.QUEUED,
                        or_(
                            SourceItem.analysis_queued_at.is_(None),
                            SourceItem.analysis_queued_at < stale_before,
                        ),
                    ),
                )
            )
            .order_by(reference.desc(), SourceItem.id)
            .limit(room)
            .with_for_update(skip_locked=True)
        )
    )
    if ids:
        await session.execute(
            update(SourceItem)
            .where(SourceItem.id.in_(ids))
            .values(analysis_status=AnalysisStatus.QUEUED, analysis_queued_at=now)
        )
    return ids


def _stopped(ctx: _Context, step: str) -> PipelineResult:
    return PipelineResult(Outcome.STOPPED, None, ctx.trace_id, tuple(ctx.steps), stopped_at=step)


def _ok(result: SkillRunResult) -> Mapping[str, Any] | None:
    """成功且不是 abstain 的輸出；其他情況回傳 None。"""
    if result.status is RunStatus.SUCCEEDED and result.output is not None:
        return result.output
    return None


async def _analyze_item(ctx: _Context, item_id: uuid.UUID) -> PipelineResult:
    session = ctx.session
    linked = await session.scalar(
        select(EventEvidence.event_id).where(EventEvidence.source_item_id == item_id).limit(1)
    )
    if linked is not None:
        return await _analyze_event(ctx, linked)

    evidence = await _evidence(session, [item_id])
    if not evidence:
        raise LookupError(f"來源文章 {item_id} 不存在或沒有內容")
    article = evidence[0]
    eid = str(article["evidence_id"])
    payload = {"evidence": evidence}

    relevance = _ok(await ctx.run(S01, payload))
    if relevance is None:
        return _stopped(ctx, S01)
    if relevance.get("is_campus_relevant") is not True:
        return PipelineResult(Outcome.IRRELEVANT, None, ctx.trace_id, tuple(ctx.steps))

    extraction = await ctx.run(S02, payload)
    extracted = _ok(extraction)
    if extracted is None:
        return _stopped(ctx, S02)
    if extracted.get("is_event") is not True:
        return PipelineResult(Outcome.NOT_EVENT, None, ctx.trace_id, tuple(ctx.steps))

    classified = _ok(await ctx.run(S03, payload))
    if classified is None:
        return _stopped(ctx, S03)
    category = str(classified["category"])
    if category == "none":
        return PipelineResult(Outcome.NOT_EVENT, None, ctx.trace_id, tuple(ctx.steps))

    try:
        embedded = await embed_texts(
            ctx.deps.embedder, [embedding_text(None, str(article["text"]))], config=ctx.deps.config
        )
    except (LLMError, LLMConfigError, CostLimitExceededError) as exc:
        ctx.steps.append(Step(EMBEDDING_STEP, type(exc).__name__))
        return _stopped(ctx, EMBEDDING_STEP)
    ctx.steps.append(Step(EMBEDDING_STEP, RunStatus.SUCCEEDED.value))
    vector = list(embedded.vectors[0])

    # 召回視窗的基準日：事件開始日期 > 發布日期（台北）> 今天
    start = _parse_date(extracted.get("occurrence_start_date"))
    published = article["published_at"]
    on_date = (
        start
        or (datetime.fromisoformat(str(published)) if published else datetime.now(UTC))
        .astimezone(TAIPEI)
        .date()
    )
    candidates = await find_candidate_events(
        session, vector, on_date=on_date, category=category, limit=ctx.deps.max_candidates
    )
    location = extracted.get("location")
    article_summary = {
        "evidence_ids": [eid],
        "category": category,
        "occurrence_start_date": extracted.get("occurrence_start_date"),
        "location": location,
    }
    target, related = await _link(ctx, article, article_summary, candidates)

    if target is not None:
        event = await session.get(Event, target)
        if event is None:
            raise LookupError(f"事件 {target} 不存在")
        outcome = Outcome.LINKED
    else:
        event = Event(
            title=str(extracted.get("title") or UNTITLED)[:MAX_TITLE_CHARS],
            type=category,
            status=EventStatus.DETECTED.value,
            location_id=await _location_id(session, location),
            occurrence_start_date=start,
            embedding=vector,
        )
        session.add(event)
        await session.flush()
        event.status = transition(EventStatus.DETECTED, EventStatus.ANALYZING).value
        for candidate_id, run_id, confidence, cited in related:
            session.add(
                EventRelation(
                    from_event_id=event.id,
                    to_event_id=candidate_id,
                    type="related_event",
                    score=confidence,
                    evidence=cited,
                    ai_run_id=run_id,
                )
            )
        outcome = Outcome.CREATED
    session.add(
        EventEvidence(event_id=event.id, source_item_id=item_id, support_type=INITIAL_SUPPORT)
    )
    await _update_summary(
        session,
        event,
        extracted.get("summary"),
        item_id=item_id,
        run_id=extraction.run_id,
    )
    article_runs = [s.run_id for s in ctx.steps if s.name in (S01, S02, S03) and s.run_id]
    await session.execute(
        update(AIAnalysisRun)
        .where(AIAnalysisRun.id.in_(article_runs), AIAnalysisRun.event_id.is_(None))
        .values(event_id=event.id)
    )
    await session.commit()

    verification, level = await _verify_and_assess(ctx, event)
    return PipelineResult(
        outcome,
        event.id,
        ctx.trace_id,
        tuple(ctx.steps),
        verification=verification,
        risk_level=level,
    )


async def _link(
    ctx: _Context,
    article: Mapping[str, Any],
    article_summary: Mapping[str, Any],
    candidates: Sequence[Candidate],
) -> tuple[uuid.UUID | None, list[tuple[uuid.UUID, uuid.UUID, float, list[str]]]]:
    """依距離逐一以 S04 判斷候選事件；回傳 same_event 的事件與 related_event 的清單。"""
    related: list[tuple[uuid.UUID, uuid.UUID, float, list[str]]] = []
    for candidate in candidates:
        item_ids = await _event_item_ids(ctx.session, candidate.event_id, MAX_CANDIDATE_EVIDENCE)
        candidate_evidence = await _evidence(ctx.session, item_ids)
        if not candidate_evidence:
            continue
        payload = {
            "evidence": [article, *candidate_evidence],
            "article": article_summary,
            "candidate": {
                "title": candidate.title[:MAX_TITLE_CHARS],
                "evidence_ids": [e["evidence_id"] for e in candidate_evidence],
                "category": candidate.category,
                "occurrence_start_date": (
                    candidate.occurrence_start_date.isoformat()
                    if candidate.occurrence_start_date
                    else None
                ),
                "location": candidate.location[:MAX_LOCATION_CHARS] if candidate.location else None,
            },
        }
        result = await ctx.run(S04, payload, event_id=candidate.event_id)
        output = _ok(result)
        if output is None:
            continue
        if output.get("relation") == "same_event":
            return candidate.event_id, related
        if output.get("relation") == "related_event":
            related.append(
                (
                    candidate.event_id,
                    result.run_id,
                    float(output.get("confidence") or 0),
                    [str(e) for e in output.get("evidence_ids") or []],
                )
            )
    return None, related


# ---------- Celery 任務 ----------


def build_deps() -> PipelineDeps:
    """依設定檔與環境變數建立正式的相依（OpenAI 需要 OPENAI_API_KEY）。"""
    from app.reports.service import make_report_hook, send_to_celery  # 報告模組匯入本模組

    config = load_llm_config()
    settings = get_settings()
    return PipelineDeps(
        provider=create_provider(config, settings),
        embedder=create_embedding_provider(config, settings),
        config=config,
        skills=discover_skills(),
        policy=load_policy(),
        report_hook=make_report_hook(send_to_celery),
    )


async def _in_worker(item_id: str | None, event_id: str | None, trace_id: str | None) -> None:
    from app.worker.tasks.ingest import open_sessionmaker  # 避免 API 匯入時載入擷取模組

    deps = build_deps()
    async with open_sessionmaker() as maker, maker() as session:
        if item_id is not None:
            await analyze_item(session, uuid.UUID(item_id), deps, trace_id=trace_id)
        elif event_id is not None:
            await analyze_event(session, uuid.UUID(event_id), deps, trace_id=trace_id)


@celery_app.task(name=ANALYZE_ITEM_TASK)
def analyze_item_task(item_id: str, trace_id: str | None = None) -> None:
    """Celery 任務：對一篇來源文章執行完整管線。"""
    asyncio.run(_in_worker(item_id, None, trace_id))


async def _dispatch_in_worker() -> list[uuid.UUID]:
    from app.worker.tasks.ingest import open_sessionmaker  # 避免 API 匯入時載入擷取模組

    config = load_llm_config()
    async with open_sessionmaker() as maker, maker() as session, session.begin():
        return await dispatch_pending_items(
            session, now=datetime.now(UTC), daily_usd=config.limits.daily_usd
        )


@celery_app.task(name=ANALYZE_DISPATCH_TASK)
def dispatch_pending_items_task() -> int:
    """Celery beat 任務：交易提交後才把挑出的文章排入 `analyze_item`。"""
    ids = asyncio.run(_dispatch_in_worker())
    for item_id in ids:
        celery_app.send_task(ANALYZE_ITEM_TASK, args=[str(item_id)])
    return len(ids)


@celery_app.task(name=ANALYZE_EVENT_TASK)
def analyze_event_task(event_id: str, trace_id: str | None = None) -> None:
    """Celery 任務：重跑事件的查證與風險評估（trace_id 沿用 API 的 request_id）。"""
    asyncio.run(_in_worker(None, event_id, trace_id))


__all__ = [
    "ANALYSIS_WINDOW_DAYS",
    "ANALYZE_DISPATCH_TASK",
    "ANALYZE_EVENT_TASK",
    "ANALYZE_ITEM_TASK",
    "MAX_ANALYSIS_ATTEMPTS",
    "AnalysisStatus",
    "Outcome",
    "PipelineDeps",
    "PipelineResult",
    "ReportHook",
    "Step",
    "analyze_event",
    "analyze_item",
    "dispatch_pending_items",
]
