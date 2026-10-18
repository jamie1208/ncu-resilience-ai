"""P1-T14：HTML Adapter 共用的 HTTP 讀取（robots、限速、大小上限、錯誤分類）。"""

from __future__ import annotations

from collections.abc import Callable

import httpx2
import pytest

from app.ingestion.base import AdapterError, SourceUnavailableError
from app.ingestion.http import USER_AGENT, HostRateLimiter, Page, PageFetcher

pytestmark = pytest.mark.anyio

SITE = "https://news.example.edu"
ROBOTS = f"{SITE}/robots.txt"
PAGE = f"{SITE}/list.php"

Handler = Callable[[httpx2.Request], httpx2.Response]


class FakeSite:
    def __init__(self, routes: dict[str, Handler | tuple[int, str]]) -> None:
        self.routes = routes
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        route = self.routes.get(str(request.url))
        if route is None:
            return httpx2.Response(404, text="not found")
        if callable(route):
            return route(request)
        status, body = route
        return httpx2.Response(
            status, text=body, headers={"content-type": "text/html; charset=utf-8"}
        )

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def setup(
    routes: dict[str, Handler | tuple[int, str]],
    *,
    min_interval: float = 5.0,
    max_bytes: int = 1_000,
) -> tuple[PageFetcher, httpx2.AsyncClient, FakeSite, FakeTime]:
    site = FakeSite(routes)
    t = FakeTime()
    fetcher = PageFetcher(
        "demo",
        limiter=HostRateLimiter(clock=t.clock, sleep=t.sleep),
        min_interval=min_interval,
        timeout=5.0,
        max_bytes=max_bytes,
    )
    return fetcher, httpx2.AsyncClient(transport=httpx2.MockTransport(site)), site, t


# --- 讀取 -------------------------------------------------------------------------------


async def test_get_returns_status_final_url_and_text() -> None:
    fetcher, client, site, _ = setup({PAGE: (200, "<p>公告</p>")})
    page = await fetcher.get(client, PAGE)
    assert page == Page(200, PAGE, "<p>公告</p>")
    assert site.requests[0].headers["User-Agent"] == USER_AGENT


async def test_get_follows_redirects_and_reports_final_url() -> None:
    target = f"{SITE}/new.php"
    fetcher, client, _, _ = setup(
        {
            PAGE: lambda r: httpx2.Response(302, headers={"location": target}),
            target: (200, "moved"),
        }
    )
    page = await fetcher.get(client, PAGE)
    assert page.url == target
    assert page.text == "moved"


async def test_client_errors_are_returned_not_raised() -> None:
    fetcher, client, _, _ = setup({})
    page = await fetcher.get(client, PAGE)
    assert page.status == 404


@pytest.mark.parametrize("status", [500, 503, 408, 425, 429])
async def test_retryable_statuses_raise_source_unavailable(status: int) -> None:
    fetcher, client, _, _ = setup({PAGE: (status, "")})
    with pytest.raises(SourceUnavailableError) as info:
        await fetcher.get(client, PAGE)
    assert "demo" in str(info.value)
    assert info.value.retryable is True


async def test_transport_error_is_retryable() -> None:
    def boom(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    fetcher, client, _, _ = setup({PAGE: boom})
    with pytest.raises(SourceUnavailableError):
        await fetcher.get(client, PAGE)


async def test_oversized_page_is_not_retryable() -> None:
    fetcher, client, _, _ = setup({PAGE: (200, "x" * 1_001)})
    with pytest.raises(AdapterError) as info:
        await fetcher.get(client, PAGE)
    assert info.value.retryable is False
    assert "1000" in str(info.value)


async def test_page_at_limit_is_allowed() -> None:
    fetcher, client, _, _ = setup({PAGE: (200, "x" * 1_000)})
    assert len((await fetcher.get(client, PAGE)).text) == 1_000


async def test_declared_encoding_is_used() -> None:
    body = "停電".encode("big5")
    fetcher, client, _, _ = setup(
        {
            PAGE: lambda r: httpx2.Response(
                200, content=body, headers={"content-type": "text/html; charset=big5"}
            )
        }
    )
    assert (await fetcher.get(client, PAGE)).text == "停電"


async def test_requests_to_same_host_are_rate_limited() -> None:
    fetcher, client, _, t = setup({PAGE: (200, "a")}, min_interval=7.0)
    await fetcher.get(client, PAGE)
    await fetcher.get(client, PAGE)
    assert t.sleeps == [7.0]


# --- robots.txt -------------------------------------------------------------------------


async def test_robots_disallow_applies_to_our_agent() -> None:
    fetcher, client, _, _ = setup({ROBOTS: (200, "User-agent: NCU-Resilience-AI\nDisallow: /\n")})
    assert await fetcher.allowed(client, PAGE) is False


async def test_robots_allow() -> None:
    fetcher, client, _, _ = setup({ROBOTS: (200, "User-agent: *\nDisallow: /private/\n")})
    assert await fetcher.allowed(client, PAGE) is True
    assert await fetcher.allowed(client, f"{SITE}/private/x") is False


async def test_missing_robots_allows_everything() -> None:
    fetcher, client, _, _ = setup({})
    assert await fetcher.allowed(client, PAGE) is True


async def test_robots_server_error_is_retryable() -> None:
    fetcher, client, _, _ = setup({ROBOTS: (503, "")})
    with pytest.raises(SourceUnavailableError):
        await fetcher.allowed(client, PAGE)


async def test_robots_is_fetched_once_per_origin_across_clients() -> None:
    fetcher, client, site, _ = setup({ROBOTS: (200, "User-agent: *\nAllow: /\n")})
    await fetcher.allowed(client, PAGE)
    await fetcher.allowed(client, f"{SITE}/other.php")
    other = httpx2.AsyncClient(transport=httpx2.MockTransport(site))
    await fetcher.allowed(other, PAGE)  # 每輪換一個 client，快取仍有效
    await fetcher.allowed(client, "https://other.example.edu/x")
    assert site.urls.count(ROBOTS) == 1
    assert site.urls.count("https://other.example.edu/robots.txt") == 1
