"""P1-T16：環安中心最新消息 Adapter（ncu-epsc；快照解析，CI 不連外）。

文章編號是 hex 字串、列表也不依時間排序，所以 cursor 改用「最新發布日期｜當日已抓的編號」。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingestion.adapters.ncu_epsc import LIST_URL, NcuEpscAdapter, parse_detail, parse_list
from app.ingestion.base import AdapterError, LayoutChangedError, SourceAdapter
from app.ingestion.http import HostRateLimiter
from app.ingestion.registry import registry
from app.ingestion.types import AccessPolicy, SourceConfig

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "ncu_epsc"
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
SITE = "https://ncu.edu.tw"
A, B, C, D = (f"{x * 4}{'0' * 31}{x}" for x in "abcd")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def detail_url(key: str) -> str:
    return f"{SITE}/epsc/index.php/index/News/News_more.html?id={key}"


def config(**options: Any) -> SourceConfig:
    return SourceConfig(
        code="ncu-epsc",
        type="ncu_epsc",
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


def site() -> Site:
    return Site(
        {
            f"{SITE}/robots.txt": (200, "User-agent: *\nDisallow: /cgi-bin/\n"),
            LIST_URL: (200, fixture("list.html")),
            detail_url(A): (200, fixture("detail.html")),
            detail_url(B): (200, fixture("detail.html")),
            detail_url(C): (200, fixture("detail_no_body.html")),
            detail_url(D): (200, fixture("detail.html")),
        }
    )


def make(s: Site, **options: Any) -> NcuEpscAdapter:
    async def no_sleep(_: float) -> None:
        return None

    return NcuEpscAdapter(
        config(**options),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(s)),
        limiter=HostRateLimiter(sleep=no_sleep),
        clock=lambda: NOW,
    )


def test_registered_as_ncu_epsc_type() -> None:
    adapter = registry.create(config())
    assert isinstance(adapter, NcuEpscAdapter)
    assert isinstance(adapter, SourceAdapter)


def test_parse_list() -> None:
    entries = parse_list(fixture("list.html"), LIST_URL, NOW)
    assert [(e.key, e.published) for e in entries] == [
        (A, date(2025, 4, 14)),
        (B, date(2026, 10, 16)),
        (C, date(2026, 10, 16)),
        (D, date(2026, 9, 24)),
    ]
    assert entries[1].url == detail_url(B)
    assert entries[1].title == "科學三館消防設備檢修公告"


def test_list_without_items_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_list(fixture("list_no_items.html"), LIST_URL, NOW)


def test_parse_detail_fields() -> None:
    detail = parse_detail(fixture("detail.html"))
    assert detail.title == "科學三館消防設備檢修公告"
    assert detail.published == datetime(2026, 10, 15, 16, 0, tzinfo=UTC)
    assert detail.content_html is not None and "消防設備檢修" in detail.content_html
    assert "友善列印" not in detail.content_html


def test_parse_detail_without_body() -> None:
    assert parse_detail(fixture("detail_no_body.html")).content_html is None


def test_parse_detail_without_title_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        parse_detail(fixture("detail_no_title.html"))


@pytest.mark.anyio
async def test_first_run_takes_oldest_first_and_sets_date_cursor() -> None:
    adapter = make(site())
    items = await adapter.fetch_since(None)
    assert [i.external_id for i in items] == [A, D, B, C]  # 依日期，同日依編號
    assert adapter.next_cursor(items, None) == f"2026-10-16|{B},{C}"


@pytest.mark.anyio
async def test_cursor_skips_older_dates_and_known_ids_on_same_day() -> None:
    s = site()
    adapter = make(s)
    assert await adapter.fetch_since(f"2026-10-16|{B},{C}") == []
    assert all("News_more" not in u for u in s.urls)
    items = await adapter.fetch_since(f"2026-10-16|{B}")
    assert [i.external_id for i in items] == [C]
    assert adapter.next_cursor(items, f"2026-10-16|{B}") == f"2026-10-16|{B},{C}"


@pytest.mark.anyio
async def test_max_items_keeps_cursor_at_last_fetched() -> None:
    adapter = make(site(), max_items=2)
    items = await adapter.fetch_since(None)
    assert [i.external_id for i in items] == [A, D]
    cursor = adapter.next_cursor(items, None)
    assert cursor == f"2026-09-24|{D}"
    rest = await adapter.fetch_since(cursor)
    assert [i.external_id for i in rest] == [B, C]


def test_next_cursor_without_items_keeps_previous() -> None:
    adapter = make(site())
    assert adapter.next_cursor([], "2026-10-16|x") == "2026-10-16|x"


@pytest.mark.parametrize("cursor", ["5", "2026-13-01|a", "2026-10-16", "2026-10-16|A B"])
@pytest.mark.anyio
async def test_invalid_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(AdapterError):
        await make(site()).fetch_since(cursor)
