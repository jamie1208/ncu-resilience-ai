"""P5-T11：公開唯讀 API（/api/v1/public/*）與公開狀態同步（app/publishing/sync.py）。

P8-T06（ADR-0018 2-1，使用者 2026-11-05 同意修改，只限放寬的規則）：未查證、有爭議與只有第一手
來源的事件改為公開 AI 摘要並標「尚未查證」，不再改寫成安全提醒；改善進度改以系列為單位。

- 同步：從事件、證據、風險評估、最新有效的小報告、改善措施與查證稽核組出 P5-T10 的輸入，
  寫入 publications 與版本快照；內容沒變不升版；不公開時撤下；已下架（P5-T14）的不覆蓋。
- 公開 API：只讀取 state 為 published 的目前版本；免登入、只有 GET；cursor 分頁、
  rate limit（429 RATE_LIMITED + Retry-After）、快取標頭（Cache-Control、ETag、304）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.v1.public.route import RateLimiter
from app.core.security import Principal, Role
from app.domain.states import EventStatus
from app.main import create_app
from app.models import AuditLog
from app.models.ai import AIAnalysisRun, RiskAssessment
from app.models.event import Event, EventEvidence, Location
from app.models.publication import Publication, PublicationVersion
from app.models.report import EventReport
from app.models.response_action import ResponseAction
from app.models.series import EventSeries, EventSeriesMember
from app.models.source import Source, SourceItem, SourceItemVersion
from app.publishing.sync import SyncAction, sync_all, sync_publication
from app.services.audit import record_audit

pytestmark = pytest.mark.anyio

PUBLIC = "/api/v1/public"
ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
PUBLISHED_AT = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


# ---------- 種子資料 ----------


@dataclass(frozen=True)
class Evidence:
    """一份證據：來源代碼、原文與網址。"""

    source_code: str
    text: str
    url: str = ""


OFFICIAL = Evidence("ncu-announcements", "總圖書館三樓天花板漏水，三樓座位暫停開放。")


async def _source(session: AsyncSession, code: str) -> Source:
    existing = await session.scalar(select(Source).where(Source.code == code))
    if existing is not None:
        return existing
    name = {"ncu-announcements": "中央大學最新消息", "facebook": "Facebook NCU 社團"}.get(
        code, code
    )
    src = Source(code=code, name=name, type="fake", access_policy="approved", poll_interval=3600)
    session.add(src)
    await session.flush()
    return src


def measure(action: str, *, cause_basis: str | None = "confirmed") -> dict[str, Any]:
    """S08 的一項措施。"""
    return {
        "action": action,
        "audience": "students",
        "horizon": "short",
        "priority": "high",
        "kind": "mitigate",
        "responsible_unit": "總務處",
        "cause_id": "c1" if cause_basis else None,
        "cause_basis": cause_basis,
        "evidence_ids": [],
    }


async def add_event(
    session: AsyncSession,
    *,
    title: str = "總圖書館三樓漏水",
    event_type: str = "facility",
    status: EventStatus = EventStatus.VERIFIED,
    summary: str | None = "總圖書館三樓天花板漏水，部分座位暫停使用。",
    location: str | None = "總圖書館",
    evidence: Sequence[Evidence] = (OFFICIAL,),
    risk: str | None = "high",
    confirm: str | None = "user",
    impacts: Sequence[dict[str, Any]] = (),
    causes: Sequence[dict[str, Any]] = (),
    measures: Sequence[dict[str, Any]] = (),
    actions: Sequence[dict[str, Any]] = (),
) -> Event:
    """建立一個事件與它的證據、風險、查證依據、小報告與改善措施（已 commit）。

    `confirm`：user＝使用者在後台查證為 verified（event.verify 稽核）；official＝S05 判定官方來源
    支持；None＝沒有任何依據（例如只有第一手來源）。
    """
    loc = None
    if location is not None:
        loc = Location(name=location)
        session.add(loc)
        await session.flush()
    event = Event(
        title=title,
        type=event_type,
        status=status.value,
        summary=summary,
        location_id=loc.id if loc else None,
        occurrence_start_date=date(2026, 10, 27),
    )
    session.add(event)
    await session.flush()
    items: list[SourceItem] = []
    for ev in evidence:
        src = await _source(session, ev.source_code)
        item = SourceItem(
            source_id=src.id,
            external_id=uuid.uuid4().hex,
            url=ev.url or f"https://example.test/{uuid.uuid4().hex}",
            published_at=PUBLISHED_AT,
        )
        session.add(item)
        await session.flush()
        session.add(
            SourceItemVersion(
                item_id=item.id, content_hash=uuid.uuid4().hex, normalized_text=ev.text
            )
        )
        session.add(
            EventEvidence(
                event_id=event.id, source_item_id=item.id, support_type="supports", excerpt=ev.text
            )
        )
        items.append(item)
    if risk is not None:
        session.add(
            RiskAssessment(
                event_id=event.id,
                dimensions={},
                score=Decimal("70"),
                level=risk,
                policy_version="test",
            )
        )
    if confirm == "user":
        await record_audit(
            session,
            ADMIN,
            "event.verify",
            target=f"event:{event.id}",
            metadata={"from": "unverified", "to": "verified"},
        )
    elif confirm == "official":
        session.add(
            AIAnalysisRun(
                event_id=event.id,
                skill_name="evidence-verifier",
                skill_version="1.0.0",
                schema_version="1",
                provider="fake",
                model_id="fake",
                status="succeeded",
                result={
                    "assessments": [
                        {
                            "evidence_id": items[0].id.hex,
                            "claim_type": "official",
                            "stance": "supports",
                        }
                    ]
                },
                attempts=1,
                trace_id="t",
            )
        )
    report = None
    if impacts or causes or measures:
        report = EventReport(
            event_id=event.id,
            trigger="auto",
            verification_status="verified",
            risk_level=risk,
            impacts=list(impacts),
            causes_status="ok",
            causes=list(causes),
            measures_status="ok",
            measures=list(measures),
            trace_id="t",
        )
        session.add(report)
        await session.flush()
    for i, a in enumerate(actions):
        fields = dict(a)
        if "measure" in fields:
            assert report is not None
            fields.update(origin="measure", report_id=report.id, measure_index=i)
        else:
            fields.setdefault("origin", "manual")
        fields.setdefault("state", "handling")
        session.add(ResponseAction(event_id=event.id, **fields))
    await session.commit()
    return event


async def add_series(
    session: AsyncSession, events: Sequence[Event], *, title: str = "總圖書館漏水"
) -> EventSeries:
    """把事件放進同一個系列（P8-T04 的資料表；已 commit）。"""
    series = EventSeries(category=events[0].type, title=title)
    session.add(series)
    await session.flush()
    for e in events:
        session.add(
            EventSeriesMember(
                event_id=e.id, series_id=series.id, method="rule", joined_at=PUBLISHED_AT
            )
        )
    await session.commit()
    return series


async def publish(session: AsyncSession, event: Event) -> SyncAction:
    action = await sync_publication(session, event.id)
    await session.commit()
    return action


async def publication_of(session: AsyncSession, event: Event) -> Publication:
    pub = await session.scalar(select(Publication).where(Publication.event_id == event.id))
    assert pub is not None
    await session.refresh(pub)
    return pub


async def versions_of(session: AsyncSession, pub: Publication) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(PublicationVersion)
            .where(PublicationVersion.publication_id == pub.id)
        )
        or 0
    )


@pytest.fixture()
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


# ---------- 同步 ----------


async def test_user_verified_event_publishes_content(session: AsyncSession) -> None:
    event = await add_event(session, confirm="user")
    assert await publish(session, event) is SyncAction.PUBLISHED
    pub = await publication_of(session, event)
    assert (pub.level, pub.state, pub.version) == ("content", "published", 1)
    version = await session.scalar(
        select(PublicationVersion).where(PublicationVersion.publication_id == pub.id)
    )
    assert version is not None
    assert version.content is not None
    assert version.content["title"] == "總圖書館三樓漏水"
    assert version.content_hash is not None and len(version.content_hash) == 64


async def test_official_source_publishes_content(session: AsyncSession) -> None:
    event = await add_event(session, confirm="official")
    await publish(session, event)
    assert (await publication_of(session, event)).level == "content"


async def current_content(session: AsyncSession, event: Event) -> dict[str, Any]:
    pub = await publication_of(session, event)
    version = await session.scalar(
        select(PublicationVersion).where(
            PublicationVersion.publication_id == pub.id, PublicationVersion.version == pub.version
        )
    )
    assert version is not None and version.content is not None
    return version.content


async def test_verified_without_official_or_user_is_labelled_unverified(
    session: AsyncSession,
) -> None:
    event = await add_event(session, confirm=None)
    await publish(session, event)
    pub = await publication_of(session, event)
    assert pub.level == "content"
    assert "not_confirmed" in pub.reasons
    assert (await current_content(session, event))["verification_label"] == "尚未查證"


async def test_unverified_low_risk_is_published_labelled(session: AsyncSession) -> None:
    event = await add_event(session, status=EventStatus.UNVERIFIED, confirm=None, risk="medium")
    assert await publish(session, event) is SyncAction.PUBLISHED
    content = await current_content(session, event)
    assert (content["verification"], content["verification_label"]) == ("unverified", "尚未查證")


async def test_unchanged_content_keeps_version(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    assert await publish(session, event) is SyncAction.NONE
    pub = await publication_of(session, event)
    assert pub.version == 1
    assert await versions_of(session, pub) == 1


async def test_changed_content_adds_version(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    event.summary = "總圖書館三樓漏水已修復，座位恢復開放。"
    await session.commit()
    assert await publish(session, event) is SyncAction.UPDATED
    pub = await publication_of(session, event)
    assert pub.version == 2
    assert await versions_of(session, pub) == 2


async def test_event_judged_false_is_withdrawn(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    event.status = EventStatus.FALSE.value
    await session.commit()
    assert await publish(session, event) is SyncAction.WITHDRAWN
    pub = await publication_of(session, event)
    assert (pub.level, pub.state, pub.version) == ("none", "withdrawn", 2)


async def test_archived_event_is_withdrawn(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    event.status = EventStatus.ARCHIVED.value
    await session.commit()
    assert await publish(session, event) is SyncAction.WITHDRAWN


async def test_withdrawn_event_can_be_published_again(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    event.status = EventStatus.FALSE.value
    await session.commit()
    assert await publish(session, event) is SyncAction.WITHDRAWN
    event.status = EventStatus.VERIFIED.value
    await record_audit(
        session,
        ADMIN,
        "event.verify",
        target=f"event:{event.id}",
        metadata={"from": "false", "to": "verified"},
    )
    await session.commit()
    assert await publish(session, event) is SyncAction.PUBLISHED
    pub = await publication_of(session, event)
    assert (pub.level, pub.state, pub.version) == ("content", "published", 3)


async def test_monitoring_after_user_verification_keeps_content(session: AsyncSession) -> None:
    event = await add_event(session)
    event.status = EventStatus.MONITORING.value
    await record_audit(
        session,
        ADMIN,
        "event.verify",
        target=f"event:{event.id}",
        metadata={"from": "verified", "to": "monitoring"},
    )
    await session.commit()
    await publish(session, event)
    assert (await publication_of(session, event)).level == "content"


async def test_monitoring_after_disputed_is_labelled_disputed(session: AsyncSession) -> None:
    event = await add_event(session, status=EventStatus.MONITORING)
    await record_audit(
        session,
        ADMIN,
        "event.verify",
        target=f"event:{event.id}",
        metadata={"from": "disputed", "to": "monitoring"},
    )
    await session.commit()
    await publish(session, event)
    content = await current_content(session, event)
    assert (content["verification"], content["verification_label"]) == ("disputed", "尚未查證")


async def test_later_user_dispute_overrides_earlier_verification(session: AsyncSession) -> None:
    event = await add_event(session)
    event.status = EventStatus.DISPUTED.value
    await record_audit(
        session,
        ADMIN,
        "event.verify",
        target=f"event:{event.id}",
        metadata={"from": "verified", "to": "disputed"},
    )
    await session.commit()
    await publish(session, event)
    content = await current_content(session, event)
    assert (content["verification"], content["verification_label"]) == ("disputed", "尚未查證")


async def test_taken_down_publication_is_not_overwritten(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    pub = await publication_of(session, event)
    pub.state = "taken_down"
    pub.taken_down_at = datetime.now(UTC)
    event.summary = "新的摘要"
    await session.commit()
    assert await publish(session, event) is SyncAction.KEPT_DOWN
    pub = await publication_of(session, event)
    assert (pub.state, pub.version) == ("taken_down", 1)


async def test_invalidated_report_is_not_used(session: AsyncSession) -> None:
    event = await add_event(
        session,
        impacts=[{"dimension": "safety", "score": 2, "uncertainty": "low", "reason": "地滑"}],
    )
    report = await session.scalar(select(EventReport).where(EventReport.event_id == event.id))
    assert report is not None
    report.invalidated_at = datetime.now(UTC)
    report.invalidated_reason = "event_false"
    await session.commit()
    await publish(session, event)
    pub = await publication_of(session, event)
    version = await session.scalar(
        select(PublicationVersion).where(PublicationVersion.publication_id == pub.id)
    )
    assert version is not None and version.content is not None
    assert version.content["report"] is None


async def test_sync_writes_audit_with_codes_only(session: AsyncSession) -> None:
    event = await add_event(session)
    await publish(session, event)
    log = await session.scalar(select(AuditLog).where(AuditLog.action == "publication.publish"))
    assert log is not None
    assert log.actor == "service:notifier"
    assert log.target is not None and log.target.startswith("publication:")
    assert "總圖書館" not in str(log.meta)


async def test_sync_all_covers_every_event(session: AsyncSession) -> None:
    await add_event(session, title="總圖書館三樓漏水")
    await add_event(session, title="工程五館停電", confirm="official")
    await add_event(
        session, title="宵夜街機車失竊", event_type="security", risk="low", confirm=None
    )
    await add_event(session, title="工程四館火災", event_type="fire", status=EventStatus.FALSE)
    counts = await sync_all(session)
    assert counts[SyncAction.PUBLISHED] == 3  # P8-T06：未查證的低風險事件也公開
    assert counts[SyncAction.NONE] == 1


# ---------- 公開 API ----------


async def seed_public(session: AsyncSession) -> dict[str, Event]:
    """一個已查證（有舊小報告與改善措施）、一個未查證（P8-T06 前是安全提醒）、一個撤下的事件。"""
    content = await add_event(
        session,
        impacts=[{"dimension": "safety", "score": 2, "uncertainty": "low", "reason": "地面濕滑"}],
        measures=[measure("暫停開放三樓座位")],
        actions=[{"title": "更換三樓天花板防水層", "owner": "總務處", "measure": measure("x")}],
    )
    notice = await add_event(
        session,
        title="宿舍區機車失竊",
        event_type="security",
        status=EventStatus.UNVERIFIED,
        location="宿舍區",
        confirm=None,
        evidence=(Evidence("facebook", "我的機車在宿舍區被偷了"),),
    )
    gone = await add_event(session, title="工程五館停電")
    for e in (content, notice, gone):
        await publish(session, e)
    gone.status = EventStatus.FALSE.value
    await session.commit()
    await publish(session, gone)
    return {"content": content, "notice": notice, "gone": gone}


async def public_id(session: AsyncSession, event: Event) -> str:
    return str((await publication_of(session, event)).id)


async def test_feed_lists_published_items_newest_first(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    res = await client.get(f"{PUBLIC}/feed")
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    assert body["meta"]["request_id"]
    items = body["data"]
    assert [i["kind"] for i in items] == ["content", "content"]
    assert items[0]["id"] == await public_id(session, events["notice"])
    assert items[0]["title"] == "宿舍區機車失竊"
    assert items[0]["verification_label"] == "尚未查證"
    assert items[1]["title"] == "總圖書館三樓漏水"
    assert items[1]["verification_label"] == "已查證"
    for item in items:
        assert {"id", "kind", "event_type", "label", "version", "published_at"} <= set(item)


async def test_feed_cursor_pagination(client: httpx2.AsyncClient, session: AsyncSession) -> None:
    await seed_public(session)
    first = (await client.get(f"{PUBLIC}/feed", params={"limit": 1})).json()
    assert len(first["data"]) == 1
    assert first["meta"]["next_cursor"]
    second = (
        await client.get(
            f"{PUBLIC}/feed", params={"limit": 1, "cursor": first["meta"]["next_cursor"]}
        )
    ).json()
    assert len(second["data"]) == 1
    assert second["data"][0]["id"] != first["data"][0]["id"]
    assert second["meta"]["next_cursor"] is None


async def test_bad_cursor_and_limit_are_rejected(client: httpx2.AsyncClient) -> None:
    assert (await client.get(f"{PUBLIC}/feed", params={"cursor": "!!"})).status_code == 422
    assert (await client.get(f"{PUBLIC}/feed", params={"limit": 51})).status_code == 422


async def test_feed_filters_by_type(client: httpx2.AsyncClient, session: AsyncSession) -> None:
    await seed_public(session)
    items = (await client.get(f"{PUBLIC}/feed", params={"type": "security"})).json()["data"]
    assert [i["event_type"] for i in items] == ["security"]


async def test_unknown_type_is_rejected(client: httpx2.AsyncClient) -> None:
    res = await client.get(f"{PUBLIC}/events", params={"type": "drop table"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_notices_are_no_longer_produced(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    """P8-T06：未查證事件公開 AI 摘要，不再改寫成安全提醒。"""
    await seed_public(session)
    assert (await client.get(f"{PUBLIC}/notices")).json()["data"] == []


async def test_events_only_lists_content(client: httpx2.AsyncClient, session: AsyncSession) -> None:
    await seed_public(session)
    items = (await client.get(f"{PUBLIC}/events")).json()["data"]
    assert [i["title"] for i in items] == ["宿舍區機車失竊", "總圖書館三樓漏水"]
    assert "report" not in items[0]  # 列表不含小報告
    filtered = (await client.get(f"{PUBLIC}/events", params={"type": "security"})).json()
    assert [i["title"] for i in filtered["data"]] == ["宿舍區機車失竊"]


async def test_categories_count_published_items(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    await seed_public(session)
    data = (await client.get(f"{PUBLIC}/categories")).json()["data"]
    by_type = {c["event_type"]: c for c in data}
    assert "none" not in by_type
    assert by_type["facility"]["label"]
    assert (by_type["facility"]["content"], by_type["facility"]["safety_notice"]) == (1, 0)
    assert (by_type["security"]["content"], by_type["security"]["safety_notice"]) == (1, 0)
    assert by_type["fire"]["content"] == 0


async def test_event_detail_returns_public_report(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    res = await client.get(f"{PUBLIC}/events/{await public_id(session, events['content'])}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["kind"] == "content"
    assert data["report"] is None  # 單一事件小報告停用（P8-T13，ADR-0020 決策 3-5）
    assert data["sources"] == [{"name": "中央大學最新消息", "url": data["sources"][0]["url"]}]
    # 改善進度改在系列分析頁（P8-T06）；不在任何公開系列中時 series 為 null
    assert "progress" not in data
    assert data["series"] is None
    assert "更換三樓天花板防水層" not in res.text


async def test_unverified_event_detail_is_public_with_label(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    res = await client.get(f"{PUBLIC}/events/{await public_id(session, events['notice'])}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["verification_label"] == "尚未查證"
    assert data["sources"] == []  # Facebook 來源不列出
    assert "我的機車在宿舍區被偷了" not in res.text


@pytest.mark.parametrize("which", ["gone"])
async def test_event_detail_hides_notices_and_withdrawn(
    client: httpx2.AsyncClient, session: AsyncSession, which: str
) -> None:
    events = await seed_public(session)
    res = await client.get(f"{PUBLIC}/events/{await public_id(session, events[which])}")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


async def test_event_detail_by_internal_event_id_is_not_found(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    res = await client.get(f"{PUBLIC}/events/{events['content'].id}")
    assert res.status_code == 404


async def test_taken_down_disappears_immediately(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    pub = await publication_of(session, events["content"])
    pub.state = "taken_down"
    pub.taken_down_at = datetime.now(UTC)
    await session.commit()
    assert (await client.get(f"{PUBLIC}/events/{pub.id}")).status_code == 404
    feed = (await client.get(f"{PUBLIC}/feed")).json()["data"]
    assert str(pub.id) not in {i["id"] for i in feed}


async def test_progress_lists_series_with_actions(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    """P8-T06：改善進度以系列為單位（P8-T13 已把事件層級的措施掛到所屬系列）。"""
    events = await seed_public(session)
    series = await add_series(session, [events["content"]])
    action = await session.scalar(
        select(ResponseAction).where(ResponseAction.event_id == events["content"].id)
    )
    assert action is not None
    action.series_id = series.id
    await session.commit()
    await sync_all(session)
    series_pub = await session.scalar(select(Publication).where(Publication.series_id == series.id))
    assert series_pub is not None
    data = (await client.get(f"{PUBLIC}/progress")).json()["data"]
    assert [d["id"] for d in data] == [str(series_pub.id)]
    assert data[0]["title"] == "總圖書館漏水"
    assert data[0]["progress"][0]["state"] == "handling"
    assert data[0]["counts"] == {"handling": 1}
    assert "owner" not in data[0]["progress"][0]


async def test_responses_carry_cache_headers_and_etag(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    await seed_public(session)
    res = await client.get(f"{PUBLIC}/feed")
    assert res.headers["cache-control"].startswith("public, max-age=")
    etag = res.headers["etag"]
    again = await client.get(f"{PUBLIC}/feed", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == etag


async def test_etag_changes_when_content_changes(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    events = await seed_public(session)
    etag = (await client.get(f"{PUBLIC}/feed")).headers["etag"]
    events["content"].summary = "已修復"
    await session.commit()
    await publish(session, events["content"])
    res = await client.get(f"{PUBLIC}/feed", headers={"If-None-Match": etag})
    assert res.status_code == 200
    assert res.headers["etag"] != etag


async def test_errors_are_not_cached(client: httpx2.AsyncClient) -> None:
    res = await client.get(f"{PUBLIC}/events/{uuid.uuid4()}")
    assert res.status_code == 404
    assert res.headers["cache-control"] == "no-store"


async def test_rate_limit_returns_429_with_retry_after(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    transport = client._transport
    assert isinstance(transport, httpx2.ASGITransport)
    transport.app.state.public_rate_limiter = RateLimiter(limit=3, window_seconds=60)  # type: ignore[attr-defined]
    for _ in range(3):
        assert (await client.get(f"{PUBLIC}/feed")).status_code == 200
    res = await client.get(f"{PUBLIC}/feed")
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"
    assert int(res.headers["retry-after"]) >= 1
    assert res.headers["cache-control"] == "no-store"


async def test_public_routes_only_accept_get(client: httpx2.AsyncClient) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        res = await client.request(method, f"{PUBLIC}/feed", json={})
        assert res.status_code == 405, method


async def test_admin_session_is_not_needed_and_not_used(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    await seed_public(session)
    client.cookies.set("ncu_session", "forged", domain="test")
    res = await client.get(f"{PUBLIC}/feed")
    assert res.status_code == 200
    assert "set-cookie" not in res.headers


def test_rate_limiter_does_not_keep_client_addresses() -> None:
    limiter = RateLimiter(limit=2, window_seconds=60)
    limiter.hit("203.0.113.7")
    assert "203.0.113.7" not in repr(limiter.__dict__)


def test_rate_limiter_resets_after_window() -> None:
    now = [1000.0]
    limiter = RateLimiter(limit=1, window_seconds=60, clock=lambda: now[0])
    assert limiter.hit("a") is None
    assert limiter.hit("a") is not None
    assert limiter.hit("b") is None
    now[0] += 61
    assert limiter.hit("a") is None
