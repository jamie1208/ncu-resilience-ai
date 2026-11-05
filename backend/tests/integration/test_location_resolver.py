"""P8-T02：S13 location-resolver 與代表地點（ADR-0018 決策 1-5，`app/locations/resolver.py`）。

- 沒見過的名稱才呼叫 S13；同名稱直接沿用，不重複呼叫；「全校」「校外」與沒有候選時不呼叫；
- alias_of → `canonical_id` 指向代表地點（別名的別名攤平）；inside_of → `parent_id`；
- abstain、失敗或 target_id 不在候選中 → 當成新地點；
- 使用者拆開後不再自動合併；既有地點補判一次，可重跑且結果相同；
- 每次判斷寫入 location_resolutions；呼叫 S13 時稽核 `location.resolve`，拆開稽核 `location.split`；
- 管線：新事件的地點經 S13 判斷；後台 API 列出地點與拆開。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.config import load_llm_config
from app.ai.runner.run import SkillRunResult, SqlRunStore, run_skill
from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.locations.resolver import (
    LocationSplitError,
    backfill_locations,
    canonical_id_of,
    resolve_location,
    split_location,
)
from app.main import create_app
from app.models import AuditLog
from app.models.ai import AIAnalysisRun
from app.models.event import Event, EventEvidence, Location, LocationResolution
from app.pipeline.analyze_item import Outcome, analyze_item
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
TEXT = "工五二樓走廊的燈從昨晚開始一直閃。"


def article(text: str = TEXT, evidence_id: str = "e1") -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_code": "facebook",
        "title": None,
        "text": text,
        "published_at": "2026-11-01T01:00:00Z",
    }


def answer(
    decision: str | None,
    target: str | None = None,
    *,
    quote: str = "工五二樓走廊",
    evidence_id: str = "e1",
) -> dict[str, Any]:
    ok = decision is not None
    return {
        "status": "ok" if ok else "abstain",
        "decision": decision,
        "target_id": target,
        "confidence": 0.9 if ok else 0.0,
        "evidence_ids": [evidence_id] if ok else [],
        "evidence_spans": [{"evidence_id": evidence_id, "quote": quote}] if ok else [],
        "reason": "依原文判斷。",
        "abstain_reason": None if ok else "insufficient_evidence",
    }


class Resolver:
    """以 fake provider 跑真的 Skill Runner（含 schema 與業務驗證），記下每次的輸入。"""

    def __init__(self, session: AsyncSession, *answers: Any) -> None:
        self.session = session
        self.provider = SkillScript({"location-resolver": list(answers)})
        self.payloads: list[Mapping[str, Any]] = []

    async def __call__(self, payload: Mapping[str, Any]) -> SkillRunResult:
        self.payloads.append(payload)
        return await run_skill(
            SKILLS["location-resolver"],
            payload,
            provider=self.provider,
            config=load_llm_config(),
            store=SqlRunStore(self.session),
            trace_id="trace-loc",
        )

    @property
    def calls(self) -> int:
        return len(self.payloads)

    def candidate_names(self, call: int = 0) -> dict[str, str]:
        return {c["location_id"]: c["name"] for c in self.payloads[call]["candidates"]}


async def add_location(session: AsyncSession, name: str, **values: Any) -> Location:
    loc = Location(name=name, **values)
    session.add(loc)
    await session.flush()
    return loc


async def resolve(session: AsyncSession, name: str, run: Resolver, text: str = TEXT) -> Location:
    location_id = await resolve_location(
        session, name, evidence=[article(text)], run=run, principal=SERVICE
    )
    await session.commit()
    assert location_id is not None
    loc = await session.get(Location, location_id)
    assert loc is not None
    await session.refresh(loc)
    return loc


def target_of(run: Resolver, name: str, call: int = 0) -> str:
    return next(k for k, v in run.candidate_names(call).items() if v == name)


async def resolutions(session: AsyncSession, loc: Location) -> list[LocationResolution]:
    rows = await session.scalars(
        select(LocationResolution)
        .where(LocationResolution.location_id == loc.id)
        .order_by(LocationResolution.created_at, LocationResolution.id)
    )
    return list(rows)


# ---------- 判斷 ----------


async def test_alias_points_to_canonical_location(session: AsyncSession) -> None:
    canonical = await add_location(session, "工程五館")
    await add_location(session, "總圖書館")
    run = Resolver(session, answer("alias_of", "L1"))

    loc = await resolve(session, "工五", run)

    assert run.calls == 1
    assert run.candidate_names()["L1"] == "工程五館"  # 名稱最像的排在前面
    assert run.payloads[0]["new_location"] == {"name": "工五", "evidence_ids": ["e1"]}
    assert (loc.canonical_id, loc.parent_id) == (canonical.id, None)
    assert await canonical_id_of(session, loc.id) == canonical.id
    (record,) = await resolutions(session, loc)
    assert (record.decision, record.target_id, record.method) == ("alias_of", canonical.id, "ai")
    assert record.ai_run_id is not None
    audit = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "location.resolve"))
    ).one()
    assert audit.target == f"location:{loc.id}"
    assert audit.meta == {"decision": "alias_of", "target_id": str(canonical.id), "method": "ai"}


async def test_alias_of_an_alias_is_flattened(session: AsyncSession) -> None:
    canonical = await add_location(session, "工程五館")
    alias = await add_location(session, "工五", canonical_id=canonical.id)
    run = Resolver(session, answer("alias_of", "L1", quote="工五館"))

    loc = await resolve(session, "工五館", run, text="工五館外面的腳踏車架。")

    # 候選只列代表地點，別名放在 aliases
    (candidate,) = run.payloads[0]["candidates"]
    assert (candidate["name"], candidate["aliases"]) == ("工程五館", ["工五"])
    assert loc.canonical_id == canonical.id
    assert alias.id != loc.id


async def test_inside_of_sets_parent(session: AsyncSession) -> None:
    dorms = await add_location(session, "宿舍區")
    run = Resolver(session, answer("inside_of", "L1", quote="男九舍門口"))

    loc = await resolve(session, "男九舍", run, text="男九舍門口的車棚。")

    assert (loc.canonical_id, loc.parent_id) == (None, dorms.id)
    assert await canonical_id_of(session, loc.id) == loc.id  # 上層不併入計數


async def test_same_name_is_reused_without_calling(session: AsyncSession) -> None:
    await add_location(session, "工程五館")
    run = Resolver(session, answer("alias_of", "L1"))
    first = await resolve(session, "工五", run)

    second = await resolve(session, "工五", run)

    assert second.id == first.id
    assert run.calls == 1
    count = await session.scalar(select(func.count()).select_from(Location))
    assert count == 2


@pytest.mark.parametrize(
    "reply",
    [
        answer(None),  # abstain
        answer("alias_of", "L9"),  # 不在候選中
        answer("new"),
    ],
    ids=["abstain", "unknown-target", "new"],
)
async def test_abstain_or_invalid_target_becomes_new_location(
    session: AsyncSession, reply: dict[str, Any]
) -> None:
    await add_location(session, "工程五館")
    run = Resolver(session, reply)

    loc = await resolve(
        session, "游藝館", run, text="工五二樓走廊的燈從昨晚開始一直閃，游藝館也是。"
    )

    assert (loc.canonical_id, loc.parent_id) == (None, None)
    (record,) = await resolutions(session, loc)
    assert record.decision == "new"
    assert record.method == ("ai" if reply["decision"] == "new" else "fallback")


async def test_special_names_and_no_candidates_do_not_call(session: AsyncSession) -> None:
    run = Resolver(session)

    first = await resolve(session, "工程五館", run)  # 還沒有任何地點
    whole = await resolve(session, "全校", run)
    outside = await resolve(session, "校外", run)

    assert run.calls == 0
    assert {
        r.method for loc in (first, whole, outside) for r in await resolutions(session, loc)
    } == {"fallback"}
    # 特殊值不會成為其他地點的候選
    run2 = Resolver(session, answer("alias_of", "L1"))
    await resolve(session, "工五", run2)
    assert set(run2.candidate_names().values()) == {"工程五館"}


async def test_unknown_location_is_not_created(session: AsyncSession) -> None:
    run = Resolver(session)
    for name in (None, "", "不明"):
        assert (
            await resolve_location(session, name, evidence=[article()], run=run, principal=SERVICE)
            is None
        )
    assert run.calls == 0


# ---------- 拆開 ----------


async def test_split_alias_is_not_merged_again(session: AsyncSession) -> None:
    canonical = await add_location(session, "工程五館")
    run = Resolver(session, answer("alias_of", "L1"))
    loc = await resolve(session, "工五", run)

    await split_location(session, ADMIN, loc.id)
    await session.commit()

    await session.refresh(loc)
    assert loc.canonical_id is None
    records = await resolutions(session, loc)
    assert [(r.decision, r.target_id, r.method) for r in records] == [
        ("alias_of", canonical.id, "ai"),
        ("split", canonical.id, "manual"),
    ]
    audit = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "location.split"))
    ).one()
    assert audit.meta == {"from": str(canonical.id), "relation": "alias_of"}
    # 補判與同名稱再出現都不會把它併回去
    again = Resolver(session)
    await backfill_locations(session, run=again, principal=SERVICE)  # 只剩代表地點本身待補判
    await session.refresh(loc)
    assert loc.canonical_id is None
    reused = await resolve(session, "工五", again)
    assert again.calls == 0
    assert reused.canonical_id is None


async def test_split_parent(session: AsyncSession) -> None:
    dorms = await add_location(session, "宿舍區")
    run = Resolver(session, answer("inside_of", "L1", quote="男九舍門口"))
    loc = await resolve(session, "男九舍", run, text="男九舍門口的車棚。")

    await split_location(session, ADMIN, loc.id)
    await session.commit()

    await session.refresh(loc)
    assert loc.parent_id is None
    assert (await resolutions(session, loc))[-1].target_id == dorms.id


async def test_split_requires_alias_or_parent(session: AsyncSession) -> None:
    loc = await add_location(session, "工程五館")
    with pytest.raises(LocationSplitError):
        await split_location(session, ADMIN, loc.id)
    with pytest.raises(LocationSplitError):
        await split_location(session, ADMIN, uuid.uuid4())


# ---------- 補判 ----------


async def add_event_at(session: AsyncSession, loc: Location, text: str) -> Event:
    item = await add_item(session, await add_source(session, f"src-{uuid.uuid4().hex[:6]}"), text)
    event = Event(title=text[:20], type="facility", status="unverified", location_id=loc.id)
    session.add(event)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="mentions"))
    await session.flush()
    return event


async def test_backfill_resolves_once_and_rerun_is_stable(session: AsyncSession) -> None:
    canonical = await add_location(session, "工程五館")
    alias = await add_location(session, "工五")
    other = await add_location(session, "游藝館")
    await add_event_at(session, alias, "工五二樓走廊的燈一直閃。")
    await session.commit()
    run = Resolver(
        session,
        # 依名稱排序：工五、工程五館、游藝館；工程五館與游藝館沒有事件，不呼叫
        lambda: answer(
            "alias_of",
            target_of(run, "工程五館"),
            quote="工五二樓走廊",
            evidence_id=run.payloads[0]["evidence"][0]["evidence_id"],
        ),
    )

    assert await backfill_locations(session, run=run, principal=SERVICE) == 3
    await session.commit()

    assert run.calls == 1
    assert run.payloads[0]["new_location"]["name"] == "工五"
    assert run.payloads[0]["evidence"][0]["text"] == "工五二樓走廊的燈一直閃。"
    for loc in (canonical, alias, other):
        await session.refresh(loc)
    assert alias.canonical_id == canonical.id
    assert (canonical.canonical_id, other.canonical_id) == (None, None)
    state = {loc.name: (loc.canonical_id, loc.parent_id) for loc in (canonical, alias, other)}

    rerun = Resolver(session)
    assert await backfill_locations(session, run=rerun, principal=SERVICE) == 0
    assert rerun.calls == 0
    for loc in (canonical, alias, other):
        await session.refresh(loc)
    assert {
        loc.name: (loc.canonical_id, loc.parent_id) for loc in (canonical, alias, other)
    } == state


async def test_backfill_flattens_aliases_and_children_of_new_alias(
    session: AsyncSession,
) -> None:
    # 補判前已有別名與下層地點指向「工五」，之後「工五」本身才被判成「工程五館」的別名
    target = await add_location(session, "工程五館")
    middle = await add_location(session, "工五")
    alias = await add_location(session, "工五館", canonical_id=middle.id)
    child = await add_location(session, "工五二樓", parent_id=middle.id)
    for loc in (alias, child):
        session.add(
            LocationResolution(
                location_id=loc.id,
                decision="alias_of" if loc is alias else "inside_of",
                target_id=middle.id,
                method="ai",
                decided_by="pipeline",
                created_at=datetime.now(UTC),
            )
        )
    await add_event_at(session, middle, "工五二樓走廊的燈一直閃。")
    await session.commit()
    run = Resolver(
        session,
        lambda: answer(
            "alias_of",
            target_of(run, "工程五館"),
            quote="工五二樓走廊",
            evidence_id=run.payloads[0]["evidence"][0]["evidence_id"],
        ),
    )

    await backfill_locations(session, run=run, principal=SERVICE)
    await session.commit()

    assert run.calls == 1
    for loc in (target, middle, alias, child):
        await session.refresh(loc)
    assert middle.canonical_id == target.id
    assert alias.canonical_id == target.id  # 不留下別名的別名
    assert await canonical_id_of(session, alias.id) == target.id
    assert (child.canonical_id, child.parent_id) == (None, target.id)


# ---------- 管線 ----------


async def test_pipeline_resolves_new_event_location(session: AsyncSession) -> None:
    canonical = await add_location(session, "工程五館")
    item = await add_item(session, await add_source(session, "facebook"), TEXT)
    e = eid(item)
    extracted = extraction(e, location="工五")
    extracted["evidence_spans"] = [{"field": "location", "evidence_id": e, "quote": "工五"}]
    provider = SkillScript(
        {
            "event-extractor": [extracted],
            "event-classifier": [classification(e)],
            "location-resolver": [answer("alias_of", "L1", quote="工五二樓走廊", evidence_id=e)],
            "evidence-verifier": [verification({e: ("supports", "first_hand")}, "unverified")],
            "risk-assessor": [risk(e)],
        }
    )
    pipeline_deps = replace(deps(provider), check_relevance=False)

    result = await analyze_item(session, item.id, pipeline_deps)

    assert result.outcome is Outcome.CREATED
    event = await session.get(Event, result.event_id)
    assert event is not None
    loc = await session.get(Location, event.location_id)
    assert loc is not None
    await session.refresh(loc)
    assert (loc.name, loc.canonical_id) == ("工五", canonical.id)
    assert "location-resolver" in provider.called
    run = (
        await session.scalars(
            select(AIAnalysisRun).where(AIAnalysisRun.skill_name == "location-resolver")
        )
    ).one()
    assert run.event_id == event.id  # 跟 S01–S03 一樣補上 event_id


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


async def test_list_locations_groups_aliases_and_children(api: Api, session: AsyncSession) -> None:
    canonical = await add_location(session, "工程五館")
    dorms = await add_location(session, "宿舍區")
    alias = await add_location(session, "工五", canonical_id=canonical.id)
    child = await add_location(session, "男九舍", parent_id=dorms.id)
    await add_event_at(session, alias, "工五二樓走廊的燈一直閃。")
    await add_event_at(session, canonical, "工程五館停電。")
    await session.commit()

    body = (await api.client.get("/api/v1/admin/locations")).json()

    assert body["error"] is None
    by_name = {loc["name"]: loc for loc in body["data"]}
    assert set(by_name) == {"工程五館", "宿舍區"}  # 只列代表地點
    five = by_name["工程五館"]
    assert five["event_count"] == 2  # 含別名的事件
    assert [(a["id"], a["name"], a["event_count"]) for a in five["aliases"]] == [
        (str(alias.id), "工五", 1)
    ]
    assert [c["name"] for c in by_name["宿舍區"]["children"]] == ["男九舍"]
    assert by_name["宿舍區"]["children"][0]["id"] == str(child.id)


async def test_split_endpoint(api: Api, session: AsyncSession) -> None:
    canonical = await add_location(session, "工程五館")
    alias = await add_location(session, "工五", canonical_id=canonical.id)
    await session.commit()

    response = await api.client.post(f"/api/v1/admin/locations/{alias.id}/split")

    assert response.status_code == 200
    assert response.json()["data"] == {"id": str(alias.id), "canonical_id": None, "parent_id": None}
    again = await api.client.post(f"/api/v1/admin/locations/{alias.id}/split")
    assert again.json()["error"]["code"] == "CONFLICT"
    missing = await api.client.post(f"/api/v1/admin/locations/{uuid.uuid4()}/split")
    assert missing.json()["error"]["code"] == "NOT_FOUND"


async def test_location_endpoints_require_permissions(api: Api, session: AsyncSession) -> None:
    alias = await add_location(session, "工五")
    await session.commit()
    api.as_principal(
        Principal(kind="service", subject="notifier", roles=frozenset({Role.SERVICE_NOTIFIER}))
    )

    assert (await api.client.get("/api/v1/admin/locations")).status_code == 403
    assert (await api.client.post(f"/api/v1/admin/locations/{alias.id}/split")).status_code == 403
