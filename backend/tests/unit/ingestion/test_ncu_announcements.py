"""P1-T03：中央大學 RPage 公告 Adapter（固定快照，不連網）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingestion.adapters.ncu_announcements import RPageAdapter, parse_detail, parse_list
from app.ingestion.base import (
    AdapterError,
    LayoutChangedError,
    SourceAdapter,
    SourceUnavailableError,
)
from app.ingestion.http import USER_AGENT, HostRateLimiter
from app.ingestion.registry import registry
from app.ingestion.types import AccessPolicy, SourceConfig

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "html"
LIST_URL = "https://www.ncu.edu.tw/p/403-1000-15-1.php?Lang=zh-tw"
ROBOTS_URL = "https://www.ncu.edu.tw/robots.txt"
NOW = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)


def detail_url(post_id: int) -> str:
    return f"https://www.ncu.edu.tw/p/406-1000-{post_id},r15.php?Lang=zh-tw"


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
        ROBOTS_URL: (200, "User-agent: *\nAllow: /\n"),
        LIST_URL: (200, fixture("rpage_list_table.html")),
        detail_url(5003): (200, fixture("rpage_detail.html")),
        detail_url(5002): (200, fixture("rpage_detail_no_body.html")),
        detail_url(5001): (200, fixture("rpage_detail.html")),
        detail_url(4001): (200, fixture("rpage_detail.html")),
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
        code="ncu-announcements",
        type="rpage",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={"list_url": LIST_URL, **options},
    )


def make(site: FakeSite, fake_time: FakeTime | None = None, **options: Any) -> RPageAdapter:
    t = fake_time or FakeTime()
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(site))
    limiter = HostRateLimiter(clock=t.clock, sleep=t.sleep)
    return RPageAdapter(config(**options), client=client, limiter=limiter, clock=lambda: NOW)


# --- 列表頁解析（三種版型） ------------------------------------------------------------


def test_parse_list_table_template() -> None:
    entries = parse_list(fixture("rpage_list_table.html"), LIST_URL)
    assert [e.post_id for e in entries] == [5003, 5002, 5001, 4001]
    assert entries[0].url == detail_url(5003)
    # 相對網址以列表頁補成絕對網址
    assert entries[2].url == detail_url(5001)
    assert entries[0].title == "測試宿舍區 10/20 停水通知"
    # 日期來自同一列的「日期」欄
    assert [e.published for e in entries] == [
        date(2026, 10, 17),
        date(2026, 10, 16),
        date(2026, 10, 15),
        date(2026, 1, 5),
    ]


def test_parse_list_ignores_links_outside_list_and_other_hosts() -> None:
    urls = [e.url for e in parse_list(fixture("rpage_list_table.html"), LIST_URL)]
    assert detail_url(9999) not in urls  # 頁首跑馬燈
    assert not any("forms.example.org" in u for u in urls)  # 外部連結
    assert not any("other.example.edu" in u for u in urls)  # 他站內頁


def test_parse_list_oia_template() -> None:
    url = "https://oia.ncu.edu.tw/p/403-1026-342.php?Lang=zh-tw"
    entries = parse_list(fixture("rpage_list_oia.html"), url)
    assert [e.post_id for e in entries] == [7002, 7001]
    assert entries[0].title == "【測試】外籍生颱風應變須知"
    assert [e.published for e in entries] == [date(2026, 10, 16), date(2026, 10, 15)]


def test_parse_list_bs_template_deduplicates() -> None:
    url = "https://sec.ncu.edu.tw/p/403-1007-281-1.php?Lang=zh-tw"
    entries = parse_list(fixture("rpage_list_bs.html"), url)
    assert [e.post_id for e in entries] == [8003, 8002]
    assert [e.published for e in entries] == [date(2026, 10, 17), date(2026, 10, 10)]


def test_parse_list_without_dates_leaves_published_empty() -> None:
    html = fixture("rpage_list_bs.html").replace('<i class="mdate after">2026-10-17 </i>', "")
    entries = parse_list(html, "https://sec.ncu.edu.tw/p/403-1007-281-1.php?Lang=zh-tw")
    assert entries[0].published is None
    assert entries[1].published == date(2026, 10, 10)  # 不會誤取其他列的日期


def test_parse_list_without_module_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_list(fixture("rpage_list_no_module.html"), LIST_URL)


def test_parse_list_with_empty_module_raises_layout_changed() -> None:
    html = '<div class="module module-rcglist"><section class="mb"></section></div>'
    with pytest.raises(LayoutChangedError):
        parse_list(html, LIST_URL)


# --- 內頁解析 --------------------------------------------------------------------------


def test_parse_detail_fields() -> None:
    page = parse_detail(fixture("rpage_detail.html"))
    assert page.title == "測試宿舍區 10/20 停水通知"
    assert page.published == date(2026, 10, 17)
    assert page.content_html is not None
    assert "09:00–15:00 停水" in page.content_html
    assert "公告單位" not in page.content_html  # 只取內文區塊


def test_parse_detail_without_body_has_no_content() -> None:
    page = parse_detail(fixture("rpage_detail_no_body.html"))
    assert page.title == "測試講座延期公告"
    assert page.content_html is None


def test_parse_detail_without_title_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_detail(fixture("rpage_detail_no_title.html"))


def test_parse_detail_without_date_has_no_published() -> None:
    # 國際處等網站的內頁不顯示發布日期（2026-10-17 實際擷取時發現），日期改用列表頁
    page = parse_detail(fixture("rpage_detail_no_date.html"))
    assert page.title == "【測試】外籍生颱風應變須知"
    assert page.published is None
    assert page.content_html is not None
    assert "颱風警報期間" in page.content_html
    assert "PtDetail_7002_initOwl" not in page.content_html  # 不含輪播腳本


# --- Adapter：擷取與欄位 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_returns_items_with_fields() -> None:
    items = await make(FakeSite(default_routes())).fetch_since(None)
    assert [i.external_id for i in items] == ["4001", "5001", "5002", "5003"]

    item = items[-1]
    assert item.source_code == "ncu-announcements"
    assert item.url == detail_url(5003)
    assert item.title == "測試宿舍區 10/20 停水通知"
    assert item.content_type == "text/html"
    assert "09:00–15:00 停水" in item.content
    # 只有日期：視為台北時間 00:00，存成 UTC
    assert item.published_at == datetime(2026, 10, 16, 16, 0, tzinfo=UTC)
    assert item.fetched_at == NOW


@pytest.mark.anyio
async def test_item_without_body_uses_title_as_content() -> None:
    items = await make(FakeSite(default_routes())).fetch_since(None)
    item = next(i for i in items if i.external_id == "5002")
    assert item.content == "測試講座延期公告"
    assert item.content_type == "text/plain"


@pytest.mark.anyio
async def test_published_falls_back_to_list_date() -> None:
    routes = default_routes()
    routes[detail_url(5001)] = (200, fixture("rpage_detail_no_date.html"))
    items = await make(FakeSite(routes)).fetch_since(None)
    item = next(i for i in items if i.external_id == "5001")
    # 列表頁上 5001 的日期是 2026-10-15（台北時間）
    assert item.published_at == datetime(2026, 10, 14, 16, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_published_is_none_without_any_date() -> None:
    routes = default_routes()
    routes[LIST_URL] = (
        200,
        fixture("rpage_list_table.html").replace("2026-10-15 ", "—"),
    )
    routes[detail_url(5001)] = (200, fixture("rpage_detail_no_date.html"))
    items = await make(FakeSite(routes)).fetch_since(None)
    item = next(i for i in items if i.external_id == "5001")
    assert item.published_at is None


@pytest.mark.anyio
async def test_cursor_skips_known_posts_without_fetching_them() -> None:
    site = FakeSite(default_routes())
    items = await make(site).fetch_since("5001")
    assert [i.external_id for i in items] == ["5002", "5003"]
    assert detail_url(5001) not in site.urls
    assert detail_url(4001) not in site.urls


@pytest.mark.anyio
async def test_max_items_takes_oldest_new_posts_first() -> None:
    # 先取較舊的，cursor 才能逐步前進而不跳過
    a = make(FakeSite(default_routes()), max_items=2)
    items = await a.fetch_since(None)
    assert [i.external_id for i in items] == ["4001", "5001"]
    assert a.next_cursor(items, None) == "5001"


@pytest.mark.anyio
async def test_next_cursor_is_max_post_id_and_never_goes_back() -> None:
    a = make(FakeSite(default_routes()))
    items = await a.fetch_since(None)
    assert a.next_cursor(items, None) == "5003"
    assert a.next_cursor(items, "6000") == "6000"
    assert a.next_cursor([], "5003") == "5003"
    assert a.next_cursor([], None) is None


@pytest.mark.anyio
@pytest.mark.parametrize("cursor", ["abc", "-1", "2026-10-17T00:00:00+00:00"])
async def test_invalid_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(AdapterError) as exc:
        await make(FakeSite(default_routes())).fetch_since(cursor)
    assert exc.value.retryable is False


@pytest.mark.anyio
async def test_requests_use_project_user_agent() -> None:
    site = FakeSite(default_routes())
    await make(site).fetch_since(None)
    assert site.requests
    assert all(r.headers["user-agent"] == USER_AGENT for r in site.requests)


# --- Adapter：錯誤處理 -----------------------------------------------------------------


@pytest.mark.anyio
async def test_deleted_detail_page_is_skipped() -> None:
    routes = default_routes()
    del routes[detail_url(5002)]  # 404
    a = make(FakeSite(routes))
    items = await a.fetch_since(None)
    assert [i.external_id for i in items] == ["4001", "5001", "5003"]
    assert a.last_skipped == 1


@pytest.mark.anyio
async def test_detail_redirected_to_other_host_is_skipped() -> None:
    routes = default_routes()
    routes[detail_url(5002)] = lambda r: httpx2.Response(
        302, headers={"location": "https://elsewhere.example.org/post"}
    )
    routes["https://elsewhere.example.org/post"] = (200, fixture("rpage_detail.html"))
    a = make(FakeSite(routes))
    items = await a.fetch_since(None)
    assert "5002" not in [i.external_id for i in items]
    assert a.last_skipped == 1


@pytest.mark.anyio
async def test_detail_layout_change_raises() -> None:
    routes = default_routes()
    routes[detail_url(5003)] = (200, fixture("rpage_detail_no_title.html"))
    with pytest.raises(LayoutChangedError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
async def test_list_layout_change_raises() -> None:
    routes = default_routes()
    routes[LIST_URL] = (200, fixture("rpage_list_no_module.html"))
    with pytest.raises(LayoutChangedError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_list_server_errors_are_retryable(status: int) -> None:
    routes = default_routes()
    routes[LIST_URL] = (status, "")
    with pytest.raises(SourceUnavailableError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
async def test_detail_server_error_is_retryable() -> None:
    routes = default_routes()
    routes[detail_url(5001)] = (502, "")
    with pytest.raises(SourceUnavailableError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
async def test_list_client_error_is_not_retryable() -> None:
    routes = default_routes()
    routes[LIST_URL] = (403, "")
    with pytest.raises(AdapterError) as exc:
        await make(FakeSite(routes)).fetch_since(None)
    assert exc.value.retryable is False


@pytest.mark.anyio
async def test_timeout_is_retryable() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("timed out", request=request)

    routes = default_routes()
    routes[LIST_URL] = timeout
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
    routes[ROBOTS_URL] = (200, "User-agent: *\nDisallow: /p/403-\n")
    site = FakeSite(routes)
    with pytest.raises(AdapterError) as exc:
        await make(site).fetch_since(None)
    assert exc.value.retryable is False
    assert LIST_URL not in site.urls


@pytest.mark.anyio
async def test_robots_disallowing_detail_skips_it() -> None:
    routes = default_routes()
    routes[ROBOTS_URL] = (200, "User-agent: *\nDisallow: /p/406-1000-5002\n")
    site = FakeSite(routes)
    a = make(site)
    items = await a.fetch_since(None)
    assert "5002" not in [i.external_id for i in items]
    assert detail_url(5002) not in site.urls
    assert a.last_skipped == 1


@pytest.mark.anyio
async def test_robots_rules_for_our_agent_apply() -> None:
    routes = default_routes()
    routes[ROBOTS_URL] = (200, "User-agent: NCU-Resilience-AI\nDisallow: /\n")
    with pytest.raises(AdapterError):
        await make(FakeSite(routes)).fetch_since(None)


@pytest.mark.anyio
async def test_missing_robots_allows_everything() -> None:
    routes = default_routes()
    del routes[ROBOTS_URL]  # 404
    items = await make(FakeSite(routes)).fetch_since(None)
    assert len(items) == 4


@pytest.mark.anyio
async def test_robots_server_error_is_retryable() -> None:
    routes = default_routes()
    routes[ROBOTS_URL] = (503, "")
    site = FakeSite(routes)
    with pytest.raises(SourceUnavailableError):
        await make(site).fetch_since(None)
    assert LIST_URL not in site.urls


@pytest.mark.anyio
async def test_robots_is_fetched_once_per_host() -> None:
    site = FakeSite(default_routes())
    a = make(site)
    await a.fetch_since(None)
    await a.fetch_since("5003")
    assert site.urls.count(ROBOTS_URL) == 1


# --- 限速 ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_requests_to_same_host_are_spaced_by_min_interval() -> None:
    t = FakeTime()
    site = FakeSite(default_routes())
    await make(site, t).fetch_since(None)
    # robots + 列表 + 4 篇內頁 = 6 個請求，之間有 5 次等待，每次 5 秒（假時鐘不會自己前進）
    assert len(site.requests) == 6
    assert t.sleeps == [5.0] * 5


@pytest.mark.anyio
async def test_limiter_waits_only_for_remaining_interval() -> None:
    t = FakeTime()
    limiter = HostRateLimiter(clock=t.clock, sleep=t.sleep)
    await limiter.wait("a.example.edu", 5.0)
    t.now += 2.0
    await limiter.wait("a.example.edu", 5.0)
    assert t.sleeps == [3.0]


@pytest.mark.anyio
async def test_limiter_tracks_hosts_independently() -> None:
    t = FakeTime()
    limiter = HostRateLimiter(clock=t.clock, sleep=t.sleep)
    await limiter.wait("a.example.edu", 5.0)
    await limiter.wait("b.example.edu", 5.0)
    assert t.sleeps == []
    t.now += 10.0
    await limiter.wait("a.example.edu", 5.0)
    assert t.sleeps == []


# --- 多個列表頁（同一網站的多個公告分類） ---------------------------------------------

SECOND_LIST_URL = "https://www.ncu.edu.tw/p/403-1000-16-1.php?Lang=zh-tw"
SECOND_LIST = """
<div class="module module-rcglist"><section class="mb"><div class="row listBS">
  <div class="d-item"><div class="mtitle">
    <a href="https://www.ncu.edu.tw/p/406-1000-5004,r16.php?Lang=zh-tw">另一分類的新公告</a>
    <i class="mdate after">2026-10-17 </i></div></div>
  <div class="d-item"><div class="mtitle">
    <a href="https://www.ncu.edu.tw/p/406-1000-5003,r16.php?Lang=zh-tw">兩個分類都有的公告</a>
    <i class="mdate after">2026-10-17 </i></div></div>
