"""P1-T12：中央大學新聞網 Adapter（固定快照，不連網）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingestion.adapters.ncu_news import NcuNewsAdapter, parse_detail, parse_list
from app.ingestion.base import (
    AdapterError,
    LayoutChangedError,
    SourceAdapter,
    SourceUnavailableError,
)
from app.ingestion.http import USER_AGENT, HostRateLimiter
from app.ingestion.registry import registry
from app.ingestion.types import AccessPolicy, SourceConfig

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "ncu_news"
BASE = "https://ncusec.ncu.edu.tw/news"
HEADLINES = f"{BASE}/headlines.php"
FOCUS = f"{BASE}/focus-news.php"
ACADEMIC = f"{BASE}/academic-news.php"
LISTS = [HEADLINES, FOCUS, ACADEMIC]
ROBOTS_URL = "https://ncusec.ncu.edu.tw/robots.txt"
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)


def detail_url(news_id: int) -> str:
    return f"{BASE}/headlines_content.php?H_ID={news_id}"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


Route = tuple[int, str] | Callable[[httpx2.Request], httpx2.Response]


class FakeSite:
    """以網址對應回應的假網站；記錄所有請求。未登記的網址回 404。"""

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


def default_routes() -> dict[str, Route]:
    return {
        # 實際網站的 robots.txt 回傳一般 HTML 頁面（2026-10-18），視為沒有規則
        ROBOTS_URL: (200, fixture("robots_html.html")),
        HEADLINES: (200, fixture("headlines.html")),
        FOCUS: (200, fixture("focus_news.html")),
        ACADEMIC: (200, fixture("academic_news.html")),
        detail_url(9003): (200, fixture("detail.html")),
        detail_url(9002): (200, fixture("detail_no_body.html")),
        detail_url(9001): (200, fixture("detail_no_date.html")),
        detail_url(8001): (200, fixture("detail.html")),
    }


class FakeTime:
    """可控制的單調時鐘與 sleep：sleep 只推進時間並記錄秒數。"""

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
        code="ncu-news",
        type="ncu_news",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={"list_urls": LISTS, **options},
    )


def make(site: FakeSite, fake_time: FakeTime | None = None, **options: Any) -> NcuNewsAdapter:
    t = fake_time or FakeTime()
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(site))
    limiter = HostRateLimiter(clock=t.clock, sleep=t.sleep)
    return NcuNewsAdapter(config(**options), client=client, limiter=limiter, clock=lambda: NOW)


# --- 列表頁解析（兩種卡片版型） --------------------------------------------------------


def test_parse_headlines_list() -> None:
    entries = parse_list(fixture("headlines.html"), HEADLINES)
    assert [e.news_id for e in entries] == [9003, 9002, 9001]
    assert entries[0].url == detail_url(9003)  # 相對網址補成絕對網址
    assert entries[0].title == "測試校園防災演練圓滿完成"
    assert [e.published for e in entries] == [
        date(2026, 10, 17),
        date(2026, 10, 16),
        date(2026, 10, 15),
    ]
    assert [e.category for e in entries] == ["焦點新聞", "學術新聞", "焦點新聞"]


def test_parse_headlines_ignores_noise() -> None:
    urls = [e.url for e in parse_list(fixture("headlines.html"), HEADLINES)]
    assert not any("other.example.edu" in u for u in urls)  # 他站連結
    assert detail_url(9999) not in urls  # 沒有標題的輪播圖連結
    assert all("headlines_content.php" in u for u in urls)  # 選單、分頁連結


def test_parse_box_card_list() -> None:
    entries = parse_list(fixture("academic_news.html"), ACADEMIC)
    assert [e.news_id for e in entries] == [9002, 8001]
    # 以根目錄開頭的網址也正規化成同一種寫法
    assert entries[1].url == detail_url(8001)
    assert entries[1].title == "測試地震觀測網研究成果"
    assert entries[1].published == date(2026, 9, 15)
    assert {e.category for e in entries} == {"學術新聞"}


def test_parse_list_without_cards_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_list(fixture("list_no_cards.html"), HEADLINES)


def test_parse_list_without_category_or_date_leaves_them_empty() -> None:
    html = (
        fixture("focus_news.html")
        .replace('<div class="tab-box focus-tab me-3">焦點新聞</div>', "", 1)
        .replace('<div class="date-box">2026-10-17</div>', "", 1)
    )
    entries = parse_list(html, FOCUS)
    assert entries[0].category is None
    assert entries[0].published is None
    assert entries[1].published == date(2026, 10, 15)  # 不會誤取其他卡片的日期


# --- 內頁解析 --------------------------------------------------------------------------


def test_parse_detail_fields() -> None:
    page = parse_detail(fixture("detail.html"))
    assert page.title == "測試校園防災演練圓滿完成"
    assert page.published == date(2026, 10, 17)
    assert page.content_html is not None
    assert "芮氏規模 6.5 地震" in page.content_html
    assert "瀏覽人次" not in page.content_html  # 只取內文區塊
    assert "facebook" not in page.content_html


def test_parse_detail_without_text_has_no_content() -> None:
    page = parse_detail(fixture("detail_no_body.html"))
    assert page.title == "測試研究團隊發表豪雨預警模型"
    assert page.content_html is None


def test_parse_detail_without_date_has_no_published() -> None:
    page = parse_detail(fixture("detail_no_date.html"))
    assert page.published is None
    assert page.content_html is not None


def test_parse_detail_without_title_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_detail(fixture("detail_no_title.html"))


def test_parse_detail_without_detail_box_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_detail(fixture("headlines.html"))


# --- Adapter：擷取、跨列表去重與欄位 ---------------------------------------------------


def test_registered_as_ncu_news_type() -> None:
    adapter = registry.create(config())
    assert isinstance(adapter, NcuNewsAdapter)
    assert isinstance(adapter, SourceAdapter)
    assert adapter.code == "ncu-news"


@pytest.mark.anyio
async def test_fetch_merges_three_lists_without_duplicates() -> None:
    site = FakeSite(default_routes())
    items = await make(site).fetch_since(None)
    # 9003、9002、9001 同時出現在中大新聞與子分類列表，只產出一筆；8001 只在學術新聞
    assert [i.external_id for i in items] == ["8001", "9001", "9002", "9003"]
    for news_id in (8001, 9001, 9002, 9003):
        assert site.urls.count(detail_url(news_id)) == 1
    assert all(url in site.urls for url in LISTS)


@pytest.mark.anyio
async def test_categories_are_kept_per_item() -> None:
    a = make(FakeSite(default_routes()))
    await a.fetch_since(None)
    assert a.categories == {
        "8001": frozenset({"學術新聞"}),
        "9001": frozenset({"焦點新聞"}),
        "9002": frozenset({"學術新聞"}),
        "9003": frozenset({"焦點新聞"}),
    }


@pytest.mark.anyio
async def test_categories_merge_across_lists() -> None:
    routes = default_routes()
    # 同一篇在中大新聞列表標為學術新聞，在焦點新聞列表標為焦點新聞
    routes[HEADLINES] = (
        200,
        fixture("headlines.html").replace(
            '<div class="tab-box focus-tab me-3">焦點新聞</div>',
            '<div class="tab-box academic-tab me-3">學術新聞</div>',
            1,
        ),
    )
    a = make(FakeSite(routes))
    await a.fetch_since(None)
    assert a.categories["9003"] == frozenset({"焦點新聞", "學術新聞"})


@pytest.mark.anyio
async def test_item_fields() -> None:
    items = await make(FakeSite(default_routes())).fetch_since(None)
    item = items[-1]
    assert item.source_code == "ncu-news"
    assert item.external_id == "9003"
    assert item.url == detail_url(9003)
    assert item.title == "測試校園防災演練圓滿完成"
    assert item.content_type == "text/html"
    assert "芮氏規模 6.5 地震" in item.content
    # 只有日期：視為台北時間 00:00，存成 UTC
    assert item.published_at == datetime(2026, 10, 16, 16, 0, tzinfo=UTC)
    assert item.fetched_at == NOW


@pytest.mark.anyio
async def test_item_without_text_uses_title_as_content() -> None:
    items = await make(FakeSite(default_routes())).fetch_since(None)
    item = next(i for i in items if i.external_id == "9002")
    assert item.content == "測試研究團隊發表豪雨預警模型"
    assert item.content_type == "text/plain"


@pytest.mark.anyio
async def test_published_falls_back_to_list_date() -> None:
    items = await make(FakeSite(default_routes())).fetch_since(None)
    item = next(i for i in items if i.external_id == "9001")
    # 內頁沒有日期，列表頁上 9001 的日期是 2026-10-15（台北時間）
    assert item.published_at == datetime(2026, 10, 14, 16, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_only_first_page_of_each_list_is_fetched() -> None:
    site = FakeSite(default_routes())
    await make(site).fetch_since(None)
    assert not any("np=" in u or "s=12" in u for u in site.urls)  # 不跟隨分頁、不回溯歷史


@pytest.mark.anyio
async def test_cursor_skips_known_news_without_fetching_them() -> None:
    site = FakeSite(default_routes())
    items = await make(site).fetch_since("9001")
    assert [i.external_id for i in items] == ["9002", "9003"]
    assert detail_url(9001) not in site.urls
    assert detail_url(8001) not in site.urls


@pytest.mark.anyio
async def test_max_items_takes_oldest_new_news_first() -> None:
    a = make(FakeSite(default_routes()), max_items=2)
    items = await a.fetch_since(None)
    assert [i.external_id for i in items] == ["8001", "9001"]
    assert a.next_cursor(items, None) == "9001"


@pytest.mark.anyio
async def test_next_cursor_is_max_news_id_and_never_goes_back() -> None:
    a = make(FakeSite(default_routes()))
    items = await a.fetch_since(None)
    assert a.next_cursor(items, None) == "9003"
    assert a.next_cursor(items, "9500") == "9500"
    assert a.next_cursor([], "9003") == "9003"
    assert a.next_cursor([], None) is None


@pytest.mark.anyio
@pytest.mark.parametrize("cursor", ["abc", "-1", "2026-10-17T00:00:00+00:00"])
async def test_invalid_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(AdapterError) as exc:
        await make(FakeSite(default_routes())).fetch_since(cursor)
    assert exc.value.retryable is False


@pytest.mark.anyio
async def test_requests_use_project_user_agent_and_rate_limit() -> None:
    site = FakeSite(default_routes())
    t = FakeTime()
    await make(site, t).fetch_since(None)
    assert site.requests
    assert all(r.headers["user-agent"] == USER_AGENT for r in site.requests)
    # robots + 3 個列表 + 4 篇內頁，同網域依序間隔 5 秒
    assert len(site.requests) == 8
    assert t.sleeps == [5.0] * 7


# --- Adapter：錯誤處理 -----------------------------------------------------------------


@pytest.mark.anyio
async def test_deleted_detail_page_is_skipped() -> None:
    routes = default_routes()
    del routes[detail_url(9002)]  # 404
    a = make(FakeSite(routes))
    items = await a.fetch_since(None)
    assert [i.external_id for i in items] == ["8001", "9001", "9003"]
    assert a.last_skipped == 1
    assert "9002" not in a.categories


@pytest.mark.anyio
async def test_detail_redirected_to_other_host_is_skipped() -> None:
    routes = default_routes()
    routes[detail_url(9002)] = lambda r: httpx2.Response(
        302, headers={"location": "https://elsewhere.example.org/post"}
    )
    routes["https://elsewhere.example.org/post"] = (200, fixture("detail.html"))
    a = make(FakeSite(routes))
    items = await a.fetch_since(None)
    assert "9002" not in [i.external_id for i in items]
    assert a.last_skipped == 1


@pytest.mark.anyio
async def test_detail_layout_change_raises() -> None:
    routes = default_routes()
    routes[detail_url(9003)] = (200, fixture("detail_no_title.html"))
    with pytest.raises(LayoutChangedError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
async def test_list_layout_change_raises() -> None:
    routes = default_routes()
    routes[FOCUS] = (200, fixture("list_no_cards.html"))
    with pytest.raises(LayoutChangedError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_list_server_errors_are_retryable(status: int) -> None:
    routes = default_routes()
    routes[ACADEMIC] = (status, "")
    with pytest.raises(SourceUnavailableError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
async def test_detail_server_error_is_retryable() -> None:
    routes = default_routes()
    routes[detail_url(9001)] = (502, "")
    with pytest.raises(SourceUnavailableError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
async def test_list_client_error_is_not_retryable() -> None:
    routes = default_routes()
    routes[HEADLINES] = (403, "")
    with pytest.raises(AdapterError) as exc:
        await make(FakeSite(routes)).fetch_since(None)
    assert exc.value.retryable is False


@pytest.mark.anyio
async def test_timeout_is_retryable() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("timed out", request=request)

    routes = default_routes()
    routes[HEADLINES] = timeout
    with pytest.raises(SourceUnavailableError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
async def test_oversized_page_is_rejected() -> None:
    with pytest.raises(AdapterError) as exc:
        await make(FakeSite(default_routes()), max_bytes=1000).fetch_since(None)
    assert exc.value.retryable is False


# --- robots.txt ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_robots_disallowing_list_blocks_before_fetching() -> None:
    routes = default_routes()
    routes[ROBOTS_URL] = (200, "User-agent: *\nDisallow: /news/\n")
    site = FakeSite(routes)
    with pytest.raises(AdapterError) as exc:
        await make(site).fetch_since(None)
    assert exc.value.retryable is False
    assert HEADLINES not in site.urls


@pytest.mark.anyio
async def test_robots_disallowing_detail_skips_it() -> None:
    routes = default_routes()
    routes[ROBOTS_URL] = (200, "User-agent: *\nDisallow: /news/headlines_content.php?H_ID=9002\n")
    site = FakeSite(routes)
    a = make(site)
    items = await a.fetch_since(None)
    assert "9002" not in [i.external_id for i in items]
    assert detail_url(9002) not in site.urls
    assert a.last_skipped == 1


@pytest.mark.anyio
async def test_robots_fetched_once_per_run() -> None:
    site = FakeSite(default_routes())
    await make(site).fetch_since(None)
    assert site.urls.count(ROBOTS_URL) == 1


# --- 設定驗證 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        {"list_urls": []},
        {"list_urls": ["ftp://ncusec.ncu.edu.tw/news/headlines.php"]},
        {"list_urls": [HEADLINES, "https://other.example.edu/news/focus-news.php"]},
        {"max_items": 0},
        {"max_items": 51},
        {"min_interval": 1},
        {"unknown": True},
    ],
)
def test_invalid_options_are_rejected(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        NcuNewsAdapter(config(**options))
