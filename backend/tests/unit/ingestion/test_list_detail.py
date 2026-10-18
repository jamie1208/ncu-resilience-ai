"""P1-T16：列表＋內頁型 HTML Adapter 的共用流程（以最小的示範 Adapter 測試）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx2
import pytest
from selectolax.lexbor import LexborHTMLParser

from app.ingestion.adapters.list_detail import Detail, ListDetailAdapter, ListEntry
from app.ingestion.base import AdapterError, SourceAdapter
from app.ingestion.html import TAIPEI
from app.ingestion.http import USER_AGENT, HostRateLimiter
from app.ingestion.types import AccessPolicy, SourceConfig

pytestmark = pytest.mark.anyio

SITE = "https://demo.ncu.edu.tw"
LIST_URL = f"{SITE}/news.php"
ROBOTS = f"{SITE}/robots.txt"
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)

Route = tuple[int, str] | Callable[[httpx2.Request], httpx2.Response]


def detail_url(n: int) -> str:
    return f"{SITE}/detail.php?id={n}"


class DemoAdapter(ListDetailAdapter):
    """列表每個 <a data-day> 是一篇；內頁 <h1> 是標題、<article> 是內文。"""

    def parse_list(self, html: str, list_url: str, now: datetime) -> list[ListEntry]:
        tree = LexborHTMLParser(html)
        entries = []
        for a in tree.css("a[data-id]"):
            day = a.attributes.get("data-day")
            entries.append(
                ListEntry(
                    key=a.attributes["data-id"] or "",
                    url=a.attributes["href"] or "",
                    title=a.text(strip=True),
                    published=date.fromisoformat(day) if day else None,
                )
            )
        return entries

    def parse_detail(self, html: str) -> Detail:
        tree = LexborHTMLParser(html)
        h1 = tree.css_first("h1")
        body = tree.css_first("article")
        stamp = tree.css_first("time")
        return Detail(
            title=h1.text(strip=True) if h1 else "",
            published=datetime.fromisoformat(stamp.text()).replace(tzinfo=TAIPEI)
            if stamp
            else None,
            content_html=body.html if body is not None and body.text(strip=True) else None,
        )


def list_html(*entries: tuple[int, str | None] | tuple[int, str | None, str]) -> str:
    links = []
    for entry in entries:
        n, day, *href = entry
        url = href[0] if href else detail_url(n)
        attr = f' data-day="{day}"' if day else ""
        links.append(f'<a data-id="{n}"{attr} href="{url}">公告 {n}</a>')
    return "<html><body>" + "".join(links) + "</body></html>"


def detail_html(n: int, *, body: str = "內文", stamp: str | None = None) -> str:
    time = f"<time>{stamp}</time>" if stamp else ""
    return f"<html><body><h1>標題 {n}</h1>{time}<article>{body}</article></body></html>"


class FakeSite:
    def __init__(self, routes: dict[str, Route]) -> None:
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


def routes(*ids: int, days: dict[int, str] | None = None) -> dict[str, Route]:
    days = days or {}
    table: dict[str, Route] = {
        ROBOTS: (200, "User-agent: *\nAllow: /\n"),
        LIST_URL: (200, list_html(*[(n, days.get(n, "2026-10-01")) for n in ids])),
    }
    for n in ids:
        table[detail_url(n)] = (200, detail_html(n))
    return table


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def config(**options: Any) -> SourceConfig:
    return SourceConfig(
        code="demo",
        type="demo",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={"list_url": LIST_URL, **options},
    )


def make(site: FakeSite, t: FakeTime | None = None, **options: Any) -> DemoAdapter:
    t = t or FakeTime()
    return DemoAdapter(
        config(**options),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(site)),
        limiter=HostRateLimiter(clock=t.clock, sleep=t.sleep),
        clock=lambda: NOW,
    )


# --- 設定 -------------------------------------------------------------------------------


def test_is_a_source_adapter() -> None:
    assert isinstance(make(FakeSite({})), SourceAdapter)


@pytest.mark.parametrize(
    "options",
    [
        {"list_url": "ftp://demo.ncu.edu.tw/news"},
        {"list_url": "/news.php"},
        {"max_items": 0},
        {"max_items": 51},
        {"min_interval": 1},
        {"timeout": 0},
        {"max_bytes": 0},
        {"unknown": True},
    ],
)
def test_invalid_options_are_rejected(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        DemoAdapter(config(**options))


# --- 擷取 -------------------------------------------------------------------------------


async def test_items_have_fields_from_list_and_detail() -> None:
    site = FakeSite(routes(3))
    [item] = await make(site).fetch_since(None)
    assert item.source_code == "demo"
    assert item.external_id == "3"
    assert item.url == detail_url(3)
    assert item.title == "標題 3"
    assert item.content_type == "text/html"
    assert "內文" in item.content
    assert item.published_at == datetime(2026, 9, 30, 16, 0, tzinfo=UTC)  # 列表日期，台北 00:00
    assert item.fetched_at == NOW


async def test_detail_time_takes_priority_over_list_date() -> None:
    table = routes(3)
    table[detail_url(3)] = (200, detail_html(3, stamp="2026-10-01T09:30:00"))
    [item] = await make(FakeSite(table)).fetch_since(None)
    assert item.published_at == datetime(2026, 10, 1, 1, 30, tzinfo=UTC)


async def test_detail_without_body_uses_title_as_plain_text() -> None:
    table = routes(3)
    table[detail_url(3)] = (200, detail_html(3, body=" "))
    [item] = await make(FakeSite(table)).fetch_since(None)
    assert item.content == "標題 3"
    assert item.content_type == "text/plain"


async def test_requests_use_user_agent_and_rate_limit() -> None:
    site = FakeSite(routes(1, 2))
    t = FakeTime()
    await make(site, t, min_interval=6).fetch_since(None)
    assert all(r.headers["User-Agent"] == USER_AGENT for r in site.requests)
    assert t.sleeps == [6.0] * (len(site.requests) - 1)


async def test_duplicate_entries_on_list_are_fetched_once() -> None:
    table = routes(1)
    table[LIST_URL] = (200, list_html((1, "2026-10-01"), (1, "2026-10-01")))
    site = FakeSite(table)
    items = await make(site).fetch_since(None)
    assert len(items) == 1
    assert site.urls.count(detail_url(1)) == 1


async def test_links_to_other_hosts_are_ignored() -> None:
    table = routes(1)
    table[LIST_URL] = (
        200,
        list_html((1, "2026-10-01"), (2, "2026-10-01", "https://other.example.edu/x?id=2")),
    )
    site = FakeSite(table)
    items = await make(site).fetch_since(None)
    assert [i.external_id for i in items] == ["1"]
    assert not any("other.example.edu" in u for u in site.urls)


# --- 略過與錯誤 ---------------------------------------------------------------------------


async def test_deleted_detail_is_skipped() -> None:
    table = routes(1, 2)
    del table[detail_url(2)]
    adapter = make(FakeSite(table))
    items = await adapter.fetch_since(None)
    assert [i.external_id for i in items] == ["1"]
    assert adapter.last_skipped == 1


async def test_detail_redirected_to_other_host_is_skipped() -> None:
    table = routes(1)
    table[detail_url(1)] = lambda r: httpx2.Response(
        302, headers={"location": "https://other.example.edu/"}
    )
    table["https://other.example.edu/"] = (200, detail_html(1))
    adapter = make(FakeSite(table))
    assert await adapter.fetch_since(None) == []
    assert adapter.last_skipped == 1


async def test_robots_disallowing_detail_skips_it() -> None:
    table = routes(1, 2)
    table[ROBOTS] = (200, "User-agent: *\nDisallow: /detail.php?id=2\n")
    site = FakeSite(table)
    adapter = make(site)
    items = await adapter.fetch_since(None)
    assert [i.external_id for i in items] == ["1"]
    assert detail_url(2) not in site.urls
    assert adapter.last_skipped == 1


async def test_robots_disallowing_list_raises_non_retryable() -> None:
    table = routes(1)
    table[ROBOTS] = (200, "User-agent: *\nDisallow: /\n")
    site = FakeSite(table)
    with pytest.raises(AdapterError) as info:
        await make(site).fetch_since(None)
    assert info.value.retryable is False
    assert LIST_URL not in site.urls


async def test_list_client_error_raises() -> None:
    table = routes(1)
    table[LIST_URL] = (404, "")
    with pytest.raises(AdapterError) as info:
        await make(FakeSite(table)).fetch_since(None)
    assert info.value.retryable is False


# --- cursor（數字編號） -------------------------------------------------------------------


async def test_cursor_skips_known_ids_and_takes_oldest_first() -> None:
    site = FakeSite(routes(5, 3, 9, 7))
    adapter = make(site, max_items=2)
    items = await adapter.fetch_since("4")
    assert [i.external_id for i in items] == ["5", "7"]
    assert detail_url(3) not in site.urls
    assert adapter.next_cursor(items, "4") == "7"


async def test_next_cursor_never_goes_back() -> None:
    adapter = make(FakeSite({}))
    assert adapter.next_cursor([], "9") == "9"
    assert adapter.next_cursor([], None) is None


@pytest.mark.parametrize("cursor", ["abc", "-1", "1.5"])
async def test_invalid_numeric_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(AdapterError):
        await make(FakeSite(routes(1))).fetch_since(cursor)
