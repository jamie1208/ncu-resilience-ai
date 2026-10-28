"""公開狀態同步（P5-T11）：把公開規則（`policy.py`）的判斷結果寫入 publications。

從事件、證據、風險評估、最新有效的小報告、改善措施與查證稽核組出 `PublicationInput`，
交給 `evaluate()` 判斷，再寫入 publications 與 publication_versions：

- 內容（content_hash）與公開程度都沒變時不升版；改變時新增一筆版本快照並升版；
- 判斷為不公開（含事件已封存）時，公開中的事件改為 withdrawn，並新增一筆無內容的版本；
- 已下架（taken_down，P5-T14）的事件一律不覆蓋，重新上架只能由使用者在後台操作；
- 每次公開、更新、撤下都以通知服務帳號寫入稽核（只記代碼與版本，不記內容）。

查證依據（`confirmed_by`）：
- user：最近一次把事件改判為查證結果（verified、disputed、false）的 `event.verify` 稽核是 verified；
- official：最新一次成功的 S05 判定某份仍連結的證據為官方來源且支持事件（與個人通知相同）；
- first_hand：以上皆非（例如只有兩份第一手來源支持），不足以公開內容。
monitoring、resolved 沿用進入後續追蹤前的查證結果（從 `event.verify` 稽核的 from／to 推得）。

Celery beat 每分鐘執行一次 `sync_all()`；公開 API 只讀取這裡寫入的結果。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from datetime import date
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import null, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import NOTIFIER_PRINCIPAL, _has_official_source
from app.domain.states import EventStatus, VerificationStatus
from app.models.ai import RiskAssessment
from app.models.audit import AuditLog
from app.models.event import Event, EventEvidence, Location
from app.models.publication import Publication, PublicationVersion
from app.models.response_action import ResponseAction
from app.models.source import Source, SourceItem
from app.publishing.policy import (
    ActionInput,
    ConfirmedBy,
    PublicationDecision,
    PublicationInput,
    PublicLevel,
    PublicSource,
    PublishPolicy,
    ReportInput,
    evaluate,
    load_policy,
)
from app.reports.service import latest_report
from app.services.audit import record_audit
from app.worker.celery_app import SYNC_PUBLICATIONS_TASK, celery_app

TAIPEI = ZoneInfo("Asia/Taipei")
_JUDGED = frozenset({EventStatus.VERIFIED, EventStatus.DISPUTED, EventStatus.FALSE})
_FOLLOW_UP = frozenset({EventStatus.MONITORING, EventStatus.RESOLVED})
ARCHIVED_REASON = "event_archived"


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
    report = await latest_report(session, event.id)
    report_input = None
    if report is not None and report.invalidated_at is None:
        report_input = ReportInput(
            impacts=report.impacts, causes=report.causes, measures=report.measures
        )
    actions = await session.scalars(
        select(ResponseAction)
        .where(ResponseAction.event_id == event.id)
        .order_by(ResponseAction.created_at, ResponseAction.id)
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
        report=report_input,
        actions=[
            ActionInput(
                title=a.title,
                state=a.state,
                due_date=_taipei_date(a.due_at),
                updated_at=a.updated_at,
                cause_basis=(a.measure or {}).get("cause_basis"),
            )
            for a in actions
        ],
    )


async def _decide(
    session: AsyncSession, event: Event, policy: PublishPolicy
) -> PublicationDecision:
    if event.status == EventStatus.ARCHIVED.value:
        return PublicationDecision(
            event.id, PublicLevel.NONE, None, (ARCHIVED_REASON,), False, policy.policy_version
        )
    return evaluate(await publication_input(session, event), policy)


async def _current_version(session: AsyncSession, pub: Publication) -> PublicationVersion | None:
    return await session.scalar(
        select(PublicationVersion).where(
            PublicationVersion.publication_id == pub.id,
            PublicationVersion.version == pub.version,
        )
    )


def _add_version(
    session: AsyncSession, pub: Publication, decision: PublicationDecision, digest: str | None
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
            reasons=list(decision.reasons),
        )
    )


async def _audit(
    session: AsyncSession, action: SyncAction, pub: Publication, decision: PublicationDecision
) -> None:
    await record_audit(
        session,
        NOTIFIER_PRINCIPAL,
        _AUDIT_ACTIONS[action],
        target=f"publication:{pub.id}",
        metadata={
            "event_id": pub.event_id,
            "version": pub.version,
            "level": decision.level.value,
            "reasons": list(decision.reasons),
            "policy_version": decision.policy_version,
        },
    )


async def sync_publication(
    session: AsyncSession, event_id: uuid.UUID, *, policy: PublishPolicy | None = None
) -> SyncAction:
    """依公開規則更新一個事件的公開狀態（flush，不 commit）。"""
    event = await session.get(Event, event_id)
    if event is None:
        return SyncAction.NONE
    pub = await session.scalar(select(Publication).where(Publication.event_id == event_id))
    if pub is not None and pub.state == "taken_down":
        return SyncAction.KEPT_DOWN
    decision = await _decide(session, event, policy or load_policy())
    digest = content_hash(decision.content)
    publishable = decision.level is not PublicLevel.NONE

    if pub is None:
        if not publishable:
            return SyncAction.NONE
        pub = Publication(
            id=uuid.uuid4(),
            event_id=event_id,
            level=decision.level.value,
            state="published",
            version=1,
            policy_version=decision.policy_version,
            reasons=list(decision.reasons),
            needs_review=decision.needs_review,
        )
        session.add(pub)
        await session.flush()
        _add_version(session, pub, decision, digest)
        await _audit(session, SyncAction.PUBLISHED, pub, decision)
        return SyncAction.PUBLISHED

    pub.reasons = list(decision.reasons)
    pub.needs_review = decision.needs_review
    pub.policy_version = decision.policy_version
    current = await _current_version(session, pub)
    changed = current is None or (current.level, current.content_hash) != (
        decision.level.value,
        digest,
    )
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
        _add_version(session, pub, decision, digest)
    await session.flush()
    await _audit(session, action, pub, decision)
    return action


async def sync_all(
    session: AsyncSession, *, policy: PublishPolicy | None = None
) -> Counter[SyncAction]:
    """同步所有事件；每個事件各自 commit，回傳各種結果的數量。"""
    policy = policy or load_policy()
    counts: Counter[SyncAction] = Counter()
    event_ids = list(await session.scalars(select(Event.id).order_by(Event.created_at)))
    for event_id in event_ids:
        counts[await sync_publication(session, event_id, policy=policy)] += 1
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
    "SyncAction",
    "content_hash",
    "publication_input",
    "sync_all",
    "sync_publication",
    "sync_publications_task",
]
