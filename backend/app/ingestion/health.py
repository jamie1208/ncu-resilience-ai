"""來源健康狀態與連續失敗自動停用（P1-T08；規格 F01「來源健康狀態」、§16 Adapter 健康檢測）。

狀態判斷（依序，先符合者為準）：
- `disabled`：未核准或已停用。
- `unknown`：還沒有任何已結束的執行。
- `failing`：最近一次結束的執行是 failed 或 abandoned。
- `stale`：最近一次成功距今超過 3 個擷取間隔（例如 worker 沒在跑）。
- `degraded`：7 天成功率低於 98%（閘門 G1-2 門檻），或有未處理的死信。
- `healthy`：以上皆否。

成功率 = 窗口內 succeeded ÷（succeeded + failed + abandoned）；skipped 與執行中不計。

自動停用（來源盤點「共通擷取原則」第 2 條，使用者 2026-10-17 決定）：最近 5 次「有結果」的執行
（succeeded 或 failed）全部是 failed 時，把來源設為停用並寫稽核紀錄。這是確定性的系統規則，
由擷取服務在派工前執行；修好後由管理者手動重新啟用。告警通知待 P3 的通知服務。

用法（repo 根目錄）：`make health`，或在後端容器內執行 `python -m app.ingestion.health`。
"""

from __future__ import annotations

import asyncio
import unicodedata
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import Principal, Role
from app.ingestion.types import AccessPolicy, RunStatus
from app.models.source import IngestionDeadLetter, IngestionRun, Source
from app.services.audit import record_audit

INGESTION_PRINCIPAL = Principal(
    kind="service", subject="ingestion", roles=frozenset({Role.SERVICE_INGESTION})
)
"""擷取服務帳號：寫入擷取資料、死信與自動停用都以它記錄稽核。"""

WINDOW = timedelta(days=7)
SUCCESS_RATE_THRESHOLD = 0.98
STALE_AFTER_INTERVALS = 3
DEFAULT_POLL_INTERVAL = 3600
AUTO_DISABLE_AFTER = 5

FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.ABANDONED)
UNSUCCESSFUL = (RunStatus.FAILED, RunStatus.ABANDONED)

HealthStatus = Literal["healthy", "degraded", "failing", "stale", "disabled", "unknown"]


class SourceHealth(BaseModel):
    """一個來源的健康狀態。時間皆為 aware UTC。"""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    enabled: bool
    access_policy: str
    poll_interval: int | None
    status: HealthStatus
    last_run_status: str | None
    last_run_at: AwareDatetime | None
    last_success_at: AwareDatetime | None
    consecutive_failures: int
    runs_in_window: int
    success_rate: float | None
    open_dead_letters: int


def _is_active(source: Source) -> bool:
    try:
        return AccessPolicy(source.access_policy) is AccessPolicy.APPROVED and source.enabled
    except ValueError:
        return False


async def compute_health(
    session: AsyncSession, *, now: datetime, window: timedelta = WINDOW
) -> list[SourceHealth]:
    """計算所有來源的健康狀態，依來源代碼排序。"""
    sources = (await session.execute(select(Source).order_by(Source.code))).scalars().all()
    return [await _source_health(session, s, now=now, window=window) for s in sources]


