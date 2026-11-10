"""公開狀態同步（P5-T11；系列 P8-T06）：把公開規則（`policy.py`）的判斷結果寫入 publications。

事件：從事件、證據、風險評估與查證稽核組出 `PublicationInput`，交給 `evaluate()` 判斷；
系列：從公開中的子事件（事件的公開快照）、最新一次成功的系列分析、系列的改善措施組出
`SeriesPublicationInput`，交給 `evaluate_series()` 判斷；時間軸（每週件數、4 期移動平均、措施
標記）、前後比較與突增週都由程式只依公開中的子事件計算。兩者都寫入 publications 與
publication_versions：

- 內容（content_hash）與公開程度都沒變時不升版；改變時新增一筆版本快照並升版；
- 判斷為不公開（含事件已封存）時，公開中的事件改為 withdrawn，並新增一筆無內容的版本；
- 已下架（taken_down，P5-T14）的事件一律不覆蓋，重新上架只能由使用者在後台操作；
- 每次公開、更新、撤下都以通知服務帳號寫入稽核（只記代碼與版本，不記內容）；
- 公開提醒（ADR-0018 2-2、ADR-0020 2-3）：新版本中需要使用者注意的內容（疑似指控的文字、推測的
  規律與原因，`review_parts()`）和目前版本不同時，版本的 reasons 加上 `remind`，通知中心
  （`GET /api/v1/admin/publications/reminders`）列出並附一鍵下架。疑似指控與推測內容第一次公開、
  內容改變或撤下後再公開時必定產生提醒；只有件數或其他內容改變時不重複提醒。

查證依據（`confirmed_by`）：
- user：最近一次把事件改判為查證結果（verified、disputed、false）的 `event.verify` 稽核是 verified；
- official：最新一次成功的 S05 判定某份仍連結的證據為官方來源且支持事件（與個人通知相同）；
- first_hand：以上皆非（例如只有兩份第一手來源支持），不足以公開內容。
monitoring、resolved 沿用進入後續追蹤前的查證結果（從 `event.verify` 稽核的 from／to 推得）。

單一事件小報告停用（P8-T13，ADR-0020 決策 3-5）：公開事件頁（快報全文）不再帶小報告的影響、
原因與措施（`report` 一律為 None）；原因、改善建議與改善進度改由系列分析頁公開（P8-T06），
事件的 `progress` 一律為空（P8-T13 已把事件層級的措施掛到所屬系列）。

Celery beat 每分鐘執行一次 `sync_all()`（先事件、後系列，系列依事件的公開狀態）；公開 API 只讀取
這裡寫入的結果。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, func, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import NOTIFIER_PRINCIPAL, _has_official_source
from app.analytics.timeseries import detect_changes
from app.domain.states import EventStatus, VerificationStatus
from app.models.ai import RiskAssessment
from app.models.audit import AuditLog
from app.models.event import Event, EventEvidence, Location
from app.models.publication import Publication, PublicationVersion
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.models.series import EventSeries, EventSeriesMember
from app.models.source import Source, SourceItem
from app.publishing.policy import (
    ActionInput,
    BurstInput,
    ConfirmedBy,
    PublicationDecision,
    PublicationInput,
    PublicLevel,
    PublicSource,
    PublishPolicy,
    SeriesAnalysisInput,
    SeriesMemberInput,
    SeriesPublicationInput,
    evaluate,
    evaluate_series,
    load_policy,
    review_parts,
)
from app.reports.cause_decisions import confirmed_cause_ids
from app.reports.service import latest_report
from app.series.analysis import (
    CauseDecision,
    current_decisions,
    event_date,
    latest_analysis,
    taipei_date,
    weekly_counts,
)
from app.series.timeline import ActionMarker, before_after, build_timeline
from app.services.audit import record_audit
from app.worker.celery_app import SYNC_PUBLICATIONS_TASK, celery_app

TAIPEI = ZoneInfo("Asia/Taipei")
_JUDGED = frozenset({EventStatus.VERIFIED, EventStatus.DISPUTED, EventStatus.FALSE})
_FOLLOW_UP = frozenset({EventStatus.MONITORING, EventStatus.RESOLVED})
ARCHIVED_REASON = "event_archived"
REMIND = "remind"


class SyncAction(StrEnum):
    """`sync_publication` 這次做了什麼。"""

    NONE = "none"  # 沒有改變（含不公開且從未公開）
    PUBLISHED = "published"  # 新公開，或撤下後重新公開
    UPDATED = "updated"  # 公開中的內容改變，升版
    WITHDRAWN = "withdrawn"  # 不再符合公開規則，撤下
    KEPT_DOWN = "kept_down"  # 已下架，不覆蓋


_AUDIT_ACTIONS = {
    SyncAction.PUBLISHED: "publication.publish",
    SyncAction.UPDATED: "publication.update",
    SyncAction.WITHDRAWN: "publication.withdraw",
}


def content_hash(content: dict[str, Any] | None) -> str | None:
    """公開內容的 SHA-256（鍵排序後的 JSON）；沒有內容為 None。"""
    if content is None:
        return None
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def _verify_history(session: AsyncSession, event_id: uuid.UUID) -> list[dict[str, Any]]:
    """事件的 `event.verify` 稽核 metadata（新到舊）。"""
    rows = await session.scalars(
        select(AuditLog.meta)
        .where(AuditLog.action == "event.verify", AuditLog.target == f"event:{event_id}")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    )
    return [m for m in rows if isinstance(m, dict)]


def _status(value: Any) -> EventStatus | None:
    try:
        return EventStatus(value)
    except ValueError:
        return None


def _last_judgement(history: list[dict[str, Any]]) -> EventStatus | None:
    """使用者最近一次做出、目前仍有效的查證結果（verified、disputed、false）。

    改判為查證結果時取 to；進入後續追蹤（monitoring、resolved）時取進入前的 from，
    from 也是後續追蹤時再往前找；改回 unverified 或封存表示沒有有效的查證結果。
    """
    for entry in history:
        to, before = _status(entry.get("to")), _status(entry.get("from"))
        if to in _JUDGED:
            return to
        if to not in _FOLLOW_UP:
            return None
        if before in _JUDGED:
            return before
    return None


async def _verification(
    session: AsyncSession, event: Event
) -> tuple[VerificationStatus, ConfirmedBy | None]:
    status = EventStatus(event.status)
    history = await _verify_history(session, event.id)
    last = _last_judgement(history)
    if status in _FOLLOW_UP:
        status = last or EventStatus.UNVERIFIED
    if status not in _JUDGED:
        return VerificationStatus.UNVERIFIED, None
    verification = VerificationStatus(status.value)
    if verification is not VerificationStatus.VERIFIED:
        return verification, None
    if last is EventStatus.VERIFIED:
        return verification, ConfirmedBy.USER
    if await _has_official_source(session, event.id):
        return verification, ConfirmedBy.OFFICIAL
    return verification, ConfirmedBy.FIRST_HAND


def _taipei_date(value: Any) -> date | None:
    return value.astimezone(TAIPEI).date() if value is not None else None


async def publication_input(session: AsyncSession, event: Event) -> PublicationInput:
    """從資料庫組出 P5-T10 規則需要的資料。"""
    verification, confirmed_by = await _verification(session, event)
    risk_level = await session.scalar(
        select(RiskAssessment.level)
        .where(RiskAssessment.event_id == event.id)
        .order_by(RiskAssessment.created_at.desc(), RiskAssessment.id.desc())
        .limit(1)
    )
    location = (
        await session.scalar(select(Location.name).where(Location.id == event.location_id))
        if event.location_id is not None
        else None
    )
    source_rows = await session.execute(
        select(Source.code, Source.name, SourceItem.url)
        .join(SourceItem, SourceItem.source_id == Source.id)
        .join(EventEvidence, EventEvidence.source_item_id == SourceItem.id)
        .where(EventEvidence.event_id == event.id)
        .order_by(SourceItem.published_at, SourceItem.id)
    )
    return PublicationInput(
        event_id=event.id,
        event_type=event.type,
        title=event.title,
        summary=event.summary,
        risk_level=risk_level,
        verification=verification,
        confirmed_by=confirmed_by,
        location_name=location,
        occurrence_date=event.occurrence_start_date,
        sources=[PublicSource(code, name, url) for code, name, url in source_rows],
        report=None,  # 單一事件小報告停用（P8-T13）
        actions=(),  # 改善進度改在系列分析頁公開（P8-T06）
    )


async def _decide(
    session: AsyncSession, event: Event, policy: PublishPolicy
) -> PublicationDecision:
    if event.status == EventStatus.ARCHIVED.value:
        return PublicationDecision(
            event.id, PublicLevel.NONE, None, (ARCHIVED_REASON,), False, policy.policy_version
        )
    return evaluate(await publication_input(session, event), policy)


# ---------- 系列 ----------


async def _public_members(
    session: AsyncSession, series_id: uuid.UUID
) -> list[tuple[Event, Publication, dict[str, Any]]]:
    """系列中目前以事件內容公開中的子事件與它的公開快照，依事件日期排序。"""
    rows = (
        await session.execute(
            select(Event, Publication, PublicationVersion.content)
            .join(EventSeriesMember, EventSeriesMember.event_id == Event.id)
            .join(Publication, Publication.event_id == Event.id)
            .join(
                PublicationVersion,
                and_(
                    PublicationVersion.publication_id == Publication.id,
                    PublicationVersion.version == Publication.version,
                ),
            )
            .where(
                EventSeriesMember.series_id == series_id,
                Publication.state == "published",
                Publication.level == PublicLevel.CONTENT.value,
                PublicationVersion.content.is_not(None),
            )
        )
    ).all()
    members = [(e, p, c) for e, p, c in rows if isinstance(c, dict)]
    return sorted(members, key=lambda m: (event_date(m[0]), m[0].created_at, str(m[0].id)))


async def action_markers(
    session: AsyncSession, actions: list[ResponseAction]
) -> list[ActionMarker]:
    """措施第一次轉為處理（started）與改進（completed）的台北日期。"""
    if not actions:
        return []
    titles = {a.id: a.title for a in actions}
    rows = await session.execute(
        select(
            ResponseActionTransition.action_id,
            ResponseActionTransition.to_state,
            func.min(ResponseActionTransition.created_at),
        )
        .where(
            ResponseActionTransition.action_id.in_(titles),
            ResponseActionTransition.to_state.in_(("handling", "improved")),
        )
        .group_by(ResponseActionTransition.action_id, ResponseActionTransition.to_state)
    )
    markers = [
        ActionMarker(
            action_id,
            titles[action_id],
            "started" if state == "handling" else "completed",
            taipei_date(at),
        )
        for action_id, state, at in rows.all()
    ]
    return sorted(markers, key=lambda m: (m.on, m.kind, str(m.action_id)))


async def _legacy_confirmed(session: AsyncSession, actions: list[ResponseAction]) -> set[uuid.UUID]:
    """由舊小報告措施建立、依據的推測原因已人工確認的改善措施（P7-T07，唯讀）。

    只有來自該事件最新有效報告、且原因已確認時才算確認（舊報告的確認不沿用）。
    """
    found: set[uuid.UUID] = set()
    for a in actions:
        if a.report_id is None or a.event_id is None:
            continue
        report = await latest_report(session, a.event_id)
        if report is None or report.invalidated_at is not None or report.id != a.report_id:
            continue
        if str((a.measure or {}).get("cause_id")) in await confirmed_cause_ids(session, report.id):
            found.add(a.id)
    return found


async def series_input(
    session: AsyncSession, series: EventSeries, *, today: date | None = None
) -> SeriesPublicationInput:
    """從資料庫組出系列分析頁的公開規則輸入；件數與時間軸只依公開中的子事件。"""
    today = today or taipei_date(datetime.now(UTC))
    members = await _public_members(session, series.id)
    dates = [event_date(e) for e, _, _ in members]
    analysis = await latest_analysis(session, series.id)
    analysis_input = None
    if analysis is not None:
        decisions = await current_decisions(session, analysis.id)
        analysis_input = SeriesAnalysisInput(
            summary=analysis.summary,
            location_groups=analysis.location_groups or [],
            patterns=analysis.patterns or [],
            causes=analysis.causes or [],
            measures=analysis.measures or [],
            confirmed_cause_ids=frozenset(
                cause_id
                for cause_id, d in decisions.items()
                if d.decision == CauseDecision.CONFIRMED.value
            ),
        )
    actions = list(
        await session.scalars(
            select(ResponseAction)
            .where(ResponseAction.series_id == series.id)
            .order_by(ResponseAction.created_at, ResponseAction.id)
        )
    )
    legacy = await _legacy_confirmed(session, actions)
    markers = await action_markers(session, actions)
    timeline = (
        build_timeline(dates, granularity="week", range_key="all", today=today, markers=markers)
        if dates
        else None
    )
    comparisons = [
        (m.title, before_after(dates, m.on, today=today)) for m in markers if m.kind == "started"
    ]
    bursts = [
        BurstInput(a.period_start, a.observed, a.expected)
        for a in detect_changes(weekly_counts(dates, today))
    ]
    return SeriesPublicationInput(
        series_id=series.id,
        category=series.category,
        name=series.title,
        members=[
            SeriesMemberInput(
                event_id=e.id,
                publication_id=p.id,
                title=str(c.get("title") or ""),
                occurrence_date=date.fromisoformat(c["occurrence_date"])
                if c.get("occurrence_date")
                else None,
                location=c.get("location"),
                verified=c.get("verification") == "verified",
            )
            for e, p, c in members
        ],
        analysis=analysis_input,
        actions=[
            ActionInput(
                title=a.title,
                state=a.state,
                due_date=_taipei_date(a.due_at),
                updated_at=a.updated_at,
                cause_basis=(a.measure or {}).get("cause_basis") if a.report_id else None,
                cause_confirmed=a.id in legacy,
            )
            for a in actions
        ],
        timeline=timeline,
        comparisons=comparisons,
        bursts=bursts,
    )


# ---------- 寫入 ----------


async def _current_version(session: AsyncSession, pub: Publication) -> PublicationVersion | None:
    return await session.scalar(
        select(PublicationVersion).where(
            PublicationVersion.publication_id == pub.id,
            PublicationVersion.version == pub.version,
        )
    )


def _add_version(
    session: AsyncSession,
    pub: Publication,
    decision: PublicationDecision,
    digest: str | None,
    reasons: list[str],
) -> None:
    session.add(
        PublicationVersion(
            publication_id=pub.id,
            version=pub.version,
            level=decision.level.value,
            # JSONB 欄位的 None 會被寫成 JSON null；無內容必須是 SQL NULL（content 約束）
            content=decision.content if decision.content is not None else null(),
            content_hash=digest,
            policy_version=decision.policy_version,
            reasons=reasons,
        )
    )


def subject_metadata(pub: Publication) -> dict[str, Any]:
    """稽核 metadata 中的公開對象：事件為 event_id，系列為 series_id。"""
    return {"series_id": pub.series_id} if pub.series_id is not None else {"event_id": pub.event_id}


async def _audit(
    session: AsyncSession, action: SyncAction, pub: Publication, decision: PublicationDecision
) -> None:
    await record_audit(
        session,
        NOTIFIER_PRINCIPAL,
        _AUDIT_ACTIONS[action],
        target=f"publication:{pub.id}",
        metadata={
            **subject_metadata(pub),
            "version": pub.version,
            "level": decision.level.value,
            "reasons": list(pub.reasons),
            "policy_version": decision.policy_version,
        },
    )


async def _locked(session: AsyncSession, where: ColumnElement[bool]) -> Publication | None:
    # 鎖住公開狀態：與一鍵下架（takedown.py）互斥，避免把剛下架的狀態寫回 published
    return await session.scalar(
        select(Publication).where(where).with_for_update().execution_options(populate_existing=True)
    )


def _remind(current: PublicationVersion | None, decision: PublicationDecision) -> bool:
    """需要使用者注意的內容第一次公開或改變了。"""
    if decision.level is PublicLevel.NONE:
        return False
    new = review_parts(decision.content, decision.reasons)
    if not new:
        return False
    if current is None or current.level == PublicLevel.NONE.value:
        return True
    return new != review_parts(current.content, current.reasons or [])


async def _apply(
    session: AsyncSession,
    pub: Publication | None,
    decision: PublicationDecision,
    *,
    event_id: uuid.UUID | None = None,
    series_id: uuid.UUID | None = None,
) -> SyncAction:
    digest = content_hash(decision.content)
    publishable = decision.level is not PublicLevel.NONE
    reasons = list(decision.reasons)

    if pub is None:
        if not publishable:
            return SyncAction.NONE
        if _remind(None, decision):
            reasons.append(REMIND)
        pub = Publication(
            id=uuid.uuid4(),
            event_id=event_id,
            series_id=series_id,
            level=decision.level.value,
            state="published",
            version=1,
            policy_version=decision.policy_version,
            reasons=reasons,
            needs_review=decision.needs_review,
        )
        session.add(pub)
        await session.flush()
        _add_version(session, pub, decision, digest, reasons)
        await _audit(session, SyncAction.PUBLISHED, pub, decision)
        return SyncAction.PUBLISHED

    current = await _current_version(session, pub)
    changed = current is None or (current.level, current.content_hash) != (
        decision.level.value,
        digest,
    )
    if changed and _remind(current, decision):
        reasons.append(REMIND)
    elif not changed and current is not None and REMIND in (current.reasons or []):
        reasons.append(REMIND)  # 沒有新版本時保留目前版本的提醒
    pub.reasons = reasons
    pub.needs_review = decision.needs_review
    pub.policy_version = decision.policy_version
    if not publishable:
        if pub.state != "published":
            return SyncAction.NONE
        action = SyncAction.WITHDRAWN
        pub.state = "withdrawn"
    elif pub.state != "published":
        action = SyncAction.PUBLISHED
        pub.state = "published"
    elif changed:
        action = SyncAction.UPDATED
    else:
        return SyncAction.NONE
    if changed:
        pub.version += 1
        pub.level = decision.level.value
        _add_version(session, pub, decision, digest, reasons)
    await session.flush()
    await _audit(session, action, pub, decision)
    return action


async def sync_publication(
    session: AsyncSession, event_id: uuid.UUID, *, policy: PublishPolicy | None = None
) -> SyncAction:
    """依公開規則更新一個事件的公開狀態（flush，不 commit）。"""
    event = await session.get(Event, event_id)
    if event is None:
        return SyncAction.NONE
    pub = await _locked(session, Publication.event_id == event_id)
    if pub is not None and pub.state == "taken_down":
        return SyncAction.KEPT_DOWN
    decision = await _decide(session, event, policy or load_policy())
    return await _apply(session, pub, decision, event_id=event_id)


async def sync_series_publication(
    session: AsyncSession,
    series_id: uuid.UUID,
    *,
    policy: PublishPolicy | None = None,
    today: date | None = None,
) -> SyncAction:
    """依公開規則更新一個系列分析頁的公開狀態（flush，不 commit）；先同步子事件的公開狀態。"""
    series = await session.get(EventSeries, series_id)
    if series is None:
        return SyncAction.NONE
    pub = await _locked(session, Publication.series_id == series_id)
    if pub is not None and pub.state == "taken_down":
        return SyncAction.KEPT_DOWN
    item = await series_input(session, series, today=today)
    decision = evaluate_series(item, policy or load_policy())
    return await _apply(session, pub, decision, series_id=series_id)


async def sync_subject(session: AsyncSession, pub: Publication) -> SyncAction:
    """依 publication 的對象（事件或系列）重新同步（一鍵下架的重新上架用）。"""
    if pub.series_id is not None:
        return await sync_series_publication(session, pub.series_id)
    if pub.event_id is not None:
        return await sync_publication(session, pub.event_id)
    return SyncAction.NONE


async def sync_all(
    session: AsyncSession, *, policy: PublishPolicy | None = None
) -> Counter[SyncAction]:
    """同步所有事件，再同步所有系列；每個各自 commit，回傳各種結果的數量。"""
    policy = policy or load_policy()
    counts: Counter[SyncAction] = Counter()
    event_ids = list(await session.scalars(select(Event.id).order_by(Event.created_at)))
    for event_id in event_ids:
        counts[await sync_publication(session, event_id, policy=policy)] += 1
        await session.commit()
    series_ids = list(
        await session.scalars(select(EventSeries.id).order_by(EventSeries.created_at))
    )
    for series_id in series_ids:
        counts[await sync_series_publication(session, series_id, policy=policy)] += 1
        await session.commit()
    return counts


# ---------- Celery 任務 ----------


async def _in_worker() -> None:
    from app.worker.tasks.ingest import open_sessionmaker  # 避免 API 匯入時載入擷取模組

    async with open_sessionmaker() as maker, maker() as session:
        await sync_all(session)


@celery_app.task(name=SYNC_PUBLICATIONS_TASK)
def sync_publications_task() -> None:
    """Celery 任務：依公開規則同步所有事件的公開狀態（beat 每分鐘一次）。"""
    asyncio.run(_in_worker())


__all__ = [
    "REMIND",
    "SyncAction",
    "action_markers",
    "content_hash",
    "publication_input",
    "series_input",
    "subject_metadata",
    "sync_all",
    "sync_publication",
    "sync_publications_task",
    "sync_series_publication",
    "sync_subject",
]
