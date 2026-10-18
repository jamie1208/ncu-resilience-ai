"""P1-T16：圖書館最新消息 Adapter（ncu-lib；快照解析，CI 不連外）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingestion.adapters.ncu_lib import LIST_URL, NcuLibAdapter, parse_detail, parse_list
from app.ingestion.base import LayoutChangedError, SourceAdapter
from app.ingestion.http import HostRateLimiter
from app.ingestion.registry import registry
from app.ingestion.types import AccessPolicy, SourceConfig

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "ncu_lib"
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)  # 台北 2026-10-18 12:00
SITE = "https://www.lib.ncu.edu.tw"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def detail_url(n: int) -> str:
    return f"{SITE}/libboard/news_content.php?NewsID={n}"


def config(**options: Any) -> SourceConfig:
    return SourceConfig(
        code="ncu-lib",
        type="ncu_lib",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={"list_url": LIST_URL, **options},
    )


class Site:
    def __init__(self, routes: dict[str, tuple[int, str]]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.urls.append(str(request.url))
        status, body = self.routes.get(str(request.url), (404, "not found"))
        return httpx2.Response(
            status, text=body, headers={"content-type": "text/html; charset=utf-8"}
        )


def make(site: Site) -> NcuLibAdapter:
    async def no_sleep(_: float) -> None:
        return None

    return NcuLibAdapter(
        config(),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(site)),
        limiter=HostRateLimiter(sleep=no_sleep),
        clock=lambda: NOW,
    )


def test_registered_as_ncu_lib_type() -> None:
    adapter = registry.create(config())
    assert isinstance(adapter, NcuLibAdapter)
    assert isinstance(adapter, SourceAdapter)


def test_parse_list_uses_base_href_and_skips_menu_links() -> None:
    entries = parse_list(fixture("list.html"), LIST_URL, NOW)
    assert [(e.key, e.url) for e in entries] == [
        ("7460", detail_url(7460)),
        ("7459", detail_url(7459)),
        ("7400", detail_url(7400)),
    ]  # 選單中的 6294、6623 與他站連結不算
    assert entries[0].title == "總圖書館 B1 自習室冷氣維修暫停開放"


def test_list_dates_get_the_year_from_now() -> None:
    entries = {e.key: e for e in parse_list(fixture("list.html"), LIST_URL, NOW)}
    assert entries["7460"].published == date(2026, 10, 16)
    assert entries["7400"].published == date(2025, 12, 30)  # 12/30 晚於今天 → 去年


def test_list_without_rows_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_list(fixture("list_no_items.html"), LIST_URL, NOW)


def test_parse_detail_fields() -> None:
    detail = parse_detail(fixture("detail.html"))
    assert detail.title == "總圖書館 B1 自習室冷氣維修暫停開放"
    assert detail.published == datetime(2026, 10, 16, 1, 30, tzinfo=UTC)
    assert detail.content_html is not None
    assert "冷氣維修" in detail.content_html
    for noise in ("發表單位", "發表日期", "典閱組", "mailto", "<style", "<h1"):
        assert noise not in detail.content_html


def test_parse_detail_without_date() -> None:
    detail = parse_detail(fixture("detail_no_date.html"))
    assert detail.published is None
    assert detail.content_html is not None and "閉館盤點" in detail.content_html


def test_parse_detail_without_title_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_detail(fixture("detail_no_title.html"))


@pytest.mark.anyio
async def test_fetch_end_to_end() -> None:
    site = Site(
        {
            f"{SITE}/robots.txt": (404, ""),
            LIST_URL: (200, fixture("list.html")),
            detail_url(7460): (200, fixture("detail.html")),
            detail_url(7459): (200, fixture("detail.html")),
            detail_url(7400): (200, fixture("detail_no_date.html")),
        }
    )
    adapter = make(site)
    items = await adapter.fetch_since("7399")
    assert [i.external_id for i in items] == ["7400", "7459", "7460"]
    assert items[0].published_at == datetime(2025, 12, 29, 16, 0, tzinfo=UTC)  # 列表日期
    assert adapter.next_cursor(items, "7399") == "7460"
    assert await make(site).fetch_since("7460") == []
