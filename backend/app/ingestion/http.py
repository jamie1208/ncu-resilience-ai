"""擷取用的 HTTP 共用設定：User-Agent 與每網域限速（來源盤點「共通擷取原則」第 1、2 條）。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

USER_AGENT = (
    "NCU-Resilience-AI/0.1 (campus resilience research project; "
    "+https://github.com/jamie1208/ncu-resilience-ai)"
)
"""所有 Adapter 共用的 User-Agent：標示專案名稱與聯絡網址，不偽裝瀏覽器。"""

MIN_REQUEST_INTERVAL = 5.0
"""同一網域兩次請求的最短間隔（秒）。"""


class HostRateLimiter:
    """每個網域依序放行請求，兩次之間至少間隔 `min_interval` 秒。

    同一網域的等待者會排隊（持有該網域的鎖直到放行），所以同一時間最多一個請求。
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, host: str, min_interval: float = MIN_REQUEST_INTERVAL) -> None:
        """等到可以對 `host` 發出下一個請求為止。"""
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host)
            if last is not None:
                delay = last + min_interval - self._clock()
                if delay > 0:
                    await self._sleep(delay)
            self._last[host] = self._clock()


shared_limiter = HostRateLimiter()
"""行程內共用的限速器：不同來源若在同一網域，也會一起排隊。"""