async def _source_health(
    session: AsyncSession, source: Source, *, now: datetime, window: timedelta
) -> SourceHealth:
    rows = (
        await session.execute(
            select(IngestionRun.status, func.count())
            .where(
                IngestionRun.source_id == source.id,
                IngestionRun.started_at >= now - window,
                IngestionRun.status.in_(FINISHED),
            )
            .group_by(IngestionRun.status)
        )
    ).all()
    by_status: dict[str, int] = {status: count for status, count in rows}
    finished = sum(by_status.values())
    succeeded = by_status.get(RunStatus.SUCCEEDED, 0)
    success_rate = round(succeeded / finished, 4) if finished else None

    last_run = (
        await session.execute(
            select(IngestionRun)
            .where(IngestionRun.source_id == source.id)
            .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    last_success_at = (
        await session.execute(
            select(func.max(IngestionRun.finished_at)).where(
                IngestionRun.source_id == source.id,
                IngestionRun.status == RunStatus.SUCCEEDED,
            )
        )
    ).scalar_one()
    recent = (
        (
            await session.execute(
                select(IngestionRun.status)
                .where(IngestionRun.source_id == source.id, IngestionRun.status.in_(FINISHED))
                .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    consecutive = 0
    for run_status in recent:
        if run_status not in UNSUCCESSFUL:
            break
        consecutive += 1
    open_dead_letters = (
        await session.execute(
            select(func.count()).where(
                IngestionDeadLetter.source_id == source.id,
                IngestionDeadLetter.resolved_at.is_(None),
            )
        )
    ).scalar_one()

    interval = timedelta(seconds=source.poll_interval or DEFAULT_POLL_INTERVAL)
    status: HealthStatus
    if not _is_active(source):
        status = "disabled"
    elif not recent:
        status = "unknown"
    elif recent[0] in UNSUCCESSFUL:
        status = "failing"
    elif last_success_at is None or now - last_success_at > interval * STALE_AFTER_INTERVALS:
        status = "stale"
    elif (success_rate is not None and success_rate < SUCCESS_RATE_THRESHOLD) or open_dead_letters:
        status = "degraded"
    else:
        status = "healthy"

    return SourceHealth(
        code=source.code,
        name=source.name,
        enabled=source.enabled,
        access_policy=source.access_policy,
        poll_interval=source.poll_interval,
        status=status,
        last_run_status=last_run.status if last_run else None,
        last_run_at=last_run.started_at if last_run else None,
        last_success_at=last_success_at,
        consecutive_failures=consecutive,
        runs_in_window=finished,
        success_rate=success_rate,
        open_dead_letters=open_dead_letters,
    )


_RETRYABLE_ERROR_TYPES = frozenset({"SourceUnavailableError"})
"""可重試的錯誤類型（`AdapterError.retryable` 為 True：連線失敗、逾時、5xx、408／425／429）。"""
_SCAN_LIMIT = 200


def _is_retryable_failure(run: IngestionRun) -> bool:
    return run.status == RunStatus.FAILED and run.counts.get("error_type") in _RETRYABLE_ERROR_TYPES


async def auto_disable_failing(
    session: AsyncSession, *, threshold: int = AUTO_DISABLE_AFTER
) -> list[str]:
    """停用最近 `threshold` 次有結果的執行全部失敗的來源；回傳停用的來源代碼。

    可重試的失敗（連線失敗、逾時、5xx；P1-T10 使用者決定）在判斷中略過，既不計入也不中斷，
    所以網路中斷不會讓來源被停用；它們仍然寫入死信、計入失敗率。
    只 flush 不 commit，由呼叫端（派工）的交易一併提交。
    """
    disabled: list[str] = []
    sources = (
        (await session.execute(select(Source).where(Source.enabled).order_by(Source.code)))
        .scalars()
        .all()
    )
    for source in sources:
        runs = (
            (
                await session.execute(
                    select(IngestionRun)
                    .where(
                        IngestionRun.source_id == source.id,
                        IngestionRun.status.in_((RunStatus.SUCCEEDED, RunStatus.FAILED)),
                    )
                    .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
                    .limit(_SCAN_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        statuses = [r.status for r in runs if not _is_retryable_failure(r)][:threshold]
        if len(statuses) < threshold or any(s != RunStatus.FAILED for s in statuses):
            continue
        source.enabled = False
        await session.flush()
        await record_audit(
            session,
            INGESTION_PRINCIPAL,
            "sources.auto_disable",
            target=f"sources/{source.code}",
            metadata={"consecutive_failures": threshold},
        )
        disabled.append(source.code)
    return disabled


# --- CLI -------------------------------------------------------------------------------

_TAIPEI = timezone(timedelta(hours=8))


def _fmt_time(value: datetime | None) -> str:
    return value.astimezone(_TAIPEI).strftime("%m-%d %H:%M") if value else "—"


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _cell(text: str, width: int, *, right: bool = False) -> str:
    pad = " " * max(width - _width(text), 0)
    return pad + text if right else text + pad


def format_report(report: list[SourceHealth]) -> str:
    """把健康狀態排成終端機表格（時間以台北時間顯示；中文以兩格寬對齊）。"""
    columns = [("來源", 20, False), ("狀態", 10, False), ("7天成功率", 10, True),
               ("次數", 6, True), ("連續失敗", 10, True), ("死信", 6, True),
               ("最近執行", 13, True), ("最近成功", 13, True)]  # fmt: skip
    rows = [[name for name, _, _ in columns]]
    for h in report:
        rate = f"{h.success_rate:.1%}" if h.success_rate is not None else "—"
        rows.append(
            [
                h.code,
                h.status,
                rate,
                str(h.runs_in_window),
                str(h.consecutive_failures),
                str(h.open_dead_letters),
                _fmt_time(h.last_run_at),
                _fmt_time(h.last_success_at),
            ]
        )
    return "\n".join(
        "".join(_cell(v, w, right=r) for v, (_, w, r) in zip(row, columns, strict=True)).rstrip()
        for row in rows
    )


async def _main() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            report = await compute_health(session, now=datetime.now(UTC))
    finally:
        await engine.dispose()
    print(format_report(report))


if __name__ == "__main__":
    asyncio.run(_main())
