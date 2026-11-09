"""P8-T04：S11 series-matcher 與系列自動歸組（ADR-0018 決策 1-1～1-4、ADR-0020 決策 1）。

- 每個事件都屬於一個系列：同類別的既有系列由 S11 判斷同主題就加入，否則建立只有它一件的系列；
  沒有候選時不呼叫 S11；abstain、失敗或 target_id 不在候選中一律建立新系列；候選不含地點；
- 拆出的事件自成 1 件的系列，之後不再自動歸回；解散時每個事件各自成為 1 件的系列，原系列刪除；
- 子事件改判 false 移出、自成 1 件的系列；false 的事件不當候選、新事件 false 時不呼叫 S11；
- 被合併的事件離開系列（合併後只算 1 件），件數 0 的系列刪除；
- 稽核 `series.assign`（呼叫 S11 時）、`series.split`、`series.dissolve`、`series.remove`；
- 同一組輸入重跑結果相同；補判可重跑；管線在 S05 後歸組；後台 API 列出、拆出、解散。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.config import load_llm_config
from app.ai.runner.run import SkillRunResult, SqlRunStore, run_skill
from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.events.merge import merge_events
from app.main import create_app
from app.models import AuditLog
from app.models.ai import AIAnalysisRun
from app.models.event import EVENT_EMBEDDING_DIM, Event, EventEvidence
from app.models.series import EventSeries, EventSeriesMember, EventSeriesRemoval
from app.pipeline.analyze_item import Outcome, analyze_item
from app.series.grouping import (
    MAX_CANDIDATES,
    SeriesConflictError,
    SeriesNotFoundError,
    assign_series,
    backfill_series,
    dissolve_series,
    series_of,
    split_from_series,
    sync_event_series,
)
from tests.integration.test_pipeline import (
    SKILLS,
    SkillScript,
    add_item,
    add_source,
    classification,
    deps,
    eid,
    extraction,
    risk,
    verification,
)

pytestmark = pytest.mark.anyio

ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
SERVICE = Principal(kind="service", subject="pipeline", roles=frozenset({Role.SERVICE_AI}))


def vector(x: float) -> list[float]:
    """單位向量：x 越接近 1，與 vector(1) 的距離越近。"""
    return [x, (1 - x * x) ** 0.5] + [0.0] * (EVENT_EMBEDDING_DIM - 2)


def answer(target: str | None = None, *, same: bool | None = True) -> dict[str, Any]:
    """S11 的回應；evidence_id 由 Matcher 依輸入填入。"""
    ok = same is not None
    return {
        "status": "ok" if ok else "abstain",
        "same_topic": same,
        "target_id": target if same else None,
        "confidence": 0.9 if ok else 0.0,
        "evidence_ids": ["?"] if ok else [],
        "evidence_spans": [{"evidence_id": "?", "quote": "大家注意"}] if ok else [],
        "reason": "依原文判斷。",
        "abstain_reason": None if ok else "insufficient_evidence",
    }


class Matcher:
    """以 fake provider 跑真的 Skill Runner（含 schema 與業務驗證），記下每次的輸入。"""

    def __init__(self, session: AsyncSession, *answers: dict[str, Any]) -> None:
        self.session = session
        self.answers = list(answers)
        self.payloads: list[Mapping[str, Any]] = []

    async def __call__(self, payload: Mapping[str, Any]) -> SkillRunResult:
        self.payloads.append(payload)
        evidence_id = str(payload["evidence"][0]["evidence_id"])
        scripted = self.answers.pop(0)
        filled = {
            **scripted,
            "evidence_ids": [evidence_id for _ in scripted["evidence_ids"]],
            "evidence_spans": [
                {**s, "evidence_id": evidence_id} for s in scripted["evidence_spans"]
            ],
        }
        return await run_skill(
            SKILLS["series-matcher"],
            payload,
            provider=SkillScript({"series-matcher": [filled]}),
            config=load_llm_config(),
            store=SqlRunStore(self.session),
            trace_id="trace-series",
        )

    @property
    def calls(self) -> int:
        return len(self.payloads)

    def titles(self, call: int = 0) -> list[str]:
        return [c["title"] for c in self.payloads[call]["candidates"]]

    def code(self, title: str, call: int = 0) -> str:
        return next(
            c["series_id"] for c in self.payloads[call]["candidates"] if c["title"] == title
        )


async def add_event(
    session: AsyncSession,
    title: str,
    *,
    category: str = "facility",
    status: str = "unverified",
    x: float = 1.0,
) -> Event:
    """事件與一篇來源文章（內文含「大家注意」，供 S11 逐字引用）。"""
    source = await add_source(session, f"src-{uuid.uuid4().hex[:8]}")
    item = await add_item(session, source, f"{title}，大家注意。")
    event = Event(
        title=title, type=category, status=status, summary=f"{title}。", embedding=vector(x)
    )
    session.add(event)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    await session.flush()
    return event


async def assign(session: AsyncSession, event: Event, run: Matcher | None) -> uuid.UUID:
    series_id = await assign_series(session, event, run=run, principal=SERVICE)
    await session.commit()
    assert series_id is not None
    return series_id


async def members(session: AsyncSession, series_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(EventSeriesMember.event_id).where(EventSeriesMember.series_id == series_id)
    )
    return set(rows)


async def member(session: AsyncSession, event: Event) -> EventSeriesMember:
    row = await session.get(EventSeriesMember, event.id, populate_existing=True)
    assert row is not None
    return row


async def series_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(EventSeries)) or 0)


async def audits(session: AsyncSession, action: str) -> list[AuditLog]:
    return list(await session.scalars(select(AuditLog).where(AuditLog.action == action)))


async def grouped(session: AsyncSession, a: Event, b: Event) -> uuid.UUID:
    """a 自成系列，b 經 S11 判斷同主題加入；回傳系列 id。"""
    first = await assign(session, a, None)
    run = Matcher(session, answer("S1"))
    assert await assign(session, b, run) == first
    return first


# ---------- 自動歸組 ----------


async def test_first_event_gets_its_own_series_without_calling_s11(session: AsyncSession) -> None:
    event = await add_event(session, "科學一館停電")
    run = Matcher(session)

    series_id = await assign(session, event, run)

    assert run.calls == 0
    series = await session.get(EventSeries, series_id)
    assert series is not None
    assert (series.title, series.category) == ("科學一館停電", "facility")
    row = await member(session, event)
    assert (row.series_id, row.method, row.ai_run_id) == (series_id, "fallback", None)
    assert await audits(session, "series.assign") == []  # 沒呼叫 S11 時只留成員紀錄


async def test_same_topic_joins_existing_series(session: AsyncSession) -> None:
    first = await add_event(session, "科學一館停電")
    series_id = await assign(session, first, None)
    second = await add_event(session, "男九舍跳電")
    run = Matcher(session, answer("S1"))

    assert await assign(session, second, run) == series_id

    assert run.calls == 1
    assert await members(session, series_id) == {first.id, second.id}
    row = await member(session, second)
    assert row.method == "ai"
    ai_run = await session.get(AIAnalysisRun, row.ai_run_id)
    assert ai_run is not None
    assert ai_run.skill_name == "series-matcher"
    [audit] = await audits(session, "series.assign")
    assert audit.target == f"event:{second.id}"
    assert audit.meta["series_id"] == str(series_id)
    assert audit.meta["same_topic"] is True


async def test_different_topic_creates_new_series(session: AsyncSession) -> None:
    first = await add_event(session, "科學一館停電")
    series_id = await assign(session, first, None)
    leak = await add_event(session, "總圖三樓漏水")
    run = Matcher(session, answer(same=False))

    new = await assign(session, leak, run)

    assert new != series_id
    assert await members(session, new) == {leak.id}
    assert (await member(session, leak)).method == "ai"
    assert await series_count(session) == 2


@pytest.mark.parametrize(
    "scripted",
    [answer(same=None), answer("S9")],  # abstain；target_id 不在候選中
    ids=["abstain", "unknown-target"],
)
async def test_abstain_or_unknown_target_creates_new_series(
    session: AsyncSession, scripted: dict[str, Any]
) -> None:
    first = await add_event(session, "科學一館停電")
    series_id = await assign(session, first, None)
    second = await add_event(session, "工五停電")
    run = Matcher(session, scripted)

    new = await assign(session, second, run)

    assert run.calls == 1
    assert new != series_id
    assert (await member(session, second)).method == "fallback"


async def test_only_same_category_series_are_candidates(session: AsyncSession) -> None:
    theft = await add_event(session, "宿舍機車失竊", category="security")
    await assign(session, theft, None)
    power = await add_event(session, "科學一館停電")
    run = Matcher(session)

    await assign(session, power, run)

    assert run.calls == 0  # 沒有同類別的系列


async def test_candidates_have_no_location_and_are_ranked_by_vector(
    session: AsyncSession,
) -> None:
    for i in range(MAX_CANDIDATES + 2):  # 12 個 1 件的系列，距離由近到遠：x = 0.99 … 0.88
        await assign(session, await add_event(session, f"停電 {i:02d}", x=0.99 - i * 0.01), None)
    new = await add_event(session, "工五停電", x=1.0)
    run = Matcher(session, answer(same=False))

    await assign(session, new, run)

    payload = run.payloads[0]
    assert run.titles() == [f"停電 {i:02d}" for i in range(MAX_CANDIDATES)]
    assert [c["series_id"] for c in payload["candidates"]] == [
        f"S{i}" for i in range(1, MAX_CANDIDATES + 1)
    ]
    assert "location" not in payload["event"]
    assert all("location" not in e for c in payload["candidates"] for e in c["events"])
    assert payload["event"]["category"] == "facility"
    assert payload["candidates"][0]["event_count"] == 1


async def test_already_grouped_event_is_not_judged_again(session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)
    run = Matcher(session)

    assert await assign(session, b, run) == series_id
    assert run.calls == 0


async def test_rerun_with_same_input_gives_same_grouping(session: AsyncSession) -> None:
    created = []
    for title, x in (("科學一館停電", 1.0), ("工五停電", 1.0), ("總圖漏水", 0.5)):
        created.append(await add_event(session, title, x=x))
        await session.commit()  # 建立時間依序（同一個交易內的 now() 相同）
    *power, leak = created

    async def run_once() -> tuple[list[set[uuid.UUID]], list[Mapping[str, Any]]]:
        run = Matcher(session, answer("S1"), answer(same=False))
        assert await backfill_series(session, run=run, principal=SERVICE) == 3
        await session.commit()
        groups = [await members(session, s) for s in await session.scalars(select(EventSeries.id))]
        return sorted(groups, key=len), run.payloads

    first_groups, first_payloads = await run_once()
    await session.execute(delete(EventSeries))
    await session.commit()
    second_groups, second_payloads = await run_once()

    assert first_groups == second_groups == [{leak.id}, {power[0].id, power[1].id}]
    assert first_payloads == second_payloads


async def test_backfill_groups_in_creation_order_and_can_rerun(session: AsyncSession) -> None:
    a = await add_event(session, "科學一館停電")
    b = await add_event(session, "工五停電")
    await session.commit()
    run = Matcher(session, answer("S1"))

    assert await backfill_series(session, run=run, principal=SERVICE) == 2
    await session.commit()

    assert run.calls == 1  # 第一個事件沒有候選
    assert await series_of(session, a.id) == await series_of(session, b.id)
    again = Matcher(session)
    assert await backfill_series(session, run=again, principal=SERVICE) == 0
    assert again.calls == 0


# ---------- 拆出與解散 ----------


async def test_split_event_gets_own_series_and_is_not_regrouped(session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)

    new = await split_from_series(session, ADMIN, series_id, b.id)
    await session.commit()

    assert new != series_id
    assert await members(session, series_id) == {a.id}
    assert await members(session, new) == {b.id}
    assert (await member(session, b)).method == "manual"
    [removal] = list(await session.scalars(select(EventSeriesRemoval)))
    assert (removal.event_id, removal.series_id, removal.reason) == (b.id, series_id, "split")
    assert removal.decided_by == ADMIN.actor
    [audit] = await audits(session, "series.split")
    assert audit.meta == {"from": str(series_id), "to": str(new)}

    # 之後重跑歸組、補判都不會把它歸回原系列
    run = Matcher(session)
    assert await sync_event_series(session, b, run=run, principal=SERVICE) == new
    assert await backfill_series(session, run=run, principal=SERVICE) == 0
    assert run.calls == 0
    assert await series_of(session, b.id) == new


async def test_split_out_series_is_not_offered_back_to_the_event(session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)
    await split_from_series(session, ADMIN, series_id, b.id)
    # 事件失去系列（例：資料修復）後重新歸組，拆出過的系列不再是候選
    await session.execute(delete(EventSeriesMember).where(EventSeriesMember.event_id == b.id))
    await session.commit()
    run = Matcher(session)

    await assign(session, b, run)

    assert run.calls == 0  # 唯一的同類別系列是拆出過的那一個
    assert await series_of(session, b.id) != series_id


async def test_split_errors(session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)
    alone = await add_event(session, "總圖漏水")
    alone_series = await assign(session, alone, Matcher(session, answer(same=False)))

    with pytest.raises(SeriesConflictError):  # 只有一件，不需要拆出
        await split_from_series(session, ADMIN, alone_series, alone.id)
    with pytest.raises(SeriesNotFoundError):  # 事件不在這個系列
        await split_from_series(session, ADMIN, series_id, alone.id)
    with pytest.raises(SeriesNotFoundError):
        await split_from_series(session, ADMIN, uuid.uuid4(), a.id)


async def test_dissolve_gives_each_event_its_own_series(session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)
    c = await add_event(session, "男九舍跳電")
    await assign(session, c, Matcher(session, answer("S1")))

    new = await dissolve_series(session, ADMIN, series_id)
    await session.commit()

    assert await session.get(EventSeries, series_id) is None  # 件數 0 的系列刪除
    assert set(new) == {a.id, b.id, c.id}
    assert len(set(new.values())) == 3
    for event in (a, b, c):
        assert await members(session, new[event.id]) == {event.id}
    reasons = list(await session.scalars(select(EventSeriesRemoval.reason)))
    assert reasons == ["dissolve"] * 3
    [audit] = await audits(session, "series.dissolve")
    assert audit.meta == {"events": 3}
    with pytest.raises(SeriesConflictError):  # 1 件的系列不需要解散
        await dissolve_series(session, ADMIN, new[a.id])


# ---------- 改判 false 與合併 ----------


async def test_false_event_moves_out_into_own_series(session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)
    b.status = "false"

    new = await sync_event_series(session, b, run=None, principal=ADMIN)
    await session.commit()

    assert new is not None and new != series_id
    assert await members(session, series_id) == {a.id}
    assert await members(session, new) == {b.id}
    assert (await member(session, b)).method == "rule"
    [removal] = list(await session.scalars(select(EventSeriesRemoval)))
    assert removal.reason == "false"
    [audit] = await audits(session, "series.remove")
    assert audit.meta["reason"] == "false"
    # 已經自成一件時不再移動
    assert await sync_event_series(session, b, run=None, principal=ADMIN) == new


async def test_false_events_are_not_candidates_and_not_judged(session: AsyncSession) -> None:
    rumor = await add_event(session, "工五停電謠言", status="false")
    await assign(session, rumor, None)
    power = await add_event(session, "科學一館停電")
    run = Matcher(session)
    await assign(session, power, run)
    assert run.calls == 0  # 只有 false 事件的系列不是候選

    other = await add_event(session, "男九舍跳電", status="false")
    await assign(session, other, run)
    assert run.calls == 0  # false 的新事件直接自成系列


async def test_verify_false_endpoint_moves_event_out(api: Api, session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)

    response = await api.client.post(
        f"/api/v1/admin/events/{b.id}/verify", json={"status": "false"}
    )

    assert response.status_code == 200
    assert await members(session, series_id) == {a.id}
    assert await series_of(session, b.id) not in (None, series_id)


async def test_merged_event_counts_once(session: AsyncSession) -> None:
    a, b = await add_event(session, "總圖三樓漏水"), await add_event(session, "總圖又漏水")
    series_id = await grouped(session, a, b)

    await merge_events(session, ADMIN, b.id, a.id, request_id="req-merge")
    await session.commit()

    assert await members(session, series_id) == {a.id}  # 合併後只算 1 件
    assert await series_of(session, b.id) is None
    [removal] = list(await session.scalars(select(EventSeriesRemoval)))
    assert (removal.event_id, removal.reason) == (b.id, "merged")


async def test_merging_a_lone_event_deletes_its_series(session: AsyncSession) -> None:
    a = await add_event(session, "總圖三樓漏水")
    await assign(session, a, None)
    b = await add_event(session, "總圖又漏水")
    b_series = await assign(session, b, Matcher(session, answer(same=False)))

    await merge_events(session, ADMIN, b.id, a.id, request_id="req-merge")
    await session.commit()

    assert await session.get(EventSeries, b_series) is None
    assert await series_count(session) == 1


# ---------- 管線 ----------


async def test_pipeline_groups_new_event_after_verification(session: AsyncSession) -> None:
    existing = await add_event(session, "工程五館停電")
    existing.embedding = None  # 不讓 S04 召回它；系列候選仍然有它
    series_id = await assign(session, existing, None)
    item = await add_item(session, await add_source(session, "facebook"), "科學一館停電了。")
    e = eid(item)
    run_answer = answer("S1")
    run_answer["evidence_ids"] = [e]
    run_answer["evidence_spans"] = [{"evidence_id": e, "quote": "科學一館停電"}]
    provider = SkillScript(
        {
            "event-extractor": [extraction(e)],
            "event-classifier": [classification(e)],
            "location-resolver": [],
            "evidence-verifier": [verification({e: ("supports", "first_hand")}, "unverified")],
            "series-matcher": [run_answer],
            "risk-assessor": [risk(e)],
        }
    )
    pipeline_deps = replace(deps(provider), check_relevance=False, group_series=True)

    result = await analyze_item(session, item.id, pipeline_deps)

    assert result.outcome is Outcome.CREATED
    assert result.event_id is not None
    assert await series_of(session, result.event_id) == series_id
    assert "series-matcher" in provider.called
    s11 = (
        await session.scalars(
            select(AIAnalysisRun).where(AIAnalysisRun.skill_name == "series-matcher")
        )
    ).one()
    assert s11.event_id == result.event_id


# ---------- API ----------


class Api:
    def __init__(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        self.app = app
        self.client = client

    def as_principal(self, principal: Principal) -> None:
        self.app.dependency_overrides[get_principal] = lambda: principal


@pytest.fixture()
async def api(session: AsyncSession) -> AsyncIterator[Api]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        api = Api(app, client)
        api.as_principal(ADMIN)
        yield api


async def test_list_and_get_series(api: Api, session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)
    theft = await add_event(session, "宿舍機車失竊", category="security")
    await assign(session, theft, None)

    body = (await api.client.get("/api/v1/admin/series")).json()
    assert body["error"] is None
    by_title = {s["title"]: s for s in body["data"]}
    assert by_title["科學一館停電"]["event_count"] == 2
    assert by_title["宿舍機車失竊"]["event_count"] == 1
    assert by_title["宿舍機車失竊"]["category"] == "security"

    filtered = (await api.client.get("/api/v1/admin/series", params={"event_id": str(b.id)})).json()
    assert [s["id"] for s in filtered["data"]] == [str(series_id)]
    by_category = (
        await api.client.get("/api/v1/admin/series", params={"category": "security"})
    ).json()
    assert [s["title"] for s in by_category["data"]] == ["宿舍機車失竊"]

    detail = (await api.client.get(f"/api/v1/admin/series/{series_id}")).json()["data"]
    assert detail["event_count"] == 2
    assert {e["id"] for e in detail["events"]} == {str(a.id), str(b.id)}
    assert {e["method"] for e in detail["events"]} == {"fallback", "ai"}
    missing = await api.client.get(f"/api/v1/admin/series/{uuid.uuid4()}")
    assert missing.json()["error"]["code"] == "NOT_FOUND"


async def test_split_and_dissolve_endpoints(api: Api, session: AsyncSession) -> None:
    a, b = await add_event(session, "科學一館停電"), await add_event(session, "工五停電")
    series_id = await grouped(session, a, b)
    c = await add_event(session, "男九舍跳電")
    await assign(session, c, Matcher(session, answer("S1")))

    split = await api.client.post(f"/api/v1/admin/series/{series_id}/events/{c.id}/split")
    assert split.status_code == 201
    data = split.json()["data"]
    assert (data["event_id"], data["from_series_id"]) == (str(c.id), str(series_id))
    assert data["series_id"] == str(await series_of(session, c.id))
    again = await api.client.post(f"/api/v1/admin/series/{series_id}/events/{c.id}/split")
    assert again.json()["error"]["code"] == "NOT_FOUND"

    dissolved = await api.client.post(f"/api/v1/admin/series/{series_id}/dissolve")
    assert dissolved.status_code == 200
    assert {r["event_id"] for r in dissolved.json()["data"]["events"]} == {str(a.id), str(b.id)}
    gone = await api.client.post(f"/api/v1/admin/series/{series_id}/dissolve")
    assert gone.json()["error"]["code"] == "NOT_FOUND"
    lone = await api.client.post(f"/api/v1/admin/series/{data['series_id']}/dissolve")
    assert lone.json()["error"]["code"] == "CONFLICT"


async def test_series_endpoints_require_permissions(api: Api, session: AsyncSession) -> None:
    a = await add_event(session, "科學一館停電")
    series_id = await assign(session, a, None)
    api.as_principal(
        Principal(kind="service", subject="notifier", roles=frozenset({Role.SERVICE_NOTIFIER}))
    )

    assert (await api.client.get("/api/v1/admin/series")).status_code == 403
    assert (await api.client.get(f"/api/v1/admin/series/{series_id}")).status_code == 403
    url = f"/api/v1/admin/series/{series_id}"
    assert (await api.client.post(f"{url}/events/{a.id}/split")).status_code == 403
    assert (await api.client.post(f"{url}/dissolve")).status_code == 403
