"""個人通知的遞送（P3-T08）。

通知只送給使用者本人（ADR-0008）。目前只有管理介面通知中心（`inbox`）：通知中心直接讀取
alerts 表，所以遞送只需記錄一筆 alert_deliveries。Email 等外部管道之後實作 `Channel` 介面即可。

冪等：alert_deliveries 對 (alert_id, channel, destination, version) 有唯一約束，遞送前先以
`INSERT ... ON CONFLICT DO NOTHING` 佔位，佔位成功才呼叫管道送出；同一版本重送一律略過。
送出失敗只記錄狀態 `failed`，不重試（之後由外部管道的 worker 處理）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert, AlertDelivery

logger = logging.getLogger(__name__)

OWNER = "owner"
"""唯一的 audience：使用者本人。"""
INBOX = "inbox"
DELIVERED = "delivered"
FAILED = "failed"
PENDING = "pending"


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


async def deliver(
    session: AsyncSession, alert: Alert, channels: Sequence[Channel] = DEFAULT_CHANNELS
) -> tuple[str, ...]:
    """遞送 `alert` 目前的版本；回傳這次實際送出的管道（已遞送過的略過）。"""
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
            )
            .on_conflict_do_nothing(
                index_elements=["alert_id", "channel", "destination", "version"]
            )
            .returning(AlertDelivery.id)
        )
        if claimed is None:
            continue
        status = DELIVERED
        try:
            await channel.send(alert, destination)
        except Exception as e:  # 外部管道的任何失敗都只記錄狀態，不中斷其他管道
            status = FAILED
            logger.warning(
                "通知遞送失敗",
                extra={
                    "alert_id": str(alert.id),
                    "channel": channel.name,
                    "error": type(e).__name__,
                },
            )
        await session.execute(
            update(AlertDelivery).where(AlertDelivery.id == claimed).values(status=status)
        )
        if status == DELIVERED:
            sent.append(channel.name)
    return tuple(sent)
