"""個人通知的遞送（P3-T08）。

通知只送給使用者本人（ADR-0008）。目前只有管理介面通知中心（`inbox`）：通知中心直接讀取
alerts 表，所以遞送只需記錄一筆 alert_deliveries。Email 等外部管道之後實作 `Channel` 介面即可。

冪等：alert_deliveries 對 (alert_id, channel, destination, version) 有唯一約束，遞送前先以
`INSERT ... ON CONFLICT DO NOTHING` 佔位，佔位成功才呼叫管道送出；同一版本重送一律略過。

失敗重試（P8-T07，v1.0 A10）：送出失敗記為 `failed` 並排定 `next_attempt_at`；beat 每分鐘執行
`retry_failed_deliveries`，對到期的列最多重試 `MAX_RETRIES` 次，間隔依 `RETRY_DELAYS` 遞增，
第 3 次重試仍失敗就清除下次時間、停止。重試沿用同一列（唯一約束不變，不新增遞送），並以
`FOR UPDATE SKIP LOCKED` 鎖住該列，兩個 worker 同時執行也只送一次；成功後改為 `delivered`、
不再重送。通知已撤回、過期或已改成新版本時，舊版本停止重試（新版本有自己的遞送列）。
送出成功但 commit 前當機時，下次會再送一次（至少一次）；通知中心 inbox 送出沒有副作用。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.states import AlertState
from app.models.alert import Alert, AlertDelivery
from app.worker.celery_app import RETRY_FAILED_DELIVERIES_TASK, celery_app

logger = logging.getLogger(__name__)

OWNER = "owner"
"""唯一的 audience：使用者本人。"""
INBOX = "inbox"
DELIVERED = "delivered"
FAILED = "failed"
PENDING = "pending"

MAX_RETRIES = 3
"""第一次失敗後最多重試的次數；第 3 次重試仍失敗就停止。"""
RETRY_DELAYS: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
)
"""第 n 次失敗後到下一次重試的間隔（遞增）。"""
ACTIVE_STATES = frozenset({AlertState.PUBLISHED.value, AlertState.UPDATED.value})
"""還在通知中的狀態；撤回、過期的通知不再重試。"""


class Channel(Protocol):
    """遞送管道；`send` 只會在該版本第一次遞送時被呼叫。"""

    name: str

    def destination(self, audience: str) -> str:
        """`audience` 在此管道的目的地（例：email 地址；不得寫入日誌）。"""
        ...

    async def send(self, alert: Alert, destination: str) -> None:
        """送出通知；失敗時拋出例外。"""
        ...


class InboxChannel:
    """管理介面通知中心：內容已在 alerts 表，送出不需要額外動作。"""

    name = INBOX

    def destination(self, audience: str) -> str:
        """通知中心的目的地就是 audience 本身。"""
        return audience

    async def send(self, alert: Alert, destination: str) -> None:
        """通知中心直接讀取 alerts，不需要送出。"""


DEFAULT_CHANNELS: tuple[Channel, ...] = (InboxChannel(),)


def _next_attempt(attempts: int, now: datetime) -> datetime | None:
    """已送 `attempts` 次且最後一次失敗時的下次時間；重試次數用完時回傳 None。"""
    retries_done = attempts - 1
    if retries_done >= MAX_RETRIES:
        return None
    return now + RETRY_DELAYS[retries_done]


async def _send(channel: Channel, alert: Alert, destination: str) -> bool:
    """呼叫管道送出；任何例外都只記錄類型（不含內容與目的地），回傳是否成功。"""
    try:
        await channel.send(alert, destination)
    except Exception as e:  # 外部管道的任何失敗都只記錄狀態，不中斷其他管道
        logger.warning(
            "通知遞送失敗",
            extra={"alert_id": str(alert.id), "channel": channel.name, "error": type(e).__name__},
        )
        return False
    return True


async def deliver(
    session: AsyncSession,
    alert: Alert,
    channels: Sequence[Channel] = DEFAULT_CHANNELS,
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """遞送 `alert` 目前的版本；回傳這次實際送出的管道（已遞送過的略過）。

    失敗的管道記為 `failed` 並排定第一次重試（`now` 預設為目前的 UTC 時間）。
    """
    now = now or datetime.now(UTC)
    sent: list[str] = []
    for channel in channels:
        destination = channel.destination(alert.audience)
        claimed = await session.scalar(
            insert(AlertDelivery)
            .values(
                alert_id=alert.id,
                channel=channel.name,
                destination=destination,
                version=alert.version,
                status=PENDING,
                attempts=1,
            )
            .on_conflict_do_nothing(
                index_elements=["alert_id", "channel", "destination", "version"]
            )
            .returning(AlertDelivery.id)
        )
        if claimed is None:
            continue
        ok = await _send(channel, alert, destination)
        await session.execute(
            update(AlertDelivery)
            .where(AlertDelivery.id == claimed)
            .values(
                status=DELIVERED if ok else FAILED,
                next_attempt_at=None if ok else _next_attempt(1, now),
            )
        )
        if ok:
            sent.append(channel.name)
    return tuple(sent)


@dataclass(frozen=True, slots=True)
class RetryResult:
    """一次重試掃描的結果：實際重送的列數、其中送達的列數、因通知失效而停止的列數。"""

    retried: int = 0
    delivered: int = 0
    stopped: int = 0


async def retry_failed_deliveries(
    session: AsyncSession,
    channels: Sequence[Channel] = DEFAULT_CHANNELS,
    *,
    now: datetime | None = None,
) -> RetryResult:
    """重試到期的 failed 遞送；每列各自 commit（送出時持有該列的鎖，其他 worker 會略過）。

    只處理 `channels` 中有的管道；其他管道的列留給有該管道的 worker。
    """
    now = now or datetime.now(UTC)
    by_name = {c.name: c for c in channels}
    retried = delivered = stopped = 0
    seen: set[uuid.UUID] = set()  # 防呆：同一次掃描不會處理同一列兩次
    while True:
        row = await session.scalar(
            select(AlertDelivery)
            .where(
                AlertDelivery.status == FAILED,
                AlertDelivery.next_attempt_at.is_not(None),
                AlertDelivery.next_attempt_at <= now,
                AlertDelivery.channel.in_(list(by_name)),
                AlertDelivery.id.not_in(seen),
            )
            .order_by(AlertDelivery.next_attempt_at, AlertDelivery.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            break
        seen.add(row.id)
        alert = await session.get(Alert, row.alert_id, populate_existing=True)
        if alert is None or alert.state not in ACTIVE_STATES or alert.version != row.version:
            row.next_attempt_at = None
            stopped += 1
        else:
            ok = await _send(by_name[row.channel], alert, row.destination)
            row.attempts += 1
            row.status = DELIVERED if ok else FAILED
            row.next_attempt_at = None if ok else _next_attempt(row.attempts, now)
            retried += 1
            delivered += int(ok)
        await session.commit()
    return RetryResult(retried, delivered, stopped)


# ---------- Celery 任務 ----------


async def _in_worker() -> None:
    from app.worker.tasks.ingest import open_sessionmaker  # 避免 API 匯入時載入擷取模組

    async with open_sessionmaker() as maker, maker() as session:
        await retry_failed_deliveries(session)


@celery_app.task(name=RETRY_FAILED_DELIVERIES_TASK)
def retry_failed_deliveries_task() -> None:
    """Celery 任務：重試到期的通知遞送（beat 每分鐘一次）。"""
    asyncio.run(_in_worker())
