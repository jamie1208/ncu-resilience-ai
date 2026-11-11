"""既有內容以新語氣重跑（P8-T17）。

P8-T11 起 S02 的摘要與 S12 的系列說明改為播報員語氣，P8-T17 再把事件標題與系列名稱也改為
播報員語氣；但正式資料庫中既有的內容不會自動更新（S02 只在有更強或更新的文章時覆寫摘要，
系列只在有事件加入或移出時重新分析）。本模組一次性地把舊版本產生的內容重跑一次。

命令列：`uv run python -m app.pipeline.rerun_tone [--dry-run] [--events] [--series]`
（兩者都沒指定時兩個都做）。

- 事件：不是 false、未封存，且目前摘要來自舊版 S02（或沒有執行紀錄）的事件。以摘要的出處文章
  （`summary_source_item_id`）重跑 S02，只更新摘要（經 `mask_pii()`）與標題；類別、地點、
  查證、風險與出處文章不變，不重跑 S01、S03～S06，也不重新同步個人通知。
  標題只在等於這個事件某次 S02 輸出的標題時才換（使用者拆分時填的標題不動）。
  失敗、abstain、不是事件或摘要為空時保留舊內容。每個事件寫一筆稽核 `event.resummarize`
  （只記新舊版本、結果與標題是否改變，不記文字）。
- 系列：至少有一次成功分析，且沒有任何一次成功分析來自現行版本 S12 的系列。以
  `analyze_series(force=True)` 重跑，略過「每系列每天最多一次」；人工改過的名稱不覆蓋（P8-T05）。
  從沒成功分析過的系列交給既有的補做排程（`stale_series`）。
- 依版本判斷對象，所以可以重跑：已更新的不再呼叫模型。每次呼叫都受每日費用上限，
  一遇到費用上限就停止，回報剩下的數量，隔天重跑接續。
- `--dry-run` 只列出對象數量與預估費用（各 Skill 過去執行的平均 `cost_usd`），不呼叫模型。

執行後，公開同步（每分鐘）會為內容改變的事件與系列產生新版本；疑似指控、推測規律與原因的
文字改變時，通知中心會出現「公開提醒」。
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.run import RunStatus, SkillRunResult, SqlRunStore, run_skill
from app.alerts.pii import mask_pii
from app.core.security import Principal, Role
from app.domain.states import EventStatus
from app.models.ai import AIAnalysisRun
from app.models.event import Event
from app.models.series import SeriesAnalysis
from app.pipeline.analyze_item import (
    COST_LIMIT_ERRORS,
    MAX_TITLE_CHARS,
    S02,
    _evidence,
)
from app.series.analysis import S12, SkillCall, analyze_series
from app.services.audit import record_audit

RERUN_PRINCIPAL = Principal(
    kind="service", subject="tone-rerun", roles=frozenset({Role.SERVICE_AI})
)
SKIPPED_STATUSES = (EventStatus.FALSE.value, EventStatus.ARCHIVED.value)


class EventSkillCall(Protocol):
    """執行 S02 一次（帶事件 id，執行紀錄掛在該事件下）。"""

    def __call__(
        self, payload: Mapping[str, Any], *, event_id: uuid.UUID | None = None
    ) -> Awaitable[SkillRunResult]: ...


@dataclass(frozen=True)
class RerunReport:
    """一次重跑的結果。`remaining` 是因費用上限停止時還沒處理（含被擋下那筆）的數量。"""

    updated: int = 0
    titled: int = 0
    failed: int = 0
    remaining: int = 0
    stopped_by_cost: bool = False


@dataclass(frozen=True)
class RerunPlan:
    """`--dry-run` 的結果：對象數量與預估費用（US$）。"""

    events: int
    series: int
    event_cost_usd: float
    series_cost_usd: float

    @property
    def estimated_usd(self) -> float:
        """預估總費用。"""
        return self.events * self.event_cost_usd + self.series * self.series_cost_usd


def version_key(version: str | None) -> tuple[int, ...]:
    """語意化版本轉成可比較的 tuple；無法解析時視為最舊。"""
    try:
        return tuple(int(part) for part in str(version).split("."))
    except ValueError:
        return ()


def _is_old(version: str | None, current: str) -> bool:
    return version_key(version) < version_key(current)


# ---------- 對象 ----------


async def stale_tone_events(session: AsyncSession, version: str) -> list[tuple[Event, str | None]]:
    """摘要來自舊版 S02 的事件與其摘要的 S02 版本（不含 false、封存與沒有摘要出處的）。"""
    rows = (
        await session.execute(
            select(Event, AIAnalysisRun.skill_version)
            .outerjoin(AIAnalysisRun, AIAnalysisRun.id == Event.summary_ai_run_id)
            .where(
                Event.status.not_in(SKIPPED_STATUSES),
                Event.summary_source_item_id.is_not(None),
            )
            .order_by(Event.created_at, Event.id)
        )
    ).all()
    return [(event, old) for event, old in rows if _is_old(old, version)]


async def stale_tone_series(session: AsyncSession, version: str) -> list[uuid.UUID]:
    """有成功分析、但沒有任何一次成功分析來自現行版本 S12 的系列（最久沒分析的在前）。"""
    rows = (
        await session.execute(
            select(SeriesAnalysis.series_id, AIAnalysisRun.skill_version, SeriesAnalysis.created_at)
            .outerjoin(AIAnalysisRun, AIAnalysisRun.id == SeriesAnalysis.ai_run_id)
            .where(SeriesAnalysis.status == "ok")
        )
    ).all()
    newest: dict[uuid.UUID, tuple[tuple[int, ...], datetime]] = {}
    for series_id, skill_version, created_at in rows:
        key = version_key(skill_version)
        best_key, last = newest.get(series_id, ((), created_at))
        newest[series_id] = (max(best_key, key), max(last, created_at))
    current = version_key(version)
    targets = [(last, sid) for sid, (key, last) in newest.items() if key < current]
    return [sid for _, sid in sorted(targets)]


async def _average_cost(session: AsyncSession, skill: str) -> float:
    value = await session.scalar(
        select(func.avg(AIAnalysisRun.cost_usd)).where(
            AIAnalysisRun.skill_name == skill, AIAnalysisRun.cost_usd > 0
        )
    )
    return float(value or 0)


async def plan_rerun(session: AsyncSession, *, s02_version: str, s12_version: str) -> RerunPlan:
    """列出對象數量與預估費用，不呼叫模型。"""
    return RerunPlan(
        events=len(await stale_tone_events(session, s02_version)),
        series=len(await stale_tone_series(session, s12_version)),
        event_cost_usd=await _average_cost(session, S02),
        series_cost_usd=await _average_cost(session, S12),
    )


# ---------- 事件 ----------


async def _ai_titles(session: AsyncSession, event_id: uuid.UUID) -> set[str]:
    """這個事件歷次成功的 S02 輸出的標題（以建立事件時相同的截斷規則）。"""
    results = await session.scalars(
        select(AIAnalysisRun.result).where(
            AIAnalysisRun.event_id == event_id,
            AIAnalysisRun.skill_name == S02,
            AIAnalysisRun.status == RunStatus.SUCCEEDED.value,
        )
    )
    return {
        str(result["title"])[:MAX_TITLE_CHARS]
        for result in results
        if isinstance(result, Mapping) and result.get("title")
    }


def _new_summary(output: Mapping[str, Any] | None, item_hex: str) -> str | None:
    if not output or output.get("status") != "ok" or output.get("is_event") is not True:
        return None
    summary = output.get("summary")
    if not isinstance(summary, Mapping):
        return None
    text = str(summary.get("text") or "").strip()
    evidence_ids = [str(e) for e in summary.get("evidence_ids") or []]
    if not text or not evidence_ids or any(e != item_hex for e in evidence_ids):
        return None
    return text


async def rerun_events(
    session: AsyncSession,
    *,
    run: EventSkillCall,
    version: str,
    now: datetime | None = None,
    trace_id: str | None = None,
) -> RerunReport:
    """重跑摘要來自舊版 S02 的事件；每個事件處理完就提交。"""
    trace = (trace_id or f"tone-{uuid.uuid4().hex[:12]}")[:64]
    targets = await stale_tone_events(session, version)
    updated = titled = failed = 0
    for index, (event, old_version) in enumerate(targets):
        item_id = event.summary_source_item_id
        evidence = await _evidence(session, [item_id]) if item_id is not None else []
        if item_id is None or not evidence:
            failed += 1
            continue
        ai_titles = await _ai_titles(session, event.id)
        result = await run({"evidence": evidence}, event_id=event.id)
        if result.error_type in COST_LIMIT_ERRORS:
            return RerunReport(updated, titled, failed, len(targets) - index, True)
        output = result.output if result.status is RunStatus.SUCCEEDED else None
        text = _new_summary(output, item_id.hex)
        title_changed = False
        if text is None or output is None:
            failed += 1
            outcome = "kept"
        else:
            event.summary = mask_pii(text)
            event.summary_evidence_ids = [item_id.hex]
            event.summary_ai_run_id = result.run_id
            event.summary_updated_at = now or datetime.now(UTC)
            new_title = str(output.get("title") or "").strip()[:MAX_TITLE_CHARS]
            if new_title and event.title in ai_titles and event.title != new_title:
                event.title = mask_pii(new_title)
                title_changed = True
            updated += 1
            titled += title_changed
            outcome = "updated"
        await record_audit(
            session,
            RERUN_PRINCIPAL,
            "event.resummarize",
            target=f"event:{event.id}",
            request_id=trace,
            metadata={
                "from_version": old_version,
                "to_version": version,
                "result": outcome,
                "title_changed": title_changed,
            },
        )
        await session.commit()
    return RerunReport(updated, titled, failed, 0, False)


# ---------- 系列 ----------


async def rerun_series(
    session: AsyncSession,
    *,
    run: SkillCall,
    version: str,
    now: datetime | None = None,
    trace_id: str | None = None,
) -> RerunReport:
    """重跑最新成功分析來自舊版 S12 的系列（略過每天最多一次）；每個系列處理完就提交。"""
    trace = trace_id or f"tone-{uuid.uuid4().hex[:12]}"
    targets = await stale_tone_series(session, version)
    updated = failed = 0
    last: list[SkillRunResult] = []

    async def call(payload: Mapping[str, Any]) -> SkillRunResult:
        result = await run(payload)
        last.append(result)
        return result

    for index, series_id in enumerate(targets):
        last.clear()

        row = await analyze_series(
            session,
            series_id,
            run=call,
            principal=RERUN_PRINCIPAL,
            now=now,
            trace_id=trace,
            force=True,
        )
        if last and last[-1].error_type in COST_LIMIT_ERRORS:
            await session.commit()
            return RerunReport(updated, 0, failed, len(targets) - index, True)
        if row is not None and row.status == "ok":
            updated += 1
        else:
            failed += 1
        await session.commit()
    return RerunReport(updated, 0, failed, 0, False)


# ---------- 命令列 ----------


async def _in_worker(*, events: bool, series: bool, dry_run: bool) -> str:
    from app.pipeline.analyze_item import build_deps
    from app.worker.tasks.ingest import open_sessionmaker

    deps = build_deps()
    s02, s12 = deps.skills[S02], deps.skills[S12]
    trace = f"tone-{uuid.uuid4().hex[:12]}"
    lines: list[str] = []
    async with open_sessionmaker() as maker, maker() as session:
        if dry_run:
            plan = await plan_rerun(session, s02_version=s02.version, s12_version=s12.version)
            lines.append(f"事件：{plan.events} 個（S02 → {s02.version}）" if events else "")
            lines.append(f"系列：{plan.series} 個（S12 → {s12.version}）" if series else "")
            cost = (plan.event_cost_usd * plan.events if events else 0) + (
                plan.series_cost_usd * plan.series if series else 0
            )
            lines.append(f"預估費用：約 US${cost:.4f}（依過去平均單次費用）")
            return "\n".join(line for line in lines if line)

        async def call_s02(
            payload: Mapping[str, Any], *, event_id: uuid.UUID | None = None
        ) -> SkillRunResult:
            result = await run_skill(
                s02,
                payload,
                provider=deps.provider,
                config=deps.config,
                store=SqlRunStore(session),
                event_id=event_id,
                trace_id=trace,
            )
            await session.commit()
            return result

        async def call_s12(payload: Mapping[str, Any]) -> SkillRunResult:
            result = await run_skill(
                s12,
                payload,
                provider=deps.provider,
                config=deps.config,
                store=SqlRunStore(session),
                trace_id=trace,
            )
            await session.commit()
            return result

        reports: list[tuple[str, RerunReport]] = []
        if events:
            report = await rerun_events(session, run=call_s02, version=s02.version, trace_id=trace)
            reports.append(("事件", report))
        if series and not (reports and reports[-1][1].stopped_by_cost):
            report = await rerun_series(session, run=call_s12, version=s12.version, trace_id=trace)
            reports.append(("系列", report))
    for name, report in reports:
        line = f"{name}：更新 {report.updated}、失敗 {report.failed}"
        if name == "事件":
            line += f"、標題更新 {report.titled}"
        if report.stopped_by_cost:
            line += f"；已達每日費用上限，剩下 {report.remaining} 個，明天再執行一次"
        lines.append(line)
    if series and reports and reports[-1][0] == "事件" and reports[-1][1].stopped_by_cost:
        lines.append("系列：未執行（已達每日費用上限）")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """命令列入口。"""
    parser = argparse.ArgumentParser(
        prog="python -m app.pipeline.rerun_tone",
        description="把舊版 S02 摘要與標題、舊版 S12 系列分析以新語氣重跑一次（P8-T17）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出對象數量與預估費用")
    parser.add_argument("--events", action="store_true", help="只重跑事件的摘要與標題")
    parser.add_argument("--series", action="store_true", help="只重跑系列分析")
    args = parser.parse_args(argv)
    both = not args.events and not args.series
    print(
        asyncio.run(
            _in_worker(events=args.events or both, series=args.series or both, dry_run=args.dry_run)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RERUN_PRINCIPAL",
    "EventSkillCall",
    "RerunPlan",
    "RerunReport",
    "main",
    "plan_rerun",
    "rerun_events",
    "rerun_series",
    "stale_tone_events",
    "stale_tone_series",
    "version_key",
]
