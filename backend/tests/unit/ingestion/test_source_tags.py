"""P1-T15：來源分類標籤（RawItem.tags → NormalizedItem.tags；各 Adapter 產出 tags）。

既有的 Adapter 測試不修改；這裡只檢查新增的 tags 行為（使用者 2026-10-18 決定 A、C）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from app.ingestion.adapters.ncu_announcements import RPageAdapter
from app.ingestion.adapters.ncu_news import NcuNewsAdapter
from app.ingestion.adapters.ncu_osa_life import NcuOsaLifeAdapter
from app.ingestion.http import HostRateLimiter
from app.ingestion.normalize import normalize_item
from app.ingestion.types import AccessPolicy, RawItem, SourceConfig

HTML = Path(__file__).resolve().parents[2] / "fixtures" / "html"

# --- 共用的假網站（不從其他測試檔匯入，避免 mypy 模組路徑衝突） ---------------------


def fixture(name: str) -> str:
    return (HTML / name).read_text(encoding="utf-8")


class Site:
    """以網址對應回應的假網站；未登記的網址回 404。"""

    def __init__(self, routes: dict[str, tuple[int, str]]) -> None:
        self.routes = routes

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        status, body = self.routes.get(str(request.url), (404, "not found"))
        return httpx2.Response(
            status, text=body, headers={"content-type": "text/html; charset=utf-8"}
        )


async def no_sleep(_: float) -> None:
    return None


def client(routes: dict[str, tuple[int, str]]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(Site(routes)))


def source(code: str, type_: str, **options: Any) -> SourceConfig:
    return SourceConfig(
        code=code, type=type_, access_policy=AccessPolicy.APPROVED, enabled=True, options=options
    )


NEWS = "https://ncusec.ncu.edu.tw/news"
HEADLINES, FOCUS, ACADEMIC = (
    f"{NEWS}/{n}.php" for n in ("headlines", "focus-news", "academic-news")
)


def news_detail(n: int) -> str:
    return f"{NEWS}/headlines_content.php?H_ID={n}"


def news_routes() -> dict[str, tuple[int, str]]:
    return {
        "https://ncusec.ncu.edu.tw/robots.txt": (200, fixture("ncu_news/robots_html.html")),
        HEADLINES: (200, fixture("ncu_news/headlines.html")),
        FOCUS: (200, fixture("ncu_news/focus_news.html")),
        ACADEMIC: (200, fixture("ncu_news/academic_news.html")),
        news_detail(9003): (200, fixture("ncu_news/detail.html")),
        news_detail(9002): (200, fixture("ncu_news/detail_no_body.html")),
        news_detail(9001): (200, fixture("ncu_news/detail_no_date.html")),
        news_detail(8001): (200, fixture("ncu_news/detail.html")),
    }


LIST_URL = "https://www.ncu.edu.tw/p/403-1000-15-1.php?Lang=zh-tw"
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


def rpage_detail(n: int, r: int = 15) -> str:
    return f"https://www.ncu.edu.tw/p/406-1000-{n},r{r}.php?Lang=zh-tw"


def rpage_routes() -> dict[str, tuple[int, str]]:
    return {
        "https://www.ncu.edu.tw/robots.txt": (200, "User-agent: *\nAllow: /\n"),
        LIST_URL: (200, fixture("rpage_list_table.html")),
        SECOND_LIST_URL: (200, SECOND_LIST),
        rpage_detail(5003): (200, fixture("rpage_detail.html")),
        rpage_detail(5002): (200, fixture("rpage_detail_no_body.html")),
        rpage_detail(5001): (200, fixture("rpage_detail.html")),
        rpage_detail(4001): (200, fixture("rpage_detail.html")),
        rpage_detail(5004, 16): (200, fixture("rpage_detail.html")),
    }


OSA = "https://military.ncu.edu.tw"


pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)


def raw(**overrides: Any) -> RawItem:
    fields: dict[str, Any] = {
        "source_code": "demo",
        "external_id": "1",
        "url": "https://demo.ncu.edu.tw/1",
        "content": "停電通知",
        "fetched_at": NOW,
    }
    fields.update(overrides)
    return RawItem(**fields)


# --- RawItem 與標準化 -------------------------------------------------------------------


def test_tags_default_to_empty() -> None:
    assert raw().tags == frozenset()


def test_tags_are_kept_and_normalized() -> None:
    item = normalize_item(raw(tags=frozenset({" 宿舍網路 ", "資安訊息"})))
    assert item.tags == frozenset({"宿舍網路", "資安訊息"})


def test_tags_do_not_change_content_hash() -> None:
    assert (
        normalize_item(raw(tags=frozenset({"a"}))).content_hash
        == normalize_item(raw()).content_hash
    )


@pytest.mark.parametrize(
    "tags",
    [
        frozenset({""}),
        frozenset({"   "}),
        frozenset({"長" * 51}),
        frozenset(f"t{i}" for i in range(21)),
    ],
)
def test_invalid_tags_are_rejected(tags: frozenset[str]) -> None:
    with pytest.raises(ValidationError):
        raw(tags=tags)


# --- 新聞網：分類寫入 tags，categories 保留為舊介面 ---------------------------------------


async def test_news_categories_become_tags() -> None:
    adapter = NcuNewsAdapter(
        source("ncu-news", "ncu_news", list_urls=[HEADLINES, FOCUS, ACADEMIC]),
        client=client(news_routes()),
        limiter=HostRateLimiter(sleep=no_sleep),
        clock=lambda: NOW,
    )
    items = await adapter.fetch_since(None)
    tags = {i.external_id: i.tags for i in items}
    assert tags == {
        "8001": frozenset({"學術新聞"}),
        "9001": frozenset({"焦點新聞"}),
        "9002": frozenset({"學術新聞"}),
        "9003": frozenset({"焦點新聞"}),
    }
    assert adapter.categories == tags  # 舊介面由 tags 推算


# --- RPage：列表標籤 --------------------------------------------------------------------


def rpage(options: dict[str, Any]) -> RPageAdapter:
    return RPageAdapter(
        source("ncu-cc", "rpage", **options),
        client=client(rpage_routes()),
        limiter=HostRateLimiter(sleep=no_sleep),
        clock=lambda: NOW,
    )


async def test_rpage_list_tags_merge_across_lists() -> None:
    adapter = rpage(
        {
            "list_urls": [
                {"url": LIST_URL, "tag": "系統維護"},
                {"url": SECOND_LIST_URL, "tag": "宿舍網路"},
            ]
        }
    )
    items = await adapter.fetch_since("5001")
    tags = {i.external_id: i.tags for i in items}
    assert tags["5003"] == frozenset({"系統維護", "宿舍網路"})  # 兩個列表都有
    assert tags["5004"] == frozenset({"宿舍網路"})


async def test_rpage_plain_and_tagged_urls_can_mix() -> None:
    adapter = rpage({"list_urls": [LIST_URL, {"url": SECOND_LIST_URL, "tag": "宿舍網路"}]})
    items = await adapter.fetch_since("5001")
    tags = {i.external_id: i.tags for i in items}
    assert tags["5002"] == frozenset()
    assert tags["5003"] == frozenset({"宿舍網路"})


async def test_rpage_without_tags_produces_empty_tags() -> None:
    items = await rpage({"list_urls": [LIST_URL, SECOND_LIST_URL]}).fetch_since("5001")
    assert all(i.tags == frozenset() for i in items)


@pytest.mark.parametrize(
    "entry",
    [{"url": LIST_URL}, {"url": LIST_URL, "tag": ""}, {"url": LIST_URL, "tag": "x", "extra": 1}],
)
def test_invalid_rpage_list_entries_are_rejected(entry: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        rpage({"list_urls": [entry]})


# --- 生輔組：公告類別 -------------------------------------------------------------------


async def test_osa_life_category_becomes_tag() -> None:
    routes = {
        f"{OSA}/robots.txt": (404, ""),
        f"{OSA}/": (200, fixture("ncu_osa_life/list.html")),
        f"{OSA}/post_detail.php?no=1785": (200, fixture("ncu_osa_life/detail_no_body.html")),
        f"{OSA}/post_detail.php?no=1790": (200, fixture("ncu_osa_life/detail.html")),
    }
    adapter = NcuOsaLifeAdapter(
        source("ncu-osa-life", "ncu_osa_life", list_url=f"{OSA}/"),
        client=client(routes),
        limiter=HostRateLimiter(sleep=no_sleep),
        clock=lambda: NOW,
    )
    items = await adapter.fetch_since("1782")
    assert {i.external_id: i.tags for i in items} == {
        "1785": frozenset({"交通安全"}),
        "1790": frozenset({"校園安全"}),
    }
