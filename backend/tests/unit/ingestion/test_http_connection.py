"""P1-T10：PageFetcher 不重用連線（`Connection: close`）。

校方網站的 `Keep-Alive: timeout=5` 與我們的 5 秒請求間隔撞在一起：限速器從上一個請求開始計時，
連線實際閒置約 4.9 秒，伺服器剛好在送出下一個請求時關閉連線（RemoteProtocolError）。
請求本來就每 5 秒一次，重用連線沒有好處，所以每個請求都要求伺服器回應後關閉連線。
"""

from __future__ import annotations

import httpx2
import pytest

from app.ingestion.http import USER_AGENT, HostRateLimiter, PageFetcher

pytestmark = pytest.mark.anyio


async def test_every_request_asks_server_to_close_the_connection() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, text="ok")

    async def no_sleep(_: float) -> None:
        return None

    fetcher = PageFetcher(
        "demo",
        limiter=HostRateLimiter(sleep=no_sleep),
        min_interval=5.0,
        timeout=5.0,
        max_bytes=1_000,
    )
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    await fetcher.allowed(client, "https://demo.ncu.edu.tw/list.php")  # robots.txt
    await fetcher.get(client, "https://demo.ncu.edu.tw/list.php")
    assert len(seen) == 2
    for request in seen:
        assert request.headers["Connection"] == "close"
        assert request.headers["User-Agent"] == USER_AGENT