</div></section></div>
"""


def multi_routes() -> dict[str, Route]:
    routes = default_routes()
    routes[SECOND_LIST_URL] = (200, SECOND_LIST)
    routes["https://www.ncu.edu.tw/p/406-1000-5004,r16.php?Lang=zh-tw"] = (
        200,
        fixture("rpage_detail.html"),
    )
    return routes


def make_multi(site: FakeSite, **options: Any) -> RPageAdapter:
    cfg = SourceConfig(
        code="ncu-aa",
        type="rpage",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={"list_urls": [LIST_URL, SECOND_LIST_URL], **options},
    )
    t = FakeTime()
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(site))
    limiter = HostRateLimiter(clock=t.clock, sleep=t.sleep)
    return RPageAdapter(cfg, client=client, limiter=limiter, clock=lambda: NOW)


@pytest.mark.anyio
async def test_multiple_lists_are_merged_and_deduplicated() -> None:
    site = FakeSite(multi_routes())
    a = make_multi(site)
    items = await a.fetch_since("5001")
    # 5003 兩個分類都有，只抓一次（取先出現的列表頁網址）；5004 只在第二個分類
    assert [i.external_id for i in items] == ["5002", "5003", "5004"]
    assert LIST_URL in site.urls and SECOND_LIST_URL in site.urls
    assert sum("406-1000-5003" in u for u in site.urls) == 1
    assert a.next_cursor(items, "5001") == "5004"


@pytest.mark.anyio
async def test_layout_change_in_any_list_raises() -> None:
    routes = multi_routes()
    routes[SECOND_LIST_URL] = (200, fixture("rpage_list_no_module.html"))
    with pytest.raises(LayoutChangedError):
        await make_multi(FakeSite(routes)).fetch_since(None)


# --- 設定與 registry -------------------------------------------------------------------


def test_rpage_type_is_registered() -> None:
    assert "rpage" in registry.types()
    created = registry.create(config())
    assert isinstance(created, RPageAdapter)
    assert isinstance(created, SourceAdapter)


@pytest.mark.parametrize(
    "options",
    [
        {"list_url": "ftp://www.ncu.edu.tw/list"},
        {"list_url": LIST_URL, "min_interval": 1},  # 低於共通擷取原則的 5 秒
        {"list_url": LIST_URL, "max_items": 0},
        {"list_url": LIST_URL, "max_items": 51},
        {"list_url": LIST_URL, "timeout": 0},
        {"list_url": LIST_URL, "unknown": True},
        {},
        {"list_urls": []},
        {"list_url": LIST_URL, "list_urls": [LIST_URL]},  # 只能擇一
        # cursor 是站台內的文章編號，所有列表頁必須在同一網域
        {"list_urls": [LIST_URL, "https://sec.ncu.edu.tw/p/403-1007-281-1.php"]},
        {"list_urls": [LIST_URL, "ftp://www.ncu.edu.tw/list"]},
    ],
)
def test_invalid_options_are_rejected(options: dict[str, Any]) -> None:
    cfg = SourceConfig(
        code="ncu-announcements",
        type="rpage",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options=options,
    )
    with pytest.raises(ValueError):
        RPageAdapter(cfg)
