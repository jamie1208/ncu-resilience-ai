"""P8-T05：S12 series-analyst 系列分析、名稱、原因確認、系列措施與時間軸 API。

- 輸入：系列的子事件（代號 E1…，不含 false）與證據、程式算出的每週件數與突增；
- 引用驗證：引用不存在的子事件、原句不在該事件的證據中、只有 1 件支持的規律，整項丟棄；
- 計數由程式依引用的子事件算（y／x），規律與原因一律標「推測，尚未查證」；
- 1 件的系列也分析，規律區 abstain（too_few_events）；
- 每個系列每天（台北時間）最多分析一次；有新事件加入或移出才需要重新分析；
- 每日費用上限：超過時記為 failed，不改名稱；
- S12 的共通名稱取代第一起事件的標題，人工改過的不覆蓋；名稱帶地點或日期時不採用；
- 原因與規律提到特定個人、群體、店家或單位時標記（不擋）；
- 原因的人工確認（`series.cause_decide`）、手動改名（`series.rename`）、從建議一鍵加入措施；
- 時間軸 API：粒度、範圍、地點篩選、措施標記與前後比較。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMError
from app.ai.llm.config import load_llm_config
from app.ai.llm.cost import CostLimitExceededError
from app.ai.runner.run import SkillRunResult, SqlRunStore, run_skill
from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.main import create_app
from app.models import AuditLog
from app.models.event import Event, EventEvidence, Location
from app.models.response_action import ResponseAction
from app.models.series import EventSeries, EventSeriesMember, SeriesAnalysis
from app.resilience.actions import ActionState, transition_action
from app.series.analysis import (
    S12,
    SPECULATIVE_LABEL,
    AnalysisConflictError,
    AnalysisInvalidError,
    CauseDecision,
    analyze_series,
    decide_series_cause,
    rename_series,
    stale_series,
)
from tests.integration.test_pipeline import SKILLS, SkillScript, add_item, add_source

pytestmark = pytest.mark.anyio

ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
SERVICE = Principal(kind="service", subject="pipeline", roles=frozenset({Role.SERVICE_AI}))
NOW = datetime(2026, 11, 9, 4, 0, tzinfo=UTC)  # 台北 12:00
TODAY = date(2026, 11, 9)
TAIPEI = ZoneInfo("Asia/Taipei")

Builder = Callable[[Mapping[str, Any]], dict[str, Any]]


# ---------- 資料 ----------


async def add_series(
    session: AsyncSession,
    events: list[tuple[str, str, str | None, date | None]],
    *,
    category: str = "traffic",
    status: str = "unverified",
) -> tuple[EventSeries, list[Event]]:
    """建立系列與子事件；每個事件 (標題, 證據內文, 地點, 發生日期) 各一篇證據。"""
    series = EventSeries(category=category, title=events[0][0])
    session.add(series)
    await session.flush()
    out = []
    for i, (title, text, place, day) in enumerate(events):
        out.append(
            await add_member(session, series, title, text, place, day, status=status, offset=i)
        )
    return series, out


async def add_member(
    session: AsyncSession,
    series: EventSeries,
    title: str,
    text: str,
    place: str | None,
    day: date | None,
    *,
    status: str = "unverified",
    offset: int = 0,
    joined_at: datetime | None = None,
) -> Event:
    location_id = None
    if place is not None:
        loc = await session.scalar(select(Location).where(Location.name == place))
        if loc is None:
            loc = Location(name=place)
            session.add(loc)
            await session.flush()
        location_id = loc.id
    source = await add_source(session, f"src-{uuid.uuid4().hex[:8]}")
    item = await add_item(session, source, text)
    event = Event(
        title=title,
        type=series.category,
        status=status,
        summary=f"{title}。",
        location_id=location_id,
        occurrence_start_date=day,
    )
    session.add(event)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    session.add(
        EventSeriesMember(
            event_id=event.id,
            series_id=series.id,
            method="ai",
            joined_at=joined_at or NOW - timedelta(days=30 - offset),
        )
    )
    await session.flush()
    return event


THREE = [
    ("工五機車被撞", "晚上停在工五路邊的機車被撞到移位", "工五", date(2026, 10, 6)),
    ("宵夜街機車被擦撞", "下午停在宵夜街旁邊後照鏡被擦撞斷掉", "宵夜街", date(2026, 10, 15)),
    ("工程五館機車被撞倒", "昨晚停工程五館外面白線外整台被撞倒", "工程五館", date(2026, 10, 27)),
]


# ---------- S12 腳本 ----------


def ev_of(payload: Mapping[str, Any], code: str) -> str:
    """子事件代號的第一份證據 id（代號不存在時用 E1，供預設輸出在 1 件的系列中使用）。"""
    events = {e["event_id"]: e for e in payload["events"]}
    event = events.get(code, events["E1"])
    return str(event["evidence_ids"][0])


def text_of(payload: Mapping[str, Any], evidence_id: str) -> str:
    return str(next(e["text"] for e in payload["evidence"] if e["evidence_id"] == evidence_id))


def span(payload: Mapping[str, Any], code: str, length: int = 6) -> dict[str, str]:
    evidence_id = ev_of(payload, code)
    return {"evidence_id": evidence_id, "quote": text_of(payload, evidence_id)[:length]}


def output(payload: Mapping[str, Any], **override: Any) -> dict[str, Any]:
    """一份合格的 S12 輸出：地點分組、1 個規律（E1、E3）、1 個原因、2 項建議。"""
    e1, e3 = ev_of(payload, "E1"), ev_of(payload, "E3")
    base: dict[str, Any] = {
        "status": "ok",
        "title": "停放機車遭碰撞",
        "burst_explanations": [],
        "location_groups": [
            {"label": "工程五館", "event_ids": ["E1", "E3"], "reason": "工五是工程五館的簡稱。"}
        ],
        "pattern_status": "ok",
        "pattern_abstain_reason": None,
        "patterns": [
            {
                "pattern_id": "p1",
                "description": "都發生在晚間、停在路邊。",
                "event_ids": ["E1", "E3"],
                "evidence_ids": [e1, e3],
                "evidence_spans": [span(payload, "E1"), span(payload, "E3")],
            }
        ],
        "causes": [
            {
                "cause_id": "c1",
                "description": "可能是路邊停放區太靠近車道。",
                "event_ids": ["E1", "E3"],
                "evidence_ids": [e3],
                "evidence_spans": [span(payload, "E3")],
                "to_verify": "向總務處確認停車區與車道距離。",
            }
        ],
        "measures": [
            {
                "action": "盡量停在畫線的機車格內。",
                "audience": "students",
                "horizon": "short",
                "priority": "medium",
                "kind": "prevent",
                "cause_id": None,
                "responsible_unit": None,
                "evidence_ids": [e1],
            },
            {
                "action": "查看工程五館外停車區與車道的距離。",
                "audience": "school",
                "horizon": "mid",
                "priority": "medium",
                "kind": "investigate",
                "cause_id": "c1",
                "responsible_unit": "總務處",
                "evidence_ids": [e3],
            },
        ],
        "evidence_ids": [e1, e3],
        "reason": "停放機車被碰撞，集中在工程五館外。",
        "abstain_reason": None,
    }
    return {**base, **override}


class Analyst:
    """以 fake provider 跑真的 Skill Runner（schema 與業務驗證），記下每次的輸入。"""

    def __init__(self, session: AsyncSession, *builders: Builder | LLMError) -> None:
        self.session = session
        self.builders = list(builders)
        self.payloads: list[Mapping[str, Any]] = []

    async def __call__(self, payload: Mapping[str, Any]) -> SkillRunResult:
        self.payloads.append(payload)
        builder = self.builders.pop(0)
        script = builder if isinstance(builder, LLMError) else builder(payload)
        return await run_skill(
            SKILLS[S12],
            payload,
            provider=SkillScript({S12: [script] * 3}),  # Runner 最多重試 2 次
            config=load_llm_config(),
            store=SqlRunStore(self.session),
            trace_id="trace-s12",
        )

    @property
    def calls(self) -> int:
        return len(self.payloads)


async def analyze(
    session: AsyncSession, series: EventSeries, run: Analyst, *, now: datetime = NOW
) -> SeriesAnalysis | None:
    result = await analyze_series(session, series.id, run=run, principal=SERVICE, now=now)
    await session.commit()
    return result


async def audits(session: AsyncSession, action: str) -> list[AuditLog]:
    return list(await session.scalars(select(AuditLog).where(AuditLog.action == action)))


# ---------- 輸入 ----------


async def test_payload_codes_events_and_counts_by_program(session: AsyncSession) -> None:
    series, events = await add_series(session, THREE)
    await add_member(session, series, "被否認的", "其實沒有被撞", "工五", date(2026, 10, 20),
                     status="false")  # fmt: skip
    run = Analyst(session, output)

    analysis = await analyze(session, series, run)

    payload = run.payloads[0]
    assert [e["event_id"] for e in payload["events"]] == ["E1", "E2", "E3"]  # 依日期排序
    assert [e["title"] for e in payload["events"]] == [t for t, *_ in THREE]
    assert [e["location"] for e in payload["events"]] == ["工五", "宵夜街", "工程五館"]
    assert payload["series"] == {"category": "traffic", "title": "工五機車被撞", "event_count": 3}
    assert len(payload["weekly_counts"]) == 26
    assert sum(w["count"] for w in payload["weekly_counts"]) == 3  # false 不計
    assert payload["bursts"] == []
    assert analysis is not None and analysis.status == "ok"
    assert {e["code"]: e["event_id"] for e in analysis.events} == {
        f"E{i}": str(ev.id) for i, ev in enumerate(events, start=1)
    }


async def test_bursts_come_from_program_detection(session: AsyncSession) -> None:
    quiet = [(f"事件{i}", f"機車被撞第{i}次", "工五", date(2026, 6, 1) + timedelta(weeks=i))
             for i in range(5)]  # fmt: skip
    burst = [(f"突增{i}", f"颱風夜機車被撞{i}", "工五", date(2026, 10, 20)) for i in range(6)]
    series, _ = await add_series(session, quiet + burst)
    run = Analyst(session, lambda p: output(
        p,
        burst_explanations=[{
            "burst_id": "b1", "explanation": "同一週有颱風。", "event_ids": ["E6", "E7"],
            "evidence_ids": [ev_of(p, "E6")],
        }, {
            "burst_id": "b2", "explanation": "捏造的突增。", "event_ids": ["E1"],
            "evidence_ids": [ev_of(p, "E1")],
        }],
    ))  # fmt: skip

    analysis = await analyze(session, series, run)

    assert run.payloads[0]["bursts"] == [
        {"burst_id": "b1", "period_start": "2026-10-19", "count": 6, "expected": 0.5}
    ]
    assert analysis is not None
    assert [b["burst_id"] for b in analysis.bursts] == ["b1"]  # b2 不在輸入中，丟棄
    assert analysis.bursts[0]["period_start"] == "2026-10-19"
    assert analysis.bursts[0]["count"] == 6  # 件數由程式填入
    assert {d["section"] for d in analysis.dropped} == {"burst_explanations"}


# ---------- 引用驗證 ----------


async def test_items_citing_unknown_events_are_dropped(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, lambda p: output(
        p,
        location_groups=[
            {"label": "工程五館", "event_ids": ["E1", "E3"], "reason": "同一棟。"},
            {"label": "捏造", "event_ids": ["E1", "E9"], "reason": "E9 不存在。"},
        ],
        patterns=[output(p)["patterns"][0], {
            **output(p)["patterns"][0], "pattern_id": "p2", "event_ids": ["E1", "E9"],
        }],
    ))  # fmt: skip

    analysis = await analyze(session, series, run)

    assert analysis is not None
    assert [g["label"] for g in analysis.location_groups] == ["工程五館"]
    assert [p["pattern_id"] for p in analysis.patterns] == ["p1"]
    reasons = {(d["section"], d["reason"]) for d in analysis.dropped}
    assert reasons == {("location_groups", "unknown_event"), ("patterns", "unknown_event")}


async def test_quote_must_come_from_cited_events_evidence(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, lambda p: output(p, patterns=[{
        **output(p)["patterns"][0],
        "event_ids": ["E1", "E3"],
        "evidence_ids": [ev_of(p, "E2")],
        "evidence_spans": [span(p, "E2")],  # E2 的證據，不屬於 E1、E3
    }]))  # fmt: skip

    analysis = await analyze(session, series, run)

    assert analysis is not None
    assert analysis.patterns == []
    assert analysis.pattern_status == "abstained"
    assert analysis.pattern_abstain_reason == "no_valid_pattern"
    assert ("patterns", "quote_not_in_events") in {
        (d["section"], d["reason"]) for d in analysis.dropped
    }


async def test_pattern_supported_by_one_event_is_dropped(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, lambda p: output(p, patterns=[{
        **output(p)["patterns"][0], "event_ids": ["E1", "E1"],
        "evidence_ids": [ev_of(p, "E1")], "evidence_spans": [span(p, "E1")],
    }]))  # fmt: skip

    analysis = await analyze(session, series, run)

    assert analysis is not None
    assert analysis.patterns == []
    assert ("patterns", "fewer_than_two_events") in {
        (d["section"], d["reason"]) for d in analysis.dropped
    }


async def test_counts_and_labels_are_set_by_program(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    analysis = await analyze(session, series, Analyst(session, output))

    assert analysis is not None
    pattern = analysis.patterns[0]
    assert (pattern["event_count"], pattern["total"]) == (2, 3)
    assert pattern["label"] == SPECULATIVE_LABEL == "推測，尚未查證"
    assert analysis.causes[0]["label"] == SPECULATIVE_LABEL
    assert analysis.causes[0]["to_verify"] == "向總務處確認停車區與車道距離。"
    assert (analysis.location_groups[0]["event_count"], analysis.location_groups[0]["total"]) == (
        2,
        3,
    )
    codes = {e["code"]: e["event_id"] for e in analysis.events}
    assert pattern["event_ids"] == [codes["E1"], codes["E3"]]  # 代號換成事件 id
    school = analysis.measures[1]
    assert (school["audience"], school["cause_id"], school["responsible_unit"]) == (
        "school", "c1", "總務處",
    )  # fmt: skip
    assert analysis.measures[0]["responsible_unit"] is None


async def test_measure_pointing_to_dropped_cause_becomes_general(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, lambda p: output(p, causes=[{
        **output(p)["causes"][0], "event_ids": ["E9"],
    }]))  # fmt: skip

    analysis = await analyze(session, series, run)

    assert analysis is not None
    assert analysis.causes == []
    assert analysis.measures[1]["cause_id"] is None


async def test_pii_in_text_is_masked_or_rejected(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, lambda p: output(p, reason="請打 0912-345-678 詢問。"))

    analysis = await analyze(session, series, run)

    # Runner 的業務驗證擋下含個資的輸出，重試用完後記為 abstain
    assert analysis is not None
    assert analysis.status in {"abstained", "failed"}


# ---------- 1 件、abstain、失敗、費用上限 ----------


async def test_single_event_series_is_analyzed_without_patterns(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE[:1])
    run = Analyst(session, lambda p: output(
        p, location_groups=[], patterns=[], pattern_status="abstain",
        pattern_abstain_reason="too_few_events", causes=[], measures=[],
        evidence_ids=[ev_of(p, "E1")],
    ))  # fmt: skip

    analysis = await analyze(session, series, run)

    assert run.calls == 1
    assert analysis is not None and analysis.status == "ok"
    assert (analysis.pattern_status, analysis.pattern_abstain_reason) == (
        "abstained", "too_few_events",
    )  # fmt: skip


async def test_single_event_series_never_keeps_a_pattern(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE[:1])
    run = Analyst(session, lambda p: output(p, location_groups=[], patterns=[{
        **output(p)["patterns"][0], "event_ids": ["E1"],
        "evidence_ids": [ev_of(p, "E1")], "evidence_spans": [span(p, "E1")],
    }], causes=[], measures=[], evidence_ids=[ev_of(p, "E1")]))  # fmt: skip

    analysis = await analyze(session, series, run)

    assert analysis is not None
    assert analysis.patterns == []
    assert analysis.pattern_abstain_reason == "too_few_events"


async def test_abstain_keeps_previous_title(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, lambda p: {
        "status": "abstain", "title": None, "burst_explanations": [], "location_groups": [],
        "pattern_status": "abstain", "pattern_abstain_reason": "no_pattern", "patterns": [],
        "causes": [], "measures": [], "evidence_ids": [], "reason": "看不出問題。",
        "abstain_reason": "insufficient_evidence",
    })  # fmt: skip

    analysis = await analyze(session, series, run)

    assert analysis is not None
    assert (analysis.status, analysis.abstain_reason) == ("abstained", "insufficient_evidence")
    await session.refresh(series)
    assert (series.title, series.title_source) == ("工五機車被撞", "first_event")


async def test_cost_limit_is_recorded_as_failed(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, CostLimitExceededError("超過每日上限"))

    analysis = await analyze(session, series, run)

    assert analysis is not None
    assert analysis.status == "failed"
    assert analysis.patterns == [] and analysis.title is None
    await session.refresh(series)
    assert series.title_source == "first_event"


async def test_no_evidence_means_no_call(session: AsyncSession) -> None:
    series = EventSeries(category="traffic", title="沒有證據")
    session.add(series)
    await session.flush()
    event = Event(title="沒有證據", type="traffic", status="unverified")
    session.add(event)
    await session.flush()
    session.add(EventSeriesMember(event_id=event.id, series_id=series.id, method="ai",
                                  joined_at=NOW))  # fmt: skip
    run = Analyst(session)

    assert await analyze(session, series, run) is None
    assert run.calls == 0


# ---------- 每天最多一次、重新分析 ----------


async def test_at_most_once_per_series_per_taipei_day(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, output, output)

    assert await analyze(session, series, run) is not None
    late = datetime(2026, 11, 9, 15, 59, tzinfo=UTC)  # 台北 23:59，同一天
    assert await analyze(session, series, run, now=late) is None
    assert run.calls == 1
    next_day = datetime(2026, 11, 9, 16, 0, tzinfo=UTC)  # 台北隔天 00:00
    assert await analyze(session, series, run, now=next_day) is not None
    assert run.calls == 2


async def test_failed_attempt_also_counts_for_the_day(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, CostLimitExceededError("超過"), output)

    await analyze(session, series, run)
    assert await analyze(session, series, run) is None
    assert run.calls == 1


async def test_stale_series_need_reanalysis(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    other, _ = await add_series(session, THREE[:1])
    yesterday = NOW - timedelta(days=1)

    assert set(await stale_series(session, now=yesterday)) >= {series.id, other.id}
    await analyze(session, series, Analyst(session, output), now=yesterday)
    assert series.id not in await stale_series(session, now=NOW)  # 沒有變動
    await add_member(session, series, "新的一起", "又有機車被撞", "工五", date(2026, 11, 8),
                     joined_at=NOW - timedelta(hours=1))  # fmt: skip
    await session.commit()
    assert series.id in await stale_series(session, now=NOW)  # 有新事件加入
    await analyze(session, series, Analyst(session, output), now=NOW)
    assert series.id not in await stale_series(session, now=NOW)  # 今天已分析


async def test_reanalysis_audited(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    analysis = await analyze(session, series, Analyst(session, output))

    rows = await audits(session, "series.analyze")
    assert len(rows) == 1
    assert rows[0].target == f"series:{series.id}"
    assert rows[0].meta == {
        "analysis_id": str(analysis.id) if analysis else None,
        "status": "ok",
        "dropped": 0,
        "title_changed": True,
    }


# ---------- 名稱 ----------


async def test_ai_title_replaces_first_event_title(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    await analyze(session, series, Analyst(session, output))
    await session.refresh(series)
    assert (series.title, series.title_source) == ("停放機車遭碰撞", "ai")


@pytest.mark.parametrize("title", ["工五停放機車遭碰撞", "10/6 機車被撞", "11月機車碰撞"])
async def test_title_with_location_or_date_is_rejected(session: AsyncSession, title: str) -> None:
    series, _ = await add_series(session, THREE)
    analysis = await analyze(session, series, Analyst(session, lambda p: output(p, title=title)))
    assert analysis is not None and analysis.title is None
    assert ("title", "location_or_date") in {(d["section"], d["reason"]) for d in analysis.dropped}
    await session.refresh(series)
    assert series.title_source == "first_event"


async def test_manual_title_is_not_overwritten(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    await rename_series(session, ADMIN, series.id, "機車停放被碰撞")
    await session.commit()

    analysis = await analyze(session, series, Analyst(session, output))

    assert analysis is not None and analysis.title == "停放機車遭碰撞"  # 建議保留在分析中
    await session.refresh(series)
    assert (series.title, series.title_source) == ("機車停放被碰撞", "manual")


async def test_rename_audits_from_and_to(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    await rename_series(session, ADMIN, series.id, "  停放機車遭碰撞 ")
    await session.commit()
    rows = await audits(session, "series.rename")
    assert [(r.target, r.meta) for r in rows] == [
        (f"series:{series.id}", {"from": "工五機車被撞", "to": "停放機車遭碰撞"})
    ]
    with pytest.raises(AnalysisInvalidError):
        await rename_series(session, ADMIN, series.id, " ")
    with pytest.raises(AnalysisInvalidError):
        await rename_series(session, ADMIN, series.id, "聯絡 0912-345-678")


# ---------- 指控比對 ----------


async def test_causes_mentioning_parties_are_flagged_not_blocked(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    run = Analyst(session, lambda p: output(p, causes=[
        {**output(p)["causes"][0], "description": "可能是某店家員工偷偷移動機車。"},
        {**output(p)["causes"][0], "cause_id": "c2", "description": "可能是車道太窄。"},
    ], patterns=[{**output(p)["patterns"][0], "description": "都在警衛巡邏的空檔。"}]))  # fmt: skip

    analysis = await analyze(session, series, run)

    assert analysis is not None
    assert [c["mentions_party"] for c in analysis.causes] == [True, False]
    assert analysis.patterns[0]["mentions_party"] is True
    assert len(analysis.causes) == 2  # 不擋


# ---------- 原因確認 ----------


async def test_confirm_and_rollback_cause(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    analysis = await analyze(session, series, Analyst(session, output))
    assert analysis is not None

    with pytest.raises(AnalysisInvalidError):
        await decide_series_cause(session, ADMIN, series.id, analysis.id, "c1",
                                  CauseDecision.CONFIRMED, basis=" ")  # fmt: skip
    await decide_series_cause(session, ADMIN, series.id, analysis.id, "c1",
                              CauseDecision.CONFIRMED, basis="總務處回覆確認車道太窄")  # fmt: skip
    with pytest.raises(AnalysisConflictError):
        await decide_series_cause(session, ADMIN, series.id, analysis.id, "c1",
                                  CauseDecision.CONFIRMED, basis="再確認")  # fmt: skip
    await decide_series_cause(session, ADMIN, series.id, analysis.id, "c1",
                              CauseDecision.HYPOTHESIZED)  # fmt: skip
    await session.commit()

    rows = await audits(session, "series.cause_decide")
    meta = {"analysis_id": str(analysis.id), "cause_id": "c1"}
    assert [r.meta for r in rows] == [
        {**meta, "from": "hypothesized", "to": "confirmed"},
        {**meta, "from": "confirmed", "to": "hypothesized"},
    ]
    assert all("總務處" not in str(r.meta) for r in rows)  # 依據文字不進稽核


async def test_only_latest_analysis_causes_can_be_decided(session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    old = await analyze(session, series, Analyst(session, output), now=NOW - timedelta(days=1))
    await analyze(session, series, Analyst(session, output))
    assert old is not None
    with pytest.raises(AnalysisConflictError):
        await decide_series_cause(session, ADMIN, series.id, old.id, "c1",
                                  CauseDecision.CONFIRMED, basis="已確認")  # fmt: skip


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


async def test_get_series_includes_latest_analysis(api: Api, session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    await analyze(session, series, Analyst(session, output))

    body = (await api.client.get(f"/api/v1/admin/series/{series.id}")).json()["data"]

    assert body["title_source"] == "ai"
    analysis = body["analysis"]
    assert analysis["status"] == "ok"
    assert analysis["patterns"][0]["label"] == SPECULATIVE_LABEL
    assert analysis["causes"][0]["decision"] == "hypothesized"
    assert analysis["measures"][1]["tracked_action_id"] is None
    assert body["latest_attempt"]["status"] == "ok"


async def test_patch_renames_series(api: Api, session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    await session.commit()

    response = await api.client.patch(
        f"/api/v1/admin/series/{series.id}", json={"title": "停放機車遭碰撞"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["title_source"] == "manual"
    assert (await api.client.patch(f"/api/v1/admin/series/{series.id}",
                                   json={"title": ""})).status_code == 422  # fmt: skip
    assert (await api.client.patch(f"/api/v1/admin/series/{uuid.uuid4()}",
                                   json={"title": "x"})).status_code == 404  # fmt: skip
    api.as_principal(SERVICE)
    assert (await api.client.patch(f"/api/v1/admin/series/{series.id}",
                                   json={"title": "x"})).status_code == 403  # fmt: skip


async def test_add_action_from_measure(api: Api, session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    analysis = await analyze(session, series, Analyst(session, output))
    assert analysis is not None
    url = f"/api/v1/admin/series/{series.id}/actions"

    created = await api.client.post(url, json={"analysis_id": str(analysis.id), "measure_index": 1})
    assert created.status_code == 201
    data = created.json()["data"]
    assert (data["title"], data["owner"], data["state"]) == (
        "查看工程五館外停車區與車道的距離。", "總務處", "discovered",
    )  # fmt: skip
    action = await session.get(ResponseAction, uuid.UUID(data["id"]))
    assert action is not None
    assert (action.series_id, action.event_id, action.origin) == (series.id, None, "series_measure")

    again = await api.client.post(url, json={"analysis_id": str(analysis.id), "measure_index": 1})
    assert again.status_code == 409
    students = await api.client.post(
        url, json={"analysis_id": str(analysis.id), "measure_index": 0}
    )
    assert students.status_code == 422
    missing = await api.client.post(url, json={"analysis_id": str(analysis.id), "measure_index": 9})
    assert missing.status_code == 422

    body = (await api.client.get(f"/api/v1/admin/series/{series.id}")).json()["data"]
    assert body["analysis"]["measures"][1]["tracked_action_id"] == data["id"]
    assert [a["id"] for a in body["actions"]] == [data["id"]]


async def test_cause_decision_api(api: Api, session: AsyncSession) -> None:
    series, _ = await add_series(session, THREE)
    analysis = await analyze(session, series, Analyst(session, output))
    assert analysis is not None
    url = f"/api/v1/admin/series/{series.id}/causes/c1/decision"

    ok = await api.client.post(url, json={"analysis_id": str(analysis.id), "decision": "confirmed",
                                          "basis": "總務處確認"})  # fmt: skip
    assert ok.status_code == 201
    body = (await api.client.get(f"/api/v1/admin/series/{series.id}")).json()["data"]
    assert body["analysis"]["causes"][0]["decision"] == "confirmed"
    assert body["analysis"]["causes"][0]["label"] == "已確認"
    missing = await api.client.post(f"/api/v1/admin/series/{series.id}/causes/c9/decision",
                                    json={"analysis_id": str(analysis.id), "decision": "confirmed",
                                          "basis": "x"})  # fmt: skip
    assert missing.status_code == 404


async def test_timeline_api(api: Api, session: AsyncSession) -> None:
    today = datetime.now(TAIPEI).date()  # API 以實際的今天計算範圍
    before = [(f"前{i}", f"機車被撞{i}", "工五", today - timedelta(days=50 - i)) for i in range(6)]
    after = [(f"後{i}", f"機車被撞後{i}", "工程五館", today - timedelta(days=20 - i))
             for i in range(5)]  # fmt: skip
    series, _ = await add_series(session, before + after)
    action = ResponseAction(series_id=series.id, origin="manual", title="加裝照明",
                            state="discovered")  # fmt: skip
    session.add(action)
    await session.flush()
    await transition_action(session, ADMIN, action.id, ActionState.HANDLING)
    await session.commit()

    url = f"/api/v1/admin/series/{series.id}/timeline"
    data = (await api.client.get(url, params={"granularity": "week", "range": "3M"})).json()["data"]

    assert data["granularity"] == "week" and data["range"] == "3M"
    assert sum(p["count"] for p in data["points"]) == 11
    assert all("moving_average" in p for p in data["points"])
    assert [m["kind"] for m in data["markers"]] == ["started"]
    comparison = data["comparisons"][0]
    assert comparison["action_id"] == str(action.id)
    assert "變少不一定是措施的功勞" in comparison["caveat"]
    assert {loc["name"] for loc in data["locations"]} == {"工五", "工程五館"}

    filtered = (await api.client.get(url, params={"granularity": "day", "range": "3M",
                                                  "location": "工五"})).json()["data"]  # fmt: skip
    assert sum(p["count"] for p in filtered["points"]) == 6
    bad = await api.client.get(url, params={"granularity": "year"})
    assert bad.status_code == 422


# ---------- 管線 ----------


async def test_pipeline_queues_series_analysis_after_grouping(session: AsyncSession) -> None:
    from dataclasses import replace

    from app.pipeline.analyze_item import Outcome, analyze_item
    from tests.integration.test_pipeline import (
        classification,
        deps,
        eid,
        extraction,
        risk,
        verification,
    )
    from tests.integration.test_series_grouping import add_event, answer, assign

    existing = await add_event(session, "工程五館停電")
    existing.embedding = None
    series_id = await assign(session, existing, None)
    item = await add_item(session, await add_source(session, "facebook"), "科學一館停電了。")
    e = eid(item)
    matched = answer("S1")
    matched["evidence_ids"] = [e]
    matched["evidence_spans"] = [{"evidence_id": e, "quote": "科學一館停電"}]
    provider = SkillScript(
        {
            "event-extractor": [extraction(e)],
            "event-classifier": [classification(e)],
            "location-resolver": [],
            "evidence-verifier": [verification({e: ("supports", "first_hand")}, "unverified")],
            "series-matcher": [matched],
            "risk-assessor": [risk(e)],
        }
    )
    queued: list[tuple[uuid.UUID, str]] = []
    pipeline_deps = replace(
        deps(provider),
        check_relevance=False,
        group_series=True,
        series_hook=lambda sid, trace: queued.append((sid, trace)),
    )

    result = await analyze_item(session, item.id, pipeline_deps)

    assert result.outcome is Outcome.CREATED
    assert queued == [(series_id, result.trace_id)]
    assert S12 not in provider.called  # 分析是獨立的任務，不在管線中呼叫
