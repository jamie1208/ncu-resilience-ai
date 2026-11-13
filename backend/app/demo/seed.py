"""示範模式的種子（P9-T01，ADR-0022）：以真實流程建立虛構資料。

只在 `NCU_ENVIRONMENT=demo` 且資料庫名稱以 `_demo` 結尾時執行（`check_target`），永遠不會碰到
正式資料庫。每次執行先清空示範資料庫，再依序：

1. 建立示範管理員與所有來源；示範來源一律停用，只有內部的匿名回報收件匣保持啟用，
   排程不會擷取任何真實來源，示範站當場送出的投稿則照常進入管線（用 .env 的真實模型）；
2. 以真實的冪等寫入（`store_items`）與分析管線（`analyze_item`，與正式 worker 相同的設定：
   不跑 S01、跑 S11 系列歸組與 S15 快報文案）處理虛構文章，模型以 `DemoProvider` 代替；
3. 系列分析（S12）、人工確認原因、系列追蹤、事件改善措施、效益評估（S14）、
   What-if（S10）、趨勢解讀（S09）與公開同步，全部走正式的服務函式；
4. 把事件建立時間、改善措施的狀態時間與擷取紀錄回推到虛構日期，讓 CRI 四構面都有樣本。

用法（示範容器內）：`python -m app.demo.seed`；本機：`make demo-seed`。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.llm.config import load_llm_config
from app.ai.llm.fake_provider import FakeEmbeddingProvider
from app.ai.runner.loader import discover_skills
from app.ai.runner.run import SkillRunResult, SqlRunStore, run_skill
from app.analytics.forecast import Forecaster, run_forecast
from app.api.v1.public.reports import new_receipt
from app.core.config import Settings
from app.core.security import Principal, Role
from app.demo.dataset import (
    CONFIRMED_CAUSE,
    DATASET,
    IMPACT_SELECTION,
    LIBRARY,
    SERIES_ACTIONS,
    ActionSpec,
    Article,
    Dataset,
)
from app.demo.responder import DemoProvider
from app.ingestion.adapters.anonymous_report import EXTERNAL_ID_PREFIX
from app.ingestion.normalize import NormalizedItem, content_hash
from app.ingestion.source_settings import load_source_settings
from app.ingestion.store import store_items
from app.ingestion.types import AccessPolicy, RunStatus
from app.models import (
    Alert,
    AnonymousReport,
    Event,
    EventSeriesMember,
    IngestionRun,
    ReportReceipt,
    ResponseAction,
    ResponseActionTransition,
    Source,
    SourceItem,
)
from app.pipeline.analyze_item import PIPELINE_PRINCIPAL, Outcome, PipelineDeps, analyze_item
from app.publishing.sync import sync_all
from app.resilience.actions import (
    ActionState,
    create_action,
    create_series_action,
    transition_action,
)
from app.risk.engine import load_policy
from app.series.analysis import (
    CauseDecision,
    analyze_series,
    decide_series_cause,
    latest_analysis,
)
from app.services.auth import create_or_reset_admin
from app.simulations.impact import Evaluator, evaluate_impact
from app.simulations.service import Explainer, MeasureSpec, ScenarioSpec, create_scenario

TAIPEI = ZoneInfo("Asia/Taipei")
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEMO_SUFFIX = "_demo"
ANONYMOUS_SOURCE = "anonymous-report"
HISTORY_DAYS = 126
"""示範來源的「新增日」與擷取紀錄從種子日往前幾天開始（18 週，趨勢解讀的統計期間）。"""
INGESTION = Principal(
    kind="service", subject="demo-seed", roles=frozenset({Role.SERVICE_INGESTION})
)
OWNER = Principal(kind="admin", subject="demo-owner", roles=frozenset({Role.ADMIN}))
PROOF = "示範資料：已完成並留存紀錄。"


class DemoTargetError(RuntimeError):
    """目標不是示範環境或示範資料庫。"""


def check_target(settings: Settings) -> URL:
    """確認目前是示範環境的示範資料庫，回傳連線 URL；否則拋出 `DemoTargetError`。"""
    if settings.environment != "demo":
        raise DemoTargetError(
            f"拒絕執行：NCU_ENVIRONMENT 必須是 demo（目前為 {settings.environment}）"
        )
    try:
        url = settings.database_url
    except ValueError as exc:
        raise DemoTargetError(f"拒絕執行：{exc}") from None
    if not (url.database or "").endswith(DEMO_SUFFIX):
        raise DemoTargetError(
            f"拒絕執行：資料庫名稱必須以 {DEMO_SUFFIX} 結尾（目前為 {url.database}）"
        )
    return url


# ---------- 時間 ----------


def _at(today: date, ago: float, hour: int = 10) -> datetime:
    """種子日往前 `ago` 天、台北時間 `hour` 點，轉成 aware UTC。"""
    moment = datetime.combine(today - timedelta(days=int(ago)), time(hour), TAIPEI)
    return (moment + timedelta(days=ago - int(ago))).astimezone(UTC)


# ---------- 資料庫 ----------


async def _truncate_all(session: AsyncSession) -> None:
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


# ---------- 種子 ----------


class _Seeder:
    def __init__(self, session: AsyncSession, dataset: Dataset, today: date) -> None:
        self.session = session
        self.dataset = dataset
        self.today = today
        self.provider = DemoProvider(dataset, today)
        self.config = load_llm_config()
        self.skills = discover_skills()
        self.deps = PipelineDeps(
            provider=self.provider,
            embedder=FakeEmbeddingProvider(),
            config=self.config,
            skills=self.skills,
            policy=load_policy(),
            check_relevance=False,
            group_series=True,
            write_bulletin=True,
        )
        self.event_ids: dict[str, uuid.UUID] = {}
        self.now = _at(today, 0, 12)

    def call(self, skill: str) -> Any:
        async def run(payload: Mapping[str, Any]) -> SkillRunResult:
            result = await run_skill(
                self.skills[skill],
                payload,
                provider=self.provider,
                config=self.config,
                store=SqlRunStore(self.session),
                trace_id="demo-seed",
            )
            await self.session.commit()
            return result

        return run

    # ----- 來源與擷取紀錄 -----

    async def sources(self) -> None:
        added = _at(self.today, HISTORY_DAYS, 0)
        settings = load_source_settings()
        for code, s in settings.items():
            self.session.add(
                Source(
                    code=code,
                    name=s.name or code,
                    type=s.type,
                    access_policy=AccessPolicy.APPROVED.value,
                    enabled=True,  # 寫入文章需要啟用；播種完成後由 `disable_sources` 停用
                    poll_interval=s.poll_interval,
                    created_at=added,
                )
            )
        await self.session.flush()
        sources = (await self.session.scalars(select(Source).order_by(Source.code))).all()
        for i, source in enumerate(sources):
            for ago in range(HISTORY_DAYS, 0, -1):
                started = _at(self.today, ago, 8) + timedelta(minutes=i)
                if (ago + i) % 47 == 0:  # 偶爾一次失敗，當天稍後重試成功
                    self.session.add(
                        IngestionRun(
                            source_id=source.id,
                            started_at=started,
                            finished_at=started + timedelta(seconds=20),
                            status=RunStatus.FAILED.value,
                            counts={"attempts": 1, "error": "TimeoutError"},
                        )
                    )
                    started += timedelta(hours=1)
                self.session.add(
                    IngestionRun(
                        source_id=source.id,
                        started_at=started,
                        finished_at=started + timedelta(seconds=12),
                        status=RunStatus.SUCCEEDED.value,
                        counts={"fetched": 0, "created": 0, "updated": 0, "unchanged": 0},
                    )
                )
        await self.session.commit()

    # ----- 文章與管線 -----

    async def _store(self, article: Article, body: str, external_id: str, url: str) -> uuid.UUID:
        published = _at(self.today, article.ago) + timedelta(minutes=len(self.provider.articles))
        item = NormalizedItem(
            source_code=article.source,
            external_id=external_id,
            url=url,
            title=None,
            text=body,
            language="zh",
            content_hash=content_hash(body),
            published_at=published,
            fetched_at=published + timedelta(minutes=30),
        )
        await store_items(self.session, INGESTION, article.source, [item])
        await self.session.commit()
        item_id = await self.session.scalar(
            select(SourceItem.id)
            .join(Source, Source.id == SourceItem.source_id)
            .where(Source.code == article.source, SourceItem.external_id == external_id)
        )
        if item_id is None:
            raise RuntimeError("示範文章寫入失敗")
        self.provider.register(item_id.hex, article)
        return item_id

    async def _analyze(self, article: Article, item_id: uuid.UUID) -> None:
        result = await analyze_item(
            self.session, item_id, self.deps, trace_id=f"demo-{article.key}"
        )
        if result.outcome is Outcome.CREATED and result.event_id is not None and article.event:
            self.event_ids[article.event] = result.event_id
            created = _at(self.today, article.ago) + timedelta(hours=1)
            await self.session.execute(
                update(Event).where(Event.id == result.event_id).values(created_at=created)
            )
            await self.session.commit()

    async def articles(self) -> None:
        anonymous = {a.article.key: a for a in self.dataset.anonymous}
        queue = sorted(
            [*self.dataset.articles, *(a.article for a in self.dataset.anonymous)],
            key=lambda a: -a.ago,
        )
        for article in queue:
            spec = anonymous.get(article.key)
            if spec is None:
                item_id = await self._store(
                    article,
                    article.text,
                    f"demo-{article.key}",
                    f"https://demo.invalid/{article.source}/{article.key}",
                )
            else:
                item_id = await self._anonymous(article, spec.content, spec.location)
            await self._analyze(article, item_id)

    async def _anonymous(self, article: Article, content: str, location: str | None) -> uuid.UUID:
        """與公開回報表單相同地寫入收件匣，再照匿名回報 Adapter 的格式拉進管線。"""
        submitted = _at(self.today, article.ago, 22)
        row = AnonymousReport(content=content, location=location, submitted_at=submitted)
        self.session.add(row)
        await self.session.flush()
        _, digest = new_receipt()
        self.session.add(ReportReceipt(report_id=row.id, code_hash=digest, is_supplement=False))
        source = await self.session.scalar(select(Source).where(Source.code == ANONYMOUS_SOURCE))
        if source is None:
            raise RuntimeError("缺少匿名回報來源")
        source.cursor = str(row.id)
        await self.session.commit()
        body = content if location is None else f"地點：{location}\n\n{content}"
        return await self._store(
            article,
            body,
            f"{EXTERNAL_ID_PREFIX}{row.id}",
            f"https://demo.invalid/report?id={row.id}",
        )

    # ----- 系列 -----

    async def series_analyses(self) -> None:
        rows = (
            await self.session.execute(
                select(EventSeriesMember.series_id).group_by(EventSeriesMember.series_id)
            )
        ).scalars()
        for series_id in list(rows):
            await analyze_series(
                self.session,
                series_id,
                run=self.call("series-analyst"),
                principal=PIPELINE_PRINCIPAL,
                now=self.now,
                trace_id="demo-seed",
            )
            await self.session.commit()

    async def _series_of(self, key: str) -> uuid.UUID:
        first = next(e.key for e in self.dataset.events if e.series == key)
        series_id = await self.session.scalar(
            select(EventSeriesMember.series_id).where(
                EventSeriesMember.event_id == self.event_ids[first]
            )
        )
        if series_id is None:
            raise RuntimeError(f"系列 {key} 不存在")
        return series_id

    async def series_follow_up(self) -> None:
        key, cause_id, basis = CONFIRMED_CAUSE
        series_id = await self._series_of(key)
        analysis = await latest_analysis(self.session, series_id)
        if analysis is None:
            raise RuntimeError(f"系列 {key} 沒有分析")
        await decide_series_cause(
            self.session, OWNER, series_id, analysis.id, cause_id, CauseDecision.CONFIRMED,
            basis=basis,
        )  # fmt: skip
        await self.session.commit()

        for spec in SERIES_ACTIONS:
            series_id = await self._series_of(spec.series)
            from_analysis = spec.measure_index is not None
            action = await create_series_action(
                self.session,
                OWNER,
                series_id,
                title=spec.title,
                analysis_id=analysis.id if from_analysis else None,
                measure_index=spec.measure_index,
                owner=spec.owner,
            )
            await self._steps(action.id, spec.created_ago, spec.steps)

        key, measures = IMPACT_SELECTION
        await evaluate_impact(
            self.session,
            OWNER,
            await self._series_of(key),
            list(measures),
            Evaluator(self.skills["impact-evaluator"], self.provider, self.config),
            as_of=self.today,
            trace_id="demo-seed",
        )
        await self.session.commit()

    # ----- 改善措施（CRI） -----

    async def _stamp(self, action_id: uuid.UUID, seen: set[uuid.UUID], moment: datetime) -> None:
        """把這個措施新產生的狀態紀錄改到虛構時間（同一交易內的 now() 都相同，不能事後排序）。"""
        await self.session.flush()
        rows = (
            await self.session.scalars(
                select(ResponseActionTransition).where(
                    ResponseActionTransition.action_id == action_id,
                    ResponseActionTransition.id.not_in(seen or {uuid.UUID(int=0)}),
                )
            )
        ).all()
        for row in rows:
            row.created_at = moment
            seen.add(row.id)

    async def _steps(
        self, action_id: uuid.UUID, created_ago: int, steps: Sequence[tuple[str, int]]
    ) -> None:
        """依序轉移狀態，並把建立與每一步的時間回推到虛構日期。"""
        seen: set[uuid.UUID] = set()
        created = last = _at(self.today, created_ago, 9)
        await self._stamp(action_id, seen, created)
        for state, ago in steps:
            proof = PROOF if state in ("recovered", "improved") else None
            await transition_action(self.session, OWNER, action_id, ActionState(state), proof=proof)
            last = _at(self.today, ago, 15)
            await self._stamp(action_id, seen, last)
        await self.session.execute(
            update(ResponseAction)
            .where(ResponseAction.id == action_id)
            .values(created_at=created, updated_at=last)
        )
        await self.session.commit()

    async def _action(self, spec: ActionSpec) -> None:
        action = await create_action(
            self.session, OWNER, self.event_ids[spec.event], title=spec.title, owner=spec.owner
        )
        await self._steps(action.id, spec.created_ago, spec.steps)

    async def actions(self) -> None:
        for spec in self.dataset.actions:
            await self._action(spec)

    # ----- 模擬與趨勢 -----

    async def scenario(self) -> None:
        spec = ScenarioSpec(
            baseline=None,
            window_days=90,
            measures=(
                MeasureSpec(
                    "總圖前車棚加裝監視器", 0.3, 0.5, 80000, "宿舍區車棚加裝後，件數明顯下降。"
                ),
                MeasureSpec("增設固定式停車架", 0.1, 0.3, 50000, "車架可以連車身一起上鎖。"),
            ),
            seed=20261113,
            event_type="security",
            location=LIBRARY,
        )
        await create_scenario(
            self.session,
            OWNER,
            spec,
            Explainer(self.skills["scenario-simulator"], self.provider, self.config),
            as_of=self.today,
            trace_id="demo-seed",
        )
        await self.session.commit()

    async def forecast(self) -> None:
        await run_forecast(
            self.session,
            OWNER,
            Forecaster(self.skills["trend-forecaster"], self.provider, self.config),
            freq="week",
            as_of=self.today - timedelta(days=1),
            trace_id="demo-seed",
        )
        await self.session.commit()

    # ----- 收尾 -----

    async def disable_sources(self) -> None:
        """停用所有示範來源，只留匿名回報收件匣：排程不會擷取任何真實來源。"""
        await self.session.execute(
            update(Source).where(Source.code != ANONYMOUS_SOURCE).values(enabled=False)
        )
        await self.session.commit()

    async def align_alerts(self) -> None:
        """通知的時間對齊事件建立時間，通知中心才會依虛構日期排序。"""
        rows = (await self.session.execute(select(Alert.id, Event.created_at).join(Event))).all()
        for alert_id, created in rows:
            await self.session.execute(
                update(Alert).where(Alert.id == alert_id).values(created_at=created)
            )
        await self.session.commit()


async def seed_demo(session: AsyncSession, *, today: date | None = None) -> None:
    """清空後建立整套示範資料（不檢查目標資料庫，呼叫端負責）。"""
    day = today or datetime.now(TAIPEI).date()
    await _truncate_all(session)
    s = _Seeder(session, DATASET, day)
    await s.sources()
    await s.articles()
    await s.series_analyses()
    await s.series_follow_up()
    await s.actions()
    await s.scenario()
    await s.forecast()
    await s.align_alerts()
    await s.disable_sources()
    await sync_all(session)
    await session.commit()


async def _ensure_database(url: URL) -> None:
    """示範資料庫不存在時建立（連到同一台伺服器的 postgres 資料庫）。"""
    engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        await engine.dispose()


async def main() -> None:
    """命令列進入點：檢查目標、執行 migration、建立示範管理員並播種。"""
    url = check_target(Settings())
    username = os.environ.get("DEMO_ADMIN_USERNAME", "demo")
    password = os.environ.get("DEMO_ADMIN_PASSWORD")
    if not password:
        raise SystemExit("請在 .env 設定 DEMO_ADMIN_PASSWORD（只供示範資料庫使用）")
    await _ensure_database(url)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = url
    await asyncio.to_thread(command.upgrade, cfg, "head")
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await seed_demo(session)
            await create_or_reset_admin(session, username, password)
            await session.commit()
    finally:
        await engine.dispose()
    print(f"示範資料完成（{url.database}）；管理員帳號：{username}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except DemoTargetError as exc:
        raise SystemExit(str(exc)) from None
