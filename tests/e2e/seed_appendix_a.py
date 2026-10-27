"""附錄 A（A01–A08，個人版）端到端測試的種子資料（P3-T10）。

在獨立的 e2e 資料庫（`NCU_DB_NAME`，名稱必須以 `_e2e` 結尾）上：
1. 執行 migration 並清空所有資料表；
2. 建立唯一的使用者（帳號、密碼來自 `E2E_USERNAME`／`E2E_PASSWORD`，只供這個測試資料庫使用）；
3. 以真實的冪等寫入（`store_items`）與分析管線（`analyze_item`，含 S05 規則、S06 引擎、
   P3-T08 通知同步）建立各案例；模型以腳本化的假供應商代替，不呼叫真實 API；
4. 檢查只有資料庫看得到的不變量（A03 證據數、A07 冪等），不符合時以非零結束碼失敗，
   讓 Playwright 的 webServer 啟動失敗。

Playwright 再透過真實 API 與前端驗證畫面上的預期結果（tests/e2e/appendix-a.e2e.ts）。

用法（Playwright 設定會自動執行）：
    cd backend && NCU_DB_NAME=ncu_resilience_e2e uv run python ../tests/e2e/seed_appendix_a.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import asyncpg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.ai.llm.base import LLMResult, Message, Usage  # noqa: E402
from app.ai.llm.config import load_llm_config  # noqa: E402
from app.ai.llm.fake_provider import FakeEmbeddingProvider  # noqa: E402
from app.ai.runner.loader import discover_skills  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.security import Principal, Role  # noqa: E402
from app.ingestion.normalize import NormalizedItem, content_hash  # noqa: E402
from app.ingestion.store import store_items  # noqa: E402
from app.models.alert import Alert, AlertDelivery  # noqa: E402
from app.models.event import Event, EventEvidence  # noqa: E402
from app.models.source import Source, SourceItem, SourceItemVersion  # noqa: E402
from app.pipeline.analyze_item import Outcome, PipelineDeps, analyze_item  # noqa: E402
from app.risk.engine import load_policy  # noqa: E402
from app.services.auth import create_or_reset_admin  # noqa: E402
from sqlalchemy import URL, func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

INGESTION = Principal(kind="service", subject="e2e-seed", roles=frozenset({Role.SERVICE_INGESTION}))
FETCHED = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
SOURCES = {
    "ncu-announcements": "中央大學公告",
    "facebook": "NCU 社團（人工匯入）",
    "news": "新聞",
}

# 各案例的事件標題（Playwright 依標題找事件；同類別事件的日期相隔超過候選召回的 ±7 天，
# 讓每個案例只會召回自己的候選事件）
A01_TITLE = "工程五館外牆整修施工"
A02_TITLE = "室友疑似偷走宿舍交誼廳筆電"
A03_TITLE = "總圖書館自習室筆電失竊"
A04_TITLES = ("宵夜街機車失竊", "後門停車場機車失竊")
A05_TITLE = "資工系王小明老師性騷擾學生"
A06_TITLE = "工程四館火災"
A07_TITLE = "中大路與校門口車禍封路"
A08_TITLE = "學生餐廳疑似食物中毒"


# ---------- 腳本化的假模型 ----------

Script = Mapping[str, Any] | Callable[[], Mapping[str, Any]]


class SkillScript:
    """依 Skill 排隊回應；佇列剩最後一個時重複使用它（涵蓋 Skill Runner 的重試）。"""

    name = "fake"

    def __init__(self, scripts: Mapping[str, Sequence[Script]]) -> None:
        self._queues = {skill: deque(items) for skill, items in scripts.items()}

    async def generate_structured(
        self,
        messages: Sequence[Message],
        json_schema: Mapping[str, Any],
        *,
        schema_name: str,
        model: str,
        timeout: float,
        max_output_tokens: int,
    ) -> LLMResult:
        skill = schema_name.removesuffix("_output").replace("_", "-")
        queue = self._queues.get(skill)
        if not queue:
            raise RuntimeError(f"種子腳本沒有預期 {skill} 會被呼叫")
        item = queue.popleft() if len(queue) > 1 else queue[0]
        output = item() if callable(item) else item
        return LLMResult(
            provider=self.name,
            model=model,
            text=json.dumps(output, ensure_ascii=False),
            usage=Usage(input_tokens=100, output_tokens=50),
            response_id=None,
            latency_ms=1,
        )


def relevance(e: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "is_campus_relevant": True,
        "evidence_ids": [e],
        "evidence_spans": [],
        "reason": "內容與校園有關。",
        "confidence": 0.9,
        "abstain_reason": None,
    }


def extraction(e: str, title: str, day: str, location: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "is_event": True,
        "title": title,
        "occurrence_start_date": day,
        "occurrence_start_time": None,
        "occurrence_end_date": None,
        "occurrence_end_time": None,
        "location": location,
        "mentions_person": False,
        "summary": {"text": f"{title}。", "evidence_ids": [e]},
        "evidence_ids": [e],
        "evidence_spans": [],
        "reason": "寫明事件的時間與地點。",
        "confidence": 0.9,
        "abstain_reason": None,
    }


def classification(e: str, category: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "category": category,
        "tags": [],
        "classification_confidence": 0.9,
        "evidence_ids": [e],
        "evidence_spans": [],
        "reason": "依內容分類。",
        "abstain_reason": None,
    }


def linking(article: str, candidate: Sequence[str], relation: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "relation": relation,
        "confidence": 0.9,
        "evidence_ids": [article, *candidate],
        "evidence_spans": [],
        "reason": "比對時間、地點與內容。",
        "abstain_reason": None,
    }


def verification(
    stances: Mapping[str, tuple[str, str]], suggested: str, *, evidence_ids: bool = True
) -> dict[str, Any]:
    return {
        "status": "ok",
        "assessments": [
            {"evidence_id": e, "stance": stance, "claim_type": claim}
            for e, (stance, claim) in stances.items()
        ],
        "suggested_status": suggested,
        "confidence": 0.8,
        "evidence_ids": list(stances) if evidence_ids else [],
        "evidence_spans": [],
        "reason": "依各份證據的立場判斷。",
        "abstain_reason": None,
    }


HIGH = {"personal_safety": 70, "operations": 60, "property": 60, "reputation": 50, "scope": 60}
LOW = {"personal_safety": 5, "operations": 30, "property": 5, "reputation": 5, "scope": 20}
MEDIUM = {"personal_safety": 30, "operations": 30, "property": 50, "reputation": 20, "scope": 30}


def risk(e: str, scores: Mapping[str, int]) -> dict[str, Any]:
    return {
        "status": "ok",
        "dimensions": {
            name: {"score": score, "uncertainty": 0.3, "reason": "依來源內容評分。"}
            for name, score in scores.items()
        },
        "evidence_ids": [e],
        "evidence_spans": [],
        "reason": "依來源內容評估。",
        "abstain_reason": None,
    }


def new_event(
    e: str, title: str, day: str, location: str, category: str
) -> dict[str, list[Script]]:
    """S01–S03：新事件共用的前三步。"""
    return {
        "relevance-filter": [relevance(e)],
        "event-extractor": [extraction(e, title, day, location)],
        "event-classifier": [classification(e, category)],
    }


# ---------- 資料庫 ----------


def e2e_url() -> URL:
    url = Settings().database_url
    if not (url.database or "").endswith("_e2e"):
        raise SystemExit(f"拒絕執行：NCU_DB_NAME 必須以 _e2e 結尾（目前為 {url.database}）")
    return url


async def ensure_database(url: URL) -> None:
    conn = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database="postgres",
        timeout=5,
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database):
            await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


async def truncate_all(session: AsyncSession) -> None:
    """清空所有資料表；audit_logs 的 append-only trigger 只在這個交易內暫停。"""
    tables = (
        await session.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
        )
    ).scalars()
    names = ", ".join(f'"{t}"' for t in tables)
    await session.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_truncate"))
    await session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    await session.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_no_truncate"))
    await session.commit()


def normalized(source: str, external_id: str, body: str, published: datetime) -> NormalizedItem:
    return NormalizedItem(
        source_code=source,
        external_id=external_id,
        url=f"https://example.test/{source}/{external_id}",
        title=None,
        text=body,
        language="zh",
        content_hash=content_hash(body),
        published_at=published,
        fetched_at=FETCHED,
    )


class Seeder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.skills = discover_skills()
        self.config = load_llm_config()
        self.policy = load_policy()

    async def ingest(self, source: str, external_id: str, body: str, day: str) -> str:
        """以真實的冪等寫入存一篇文章，回傳 evidence_id（文章 id 的十六進位）。"""
        published = datetime.fromisoformat(f"{day}T01:00:00+00:00")
        await store_items(
            self.session, INGESTION, source, [normalized(source, external_id, body, published)]
        )
        await self.session.commit()
        item_id = await self.session.scalar(
            select(SourceItem.id)
            .join(Source, Source.id == SourceItem.source_id)
            .where(Source.code == source, SourceItem.external_id == external_id)
        )
        assert item_id is not None
        return item_id.hex

    async def analyze(self, e: str, scripts: Mapping[str, Sequence[Script]]) -> Outcome:
        deps = PipelineDeps(
            provider=SkillScript(scripts),
            embedder=FakeEmbeddingProvider(),
            config=self.config,
            skills=self.skills,
            policy=self.policy,
        )
        result = await analyze_item(self.session, uuid.UUID(e), deps, trace_id=f"e2e-{e[:8]}")
        return result.outcome

    async def scalar_int(self, stmt: Any) -> int:
        return int(await self.session.scalar(stmt) or 0)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"種子資料不變量不成立：{message}")


async def seed(session: AsyncSession) -> None:
    for code, name in SOURCES.items():
        session.add(Source(code=code, name=name, type="rpage", access_policy="approved"))
    await session.commit()
    s = Seeder(session)

    # A01 官方施工公告 → 已查證、設施類、來源可開啟
    e = await s.ingest(
        "ncu-announcements",
        "a01",
        "總務處公告：工程五館外牆整修施工\n\n11/2 起工程五館外牆整修，施工期間東側出入口封閉。",
        "2026-11-02",
    )
    outcome = await s.analyze(
        e,
        {
            **new_event(e, A01_TITLE, "2026-11-02", "工程五館", "facility"),
            "evidence-verifier": [verification({e: ("supports", "official")}, "verified")],
            "risk-assessor": [risk(e, LOW)],
        },
    )
    check(outcome is Outcome.CREATED, f"A01 應建立事件，實際 {outcome}")

    # A02 社群單篇失竊指控 → 未查證；高風險通知只用一般安全提醒，不呈現指控
    e = await s.ingest(
        "facebook",
        "a02",
        "宿舍交誼廳的筆電昨晚不見了，我覺得一定是同層的室友偷的，大家小心他！",
        "2026-09-01",
    )
    await s.analyze(
        e,
        {
            **new_event(e, A02_TITLE, "2026-09-01", "女十四舍", "security"),
            "evidence-verifier": [verification({e: ("supports", "hearsay")}, "unverified")],
            "risk-assessor": [risk(e, HIGH)],
        },
    )

    # A03 三則相同失竊新聞轉載 → 事件數 1、來源證據數 3
    news = [
        await s.ingest(
            "news",
            f"a03-{i}",
            f"（轉載 {i}）中央大學總圖書館自習室 9/20 晚間發生筆電失竊，校方提醒勿將財物留置座位。",
            "2026-09-20",
        )
        for i in (1, 2, 3)
    ]
    first = news[0]
    await s.analyze(
        first,
        {
            **new_event(first, A03_TITLE, "2026-09-20", "總圖書館", "security"),
            "evidence-verifier": [verification({first: ("supports", "first_hand")}, "unverified")],
            "risk-assessor": [risk(first, MEDIUM)],
        },
    )
    for i, e in enumerate(news[1:], start=1):
        known = news[:i]
        stances = {x: ("supports", "first_hand") for x in [*known, e]}
        outcome = await s.analyze(
            e,
            {
                **new_event(e, A03_TITLE, "2026-09-20", "總圖書館", "security"),
                "event-linker": [linking(e, known, "same_event")],
                "evidence-verifier": [verification(stances, "verified")],
                "risk-assessor": [risk(e, MEDIUM)],
            },
        )
        check(outcome is Outcome.LINKED, f"A03 第 {i + 1} 則應併入同一事件，實際 {outcome}")
    a03 = await s.scalar_int(
        select(func.count()).select_from(Event).where(Event.title == A03_TITLE)
    )
    check(a03 == 1, f"A03 事件數應為 1，實際 {a03}")
    evidence = await s.scalar_int(
        select(func.count())
        .select_from(EventEvidence)
        .join(Event, Event.id == EventEvidence.event_id)
        .where(Event.title == A03_TITLE)
    )
    check(evidence == 3, f"A03 來源證據數應為 3，實際 {evidence}")

    # A04 兩起不同日失竊 → 兩個事件（related_event 關聯的呈現留到 P4-T01／P4-T02）
    first = await s.ingest("news", "a04-1", "10/10 宵夜街一輛機車遭竊。", "2026-10-10")
    await s.analyze(
        first,
        {
            **new_event(first, A04_TITLES[0], "2026-10-10", "宵夜街", "security"),
            "evidence-verifier": [verification({first: ("supports", "first_hand")}, "unverified")],
            "risk-assessor": [risk(first, MEDIUM)],
        },
    )
    e = await s.ingest("news", "a04-2", "10/12 後門停車場又有一輛機車遭竊。", "2026-10-12")
    outcome = await s.analyze(
        e,
        {
            **new_event(e, A04_TITLES[1], "2026-10-12", "後門停車場", "security"),
            "event-linker": [linking(e, [first], "related_event")],
            "evidence-verifier": [verification({e: ("supports", "first_hand")}, "unverified")],
            "risk-assessor": [risk(e, MEDIUM)],
        },
    )
    check(outcome is Outcome.CREATED, f"A04 第二起應建立新事件，實際 {outcome}")

    # A05 匿名惡意誣指個人 → 通知不呈現指控與個資，管理端標記疑似指控
    e = await s.ingest(
        "facebook",
        "a05",
        "匿名：資工系王小明老師性騷擾學生，手機 0912345678，大家快去肉搜他！",
        "2026-10-30",
    )
    await s.analyze(
        e,
        {
            **new_event(e, A05_TITLE, "2026-10-30", "工程三館", "security"),
            "evidence-verifier": [verification({e: ("supports", "rumor")}, "unverified")],
            "risk-assessor": [risk(e, HIGH)],
        },
    )

    # A06 官方宣布事件澄清 → 查證狀態更新為不實，已發出的通知自動撤回
    rumor = await s.ingest(
        "facebook", "a06-1", "工程四館好像失火了，一直有煙冒出來！", "2026-10-15"
    )
    await s.analyze(
        rumor,
        {
            **new_event(rumor, A06_TITLE, "2026-10-15", "工程四館", "fire"),
            "evidence-verifier": [verification({rumor: ("supports", "hearsay")}, "unverified")],
            "risk-assessor": [risk(rumor, HIGH)],
        },
    )
    published = await s.scalar_int(
        select(func.count())
        .select_from(Alert)
        .join(Event, Event.id == Alert.event_id)
        .where(Event.title == A06_TITLE, Alert.state == "published")
    )
    check(published == 1, f"A06 澄清前應已通知 1 次，實際 {published}")
    official = await s.ingest(
        "ncu-announcements",
        "a06-2",
        "環安中心澄清：今日工程四館為消防演練施放煙霧，並無火災發生。",
        "2026-10-15",
    )
    outcome = await s.analyze(
        official,
        {
            **new_event(official, A06_TITLE, "2026-10-15", "工程四館", "fire"),
            "event-linker": [linking(official, [rumor], "same_event")],
            "evidence-verifier": [
                verification(
                    {rumor: ("supports", "hearsay"), official: ("refutes", "official")}, "false"
                )
            ],
            "risk-assessor": [risk(official, LOW)],
        },
    )
    check(outcome is Outcome.LINKED, f"A06 澄清應併入原事件，實際 {outcome}")

    # A07 重送相同任務 → 原文、事件、通知都不重複
    body = "中大路與校門口路口發生多車追撞，目前封路，請改道。"
    e = await s.ingest("news", "a07", body, "2026-10-20")
    scripts: dict[str, list[Script]] = {
        **new_event(e, A07_TITLE, "2026-10-20", "校門口", "traffic"),
        "evidence-verifier": [verification({e: ("supports", "first_hand")}, "unverified")],
        "risk-assessor": [risk(e, HIGH)],
    }
    check(await s.analyze(e, scripts) is Outcome.CREATED, "A07 第一次應建立事件")
    again = await s.ingest("news", "a07", body, "2026-10-20")
    check(again == e, "A07 重送的原文應寫入同一篇文章")
    await s.analyze(e, scripts)
    a07_event = select(Event.id).where(Event.title == A07_TITLE).scalar_subquery()
    counts = {
        "versions": await s.scalar_int(
            select(func.count())
            .select_from(SourceItemVersion)
            .where(SourceItemVersion.item_id == uuid.UUID(e))
        ),
        "events": await s.scalar_int(
            select(func.count()).select_from(Event).where(Event.title == A07_TITLE)
        ),
        "alerts": await s.scalar_int(
            select(func.count()).select_from(Alert).where(Alert.event_id == a07_event)
        ),
        "deliveries": await s.scalar_int(
            select(func.count())
            .select_from(AlertDelivery)
            .join(Alert, Alert.id == AlertDelivery.alert_id)
            .where(Alert.event_id == a07_event)
        ),
    }
    check(counts == dict.fromkeys(counts, 1), f"A07 重送後各應為 1，實際 {counts}")

    # A08 模型輸出無 evidence_ids → 業務驗證拒絕，即使立場是官方支持也不得標為已查證
    e = await s.ingest(
        "facebook", "a08", "學生餐廳午餐後好幾個人肚子痛，是不是食物中毒？", "2026-10-20"
    )
    await s.analyze(
        e,
        {
            **new_event(e, A08_TITLE, "2026-10-20", "學生餐廳", "health"),
            "evidence-verifier": [
                verification({e: ("supports", "official")}, "verified", evidence_ids=False)
            ],
            "risk-assessor": [risk(e, LOW)],
        },
    )
    status = await session.scalar(select(Event.status).where(Event.title == A08_TITLE))
    check(status != "verified", "A08 不得標為已查證")


async def main() -> None:
    username = os.environ.get("E2E_USERNAME", "owner")
    password = os.environ.get("E2E_PASSWORD")
    if not password:
        raise SystemExit("請設定 E2E_PASSWORD（只供 e2e 測試資料庫使用）")
    url = e2e_url()
    await ensure_database(url)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = url
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await truncate_all(session)
            await create_or_reset_admin(session, username, password)
            await session.commit()
            await seed(session)
    finally:
        await engine.dispose()
    print(f"附錄 A 種子資料完成（{url.database}）")


if __name__ == "__main__":
    asyncio.run(main())
