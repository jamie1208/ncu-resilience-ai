"""P4-T08：事件說明（摘要）。

- S02 輸出的 `summary`（一到三句中性說明＋evidence_ids）在建立事件時寫入 events。
- 同事件多篇來源時以證據最強（官方來源優先）、其次最新（發布時間）的一篇為準；
  併入的文章沒有比較強或比較新時不覆寫，沒有摘要時也不覆寫。
- 摘要含個資時 Runner 拒絕（驗證失敗重試後記為 abstain），不建立事件、不寫入摘要。
- `GET /api/v1/admin/events/{id}` 回傳摘要；沒有摘要時為 null。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.domain.states import EventStatus
from app.main import create_app
from app.models import Event, EventEvidence
from app.models.ai import AIAnalysisRun
from app.models.source import SourceItem
from app.pipeline.analyze_item import Outcome, analyze_item
from tests.integration.test_pipeline import (
    ADMIN,
    NOTICE,
    PUBLISHED,
    REPOST,
    VECTOR,
    SkillScript,
    add_item,
    add_source,
    classification,
    deps,
    eid,
    extraction,
    linking,
    relevance,
    risk,
    same_vector_embedder,
    verification,
)

pytestmark = pytest.mark.anyio

OFFICIAL_SUMMARY = "科學一館 10/22 上午停電檢修，期間電梯與照明暫停。"
REPOST_SUMMARY = "有同學反映科學一館電梯停止、走廊燈全暗。"
OLD_SUMMARY = "科學一館停電，原因尚待確認。"


def extracted(e: str, text: str | None) -> dict[str, Any]:
    """S02 輸出，摘要換成指定文字（None 表示模型沒有給摘要）。"""
    summary = {"text": text, "evidence_ids": [e]} if text is not None else None
    return {**extraction(e), "summary": summary}


async def runs_of(session: AsyncSession, skill: str) -> list[AIAnalysisRun]:
    rows = await session.scalars(
        select(AIAnalysisRun)
        .where(AIAnalysisRun.skill_name == skill)
        .order_by(AIAnalysisRun.created_at)
    )
    return list(rows)


async def existing_event(
    session: AsyncSession,
    *,
    source_code: str,
    summary: str | None,
    published_at: datetime = PUBLISHED,
    summary_updated_at: datetime | None = None,
) -> tuple[Event, SourceItem]:
    """已存在的事件（向量為 VECTOR，可被轉貼文章召回），摘要來自它唯一的證據。"""
    item = await add_item(session, await add_source(session, source_code), NOTICE)
    item.published_at = published_at
    event = Event(
        title="科學一館停電檢修",
        type="facility",
        status=EventStatus.UNVERIFIED.value,
        occurrence_start_date=date(2026, 10, 22),
        embedding=VECTOR,
    )
    if summary is not None:
        event.summary = summary
        event.summary_evidence_ids = [item.id.hex]
        event.summary_source_item_id = item.id
        event.summary_updated_at = summary_updated_at
    session.add(event)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="mentions"))
    await session.flush()
    return event, item


async def link_repost(
    session: AsyncSession,
    event: Event,
    existing_item: SourceItem,
    *,
    source_code: str,
    summary: str | None,
    published_at: datetime,
) -> SourceItem:
    """把一篇轉貼文章併入既有事件（S04 same_event）。"""
    item = await add_item(session, await add_source(session, source_code), REPOST)
    item.published_at = published_at
    await session.flush()
    e, old = eid(item), existing_item.id.hex
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extracted(e, summary)],
            "event-classifier": [classification(e)],
            "event-linker": [linking(e, old, "same_event")],
            "evidence-verifier": [
                verification(
                    {old: ("supports", "first_hand"), e: ("supports", "first_hand")}, "unverified"
                )
            ],
            "risk-assessor": [risk(e)],
        }
    )
    result = await analyze_item(session, item.id, deps(provider, same_vector_embedder()))
    assert result.outcome is Outcome.LINKED
    assert result.event_id == event.id
    await session.refresh(event)
    return item


# ---------- 寫入 ----------


async def test_new_event_stores_summary_with_evidence_and_run(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "ncu-announcements"), NOTICE)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extracted(e, OFFICIAL_SUMMARY)],
            "event-classifier": [classification(e)],
            "evidence-verifier": [verification({e: ("supports", "official")}, "verified")],
            "risk-assessor": [risk(e)],
        }
    )

    result = await analyze_item(session, item.id, deps(provider))

    assert result.outcome is Outcome.CREATED
    event = await session.get(Event, result.event_id)
    assert event is not None
    assert event.summary == OFFICIAL_SUMMARY
    assert event.summary_evidence_ids == [e]
    assert event.summary_source_item_id == item.id
    (s02,) = await runs_of(session, "event-extractor")
    assert event.summary_ai_run_id == s02.id
    assert event.summary_updated_at is not None
    assert event.summary_updated_at.tzinfo is not None


async def test_new_event_without_summary_leaves_it_empty(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    provider = SkillScript(
        {
            "relevance-filter": [relevance(e)],
            "event-extractor": [extracted(e, None)],
            "event-classifier": [classification(e)],
            "evidence-verifier": [verification({e: ("supports", "first_hand")}, "unverified")],
            "risk-assessor": [risk(e)],
        }
    )

    result = await analyze_item(session, item.id, deps(provider))

    event = await session.get(Event, result.event_id)
    assert event is not None
    assert event.summary is None
    assert event.summary_evidence_ids is None
    assert event.summary_source_item_id is None
    assert event.summary_ai_run_id is None


# ---------- 更新：證據最強、其次最新 ----------


async def test_newer_article_of_same_strength_replaces_summary(session: AsyncSession) -> None:
    event, old = await existing_event(session, source_code="dcard", summary=OLD_SUMMARY)

    item = await link_repost(
        session,
        event,
        old,
        source_code="facebook",
        summary=REPOST_SUMMARY,
        published_at=PUBLISHED + timedelta(hours=2),
    )

    assert event.summary == REPOST_SUMMARY
    assert event.summary_evidence_ids == [eid(item)]
    assert event.summary_source_item_id == item.id
    s02 = await runs_of(session, "event-extractor")
    assert event.summary_ai_run_id == s02[-1].id


async def test_older_article_of_same_strength_keeps_summary(session: AsyncSession) -> None:
    event, old = await existing_event(session, source_code="dcard", summary=OLD_SUMMARY)

    await link_repost(
        session,
        event,
        old,
        source_code="facebook",
        summary=REPOST_SUMMARY,
        published_at=PUBLISHED - timedelta(hours=2),
    )

    assert event.summary == OLD_SUMMARY
    assert event.summary_source_item_id == old.id


async def test_newer_social_post_does_not_replace_official_summary(session: AsyncSession) -> None:
    event, old = await existing_event(
        session, source_code="ncu-announcements", summary=OFFICIAL_SUMMARY
    )

    await link_repost(
        session,
        event,
        old,
        source_code="facebook",
        summary=REPOST_SUMMARY,
        published_at=PUBLISHED + timedelta(days=1),
    )

    assert event.summary == OFFICIAL_SUMMARY
    assert event.summary_source_item_id == old.id


async def test_official_article_replaces_newer_social_summary(session: AsyncSession) -> None:
    event, old = await existing_event(
        session,
        source_code="facebook",
        summary=REPOST_SUMMARY,
        published_at=PUBLISHED + timedelta(days=1),
    )

    item = await link_repost(
        session,
        event,
        old,
        source_code="ncu-announcements",
        summary=OFFICIAL_SUMMARY,
        published_at=PUBLISHED,
    )

    assert event.summary == OFFICIAL_SUMMARY
    assert event.summary_source_item_id == item.id


async def test_linked_article_fills_missing_summary(session: AsyncSession) -> None:
    event, old = await existing_event(session, source_code="ncu-announcements", summary=None)

    item = await link_repost(
        session,
        event,
        old,
        source_code="facebook",
        summary=REPOST_SUMMARY,
        published_at=PUBLISHED - timedelta(days=1),
    )

    assert event.summary == REPOST_SUMMARY
    assert event.summary_source_item_id == item.id


async def test_linked_article_without_summary_keeps_existing(session: AsyncSession) -> None:
    event, old = await existing_event(session, source_code="dcard", summary=OLD_SUMMARY)

    await link_repost(
        session,
        event,
        old,
        source_code="ncu-announcements",
        summary=None,
        published_at=PUBLISHED + timedelta(days=1),
    )

    assert event.summary == OLD_SUMMARY
    assert event.summary_source_item_id == old.id


# ---------- 個資 ----------


async def test_summary_with_pii_is_rejected(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    leaked = extracted(e, "科學一館停電，有問題請打 0912-345-678 或寄 a1234567@cc.ncu.edu.tw。")
    provider = SkillScript(
        {"relevance-filter": [relevance(e)], "event-extractor": [leaked, leaked, leaked]}
    )

    result = await analyze_item(session, item.id, deps(provider))

    assert result.outcome is Outcome.STOPPED
    assert result.stopped_at == "event-extractor"
    assert (await session.scalars(select(Event))).all() == []
    (s02,) = await runs_of(session, "event-extractor")
    assert s02.status == "abstained"
    assert "pii" in s02.validation_issues


async def test_summary_citing_unknown_evidence_is_rejected(session: AsyncSession) -> None:
    item = await add_item(session, await add_source(session, "facebook"), REPOST)
    e = eid(item)
    forged = {**extraction(e), "summary": {"text": REPOST_SUMMARY, "evidence_ids": ["e9"]}}
    provider = SkillScript(
        {"relevance-filter": [relevance(e)], "event-extractor": [forged, forged, forged]}
    )

    result = await analyze_item(session, item.id, deps(provider))

    assert result.stopped_at == "event-extractor"
    (s02,) = await runs_of(session, "event-extractor")
    assert "evidence" in s02.validation_issues


# ---------- API ----------


@pytest.fixture()
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    app.dependency_overrides[get_principal] = lambda: ADMIN
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_event_detail_returns_summary(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    event, item = await existing_event(
        session,
        source_code="ncu-announcements",
        summary=OFFICIAL_SUMMARY,
        summary_updated_at=datetime(2026, 10, 20, 2, 0, tzinfo=UTC),
    )
    event_id, item_id = event.id, item.id
    await session.commit()

    response = await client.get(f"/api/v1/admin/events/{event_id}")

    assert response.status_code == 200
    assert response.json()["data"]["summary"] == {
        "text": OFFICIAL_SUMMARY,
        "evidence_ids": [item_id.hex],
        "source_item_id": str(item_id),
        "ai_run_id": None,
        "updated_at": "2026-10-20T02:00:00Z",
    }


async def test_event_detail_without_summary_is_null(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    event, _ = await existing_event(session, source_code="facebook", summary=None)
    event_id = event.id
    await session.commit()

    response = await client.get(f"/api/v1/admin/events/{event_id}")

    assert response.status_code == 200
    assert response.json()["data"]["summary"] is None
