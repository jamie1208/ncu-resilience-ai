"""P1-T16：學務處生活輔導組公告 Adapter（ncu-osa-life；快照解析，CI 不連外）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingestion.adapters.ncu_osa_life import (
    LIST_URL,
    NcuOsaLifeAdapter,
    parse_detail,
    parse_list,
)
from app.ingestion.base import LayoutChangedError, SourceAdapter
from app.ingestion.http import HostRateLimiter
from app.ingestion.registry import registry
from app.ingestion.types import AccessPolicy, SourceConfig

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "ncu_osa_life"
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
SITE = "https://military.ncu.edu.tw"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def detail_url(n: int) -> str:
    return f"{SITE}/post_detail.php?no={n}"


def config(**options: Any) -> SourceConfig:
    return SourceConfig(
        code="ncu-osa-life",
        type="ncu_osa_life",
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


def make(site: Site) -> NcuOsaLifeAdapter:
    async def no_sleep(_: float) -> None:
        return None

    return NcuOsaLifeAdapter(
        config(),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(site)),
        limiter=HostRateLimiter(sleep=no_sleep),
        clock=lambda: NOW,
    )


def test_registered_as_ncu_osa_life_type() -> None:
    adapter = registry.create(config())
    assert isinstance(adapter, NcuOsaLifeAdapter)
    assert isinstance(adapter, SourceAdapter)


def test_parse_list_rows_including_pinned() -> None:
    entries = parse_list(fixture("list.html"), LIST_URL, NOW)
    assert [(e.key, e.published) for e in entries] == [
        ("1782", date(2026, 9, 29)),
        ("1790", date(2026, 10, 16)),
        ("1785", date(2026, 10, 14)),
    ]
    assert entries[1].url == detail_url(1790)
    assert entries[1].title == "宵夜街周邊機車失竊提醒"


def test_list_without_rows_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_list(fixture("list_no_items.html"), LIST_URL, NOW)


def test_parse_detail_fields() -> None:
    detail = parse_detail(fixture("detail.html"))
    assert detail.title == "宵夜街周邊機車失竊提醒"
    assert detail.published == datetime(2026, 10, 15, 16, 0, tzinfo=UTC)
    assert detail.content_html is not None
    assert "機車失竊" in detail.content_html
    assert "example.pdf" not in detail.content_html  # 附加檔案不算內文
    assert "csrf" not in detail.content_html


def test_parse_detail_without_body() -> None:
    detail = parse_detail(fixture("detail_no_body.html"))
    assert detail.title == "中大路口交通安全宣導"
    assert detail.content_html is None


def test_parse_detail_without_title_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_detail(fixture("detail_no_title.html"))


@pytest.mark.anyio
async def test_fetch_end_to_end_orders_by_number_not_page_position() -> None:
    site = Site(
        {
            f"{SITE}/robots.txt": (404, ""),
            LIST_URL: (200, fixture("list.html")),
            detail_url(1782): (200, fixture("detail.html")),
            detail_url(1785): (200, fixture("detail_no_body.html")),
            detail_url(1790): (200, fixture("detail.html")),
        }
    )
    adapter = make(site)
    items = await adapter.fetch_since("1782")  # 置頂的 1782 已經抓過
    assert [i.external_id for i in items] == ["1785", "1790"]
    assert items[0].content_type == "text/plain"
    assert adapter.next_cursor(items, "1782") == "1790"
