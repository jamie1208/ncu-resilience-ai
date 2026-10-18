"""擷取用的 HTTP 共用設定（來源盤點「共通擷取原則」第 1、2 條）。

- `USER_AGENT`、`HostRateLimiter`：專案 User-Agent 與每網域限速。
- `PageFetcher`：HTML Adapter 共用的頁面讀取（P1-T14）：robots.txt 檢查（每個 origin 快取一次）、
  限速、串流讀取與大小上限、錯誤分類（5xx、408、425、429、連線失敗可重試；其他不可重試）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx2

from app.ingestion.base import AdapterError, SourceUnavailableError

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


RETRYABLE_STATUS = frozenset({408, 425, 429})
"""5xx 以外也可重試的 HTTP 狀態碼。"""


@dataclass(frozen=True)
class Page:
    """讀取結果：狀態碼、轉址後的最終網址與解碼後的內容。4xx 不拋例外，由呼叫端判斷。"""

    status: int
    url: str
    text: str


class PageFetcher:
    """一個來源的頁面讀取器；robots.txt 規則依 origin 快取在這個物件上，跨輪次沿用。

    錯誤訊息只含來源代碼、HTTP 狀態與例外類型，不含頁面內容。
    """

    def __init__(
        self,
        source_code: str,
        *,
        limiter: HostRateLimiter,
        min_interval: float,
        timeout: float,
        max_bytes: int,
    ) -> None:
        self._code = source_code
        self._limiter = limiter
        self._min_interval = min_interval
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._robots: dict[str, RobotFileParser] = {}

    async def allowed(self, client: httpx2.AsyncClient, url: str) -> bool:
        """robots.txt 是否允許本專案讀取 `url`。沒有 robots.txt（4xx）視為全部允許。"""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rules = self._robots.get(origin)
        if rules is None:
            page = await self.get(client, f"{origin}/robots.txt")
            rules = RobotFileParser()
            # 4xx（沒有 robots.txt）視為全部允許；5xx 已在 get 拋出可重試錯誤
            rules.parse(page.text.splitlines() if page.status < 400 else [])
            self._robots[origin] = rules
        return rules.can_fetch(USER_AGENT, url)

    async def get(self, client: httpx2.AsyncClient, url: str) -> Page:
        """限速後以串流讀取 `url`（跟隨轉址）；超過大小上限或回應可重試的錯誤時拋出例外。"""
        await self._limiter.wait(urlsplit(url).netloc, self._min_interval)
        try:
            async with client.stream(
                "GET",
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
                follow_redirects=True,
            ) as response:
                status = response.status_code
                if status >= 500 or status in RETRYABLE_STATUS:
                    raise SourceUnavailableError(f"來源 {self._code} 回應 HTTP {status}")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise AdapterError(f"來源 {self._code} 的頁面超過 {self._max_bytes} bytes")
                    chunks.append(chunk)
                text = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                return Page(status, str(response.url), text)
        except httpx2.TransportError as exc:
            raise SourceUnavailableError(
                f"來源 {self._code} 連線失敗（{type(exc).__name__}）"
            ) from exc
        except httpx2.HTTPError as exc:
            raise AdapterError(f"來源 {self._code} 請求失敗（{type(exc).__name__}）") from exc
