"""P1-T02：RSS／Atom Adapter（固定 fixture，不連網）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingestion.adapters.rss import USER_AGENT, RssAdapter
from app.ingestion.base import (
    AdapterError,
    LayoutChangedError,
    SourceAdapter,
    SourceUnavailableError,
)
from app.ingestion.registry import registry
from app.ingestion.types import AccessPolicy, RawItem, SourceConfig

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "rss"
FEED_URL = "https://news.example.edu/rss.xml"
NOW = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)

pytestmark = pytest.mark.anyio

Handler = Callable[[httpx2.Request], httpx2.Response]


def config(**options: Any) -> SourceConfig:
    return SourceConfig(
        code="test-feed",
        type="rss",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={"url": FEED_URL, **options},
    )


def serve(name: str, *, content_type: str = "application/rss+xml") -> Handler:
    body = (FIXTURES / name).read_bytes()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": content_type})

    return handler


def adapter(handler: Handler, **options: Any) -> RssAdapter:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return RssAdapter(config(**options), client=client, clock=lambda: NOW)


def by_url(items: list[RawItem]) -> dict[str, RawItem]:
    return {i.url: i for i in items}


# --- RSS 2.0 ---------------------------------------------------------------------------


async def test_rss2_parses_count_and_fields() -> None:
    items = await adapter(serve("rss2_basic.xml")).fetch_since(None)
    assert len(items) == 3
    got = by_url(items)

    first = got["https://news.example.edu/posts/101"]
    assert first.source_code == "test-feed"
    assert first.external_id == "post-101"
    assert first.title == "宿舍區 10/18 停電通知"
    assert "宿舍區 10/18 08:00–12:00 停電" in first.content
    # +0800 轉為 UTC
    assert first.published_at == datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert first.fetched_at == NOW


async def test_rss2_prefers_full_content_over_summary() -> None:
    items = await adapter(serve("rss2_basic.xml")).fetch_since(None)
    item = by_url(items)["https://news.example.edu/posts/102"]
    assert item.content_type == "text/html"
    assert "全日閉館" in item.content
    assert "摘要文字" not in item.content
    assert item.external_id == "https://news.example.edu/posts/102"


async def test_rss2_without_guid_uses_link_as_external_id() -> None:
    items = await adapter(serve("rss2_basic.xml")).fetch_since(None)
    item = by_url(items)["https://news.example.edu/posts/103"]
    assert item.external_id == "https://news.example.edu/posts/103"
    assert item.published_at == datetime(2026, 10, 16, 1, 0, tzinfo=UTC)


# --- Atom ------------------------------------------------------------------------------


async def test_atom_parses_count_and_fields() -> None:
    items = await adapter(serve("atom_basic.xml", content_type="application/atom+xml")).fetch_since(
        None
    )
    assert len(items) == 2
    got = by_url(items)

    mail = got["https://cc.example.edu/news/11"]
    assert mail.external_id == "urn:uuid:7f3e0c55-0000-4000-8000-000000000011"
    assert mail.title == "郵件系統暫停服務"
    assert mail.content_type == "text/html"
    assert "暫停服務 2 小時" in mail.content
    # 有 published 時優先於 updated
    assert mail.published_at == datetime(2026, 10, 17, 0, 0, tzinfo=UTC)

    vpn = got["https://cc.example.edu/news/12"]
    assert vpn.content_type == "text/plain"
    assert vpn.content == "請於 10/31 前更新 VPN 憑證。"
    # 沒有 published 時退回 updated
    assert vpn.published_at == datetime(2026, 10, 16, 0, 0, tzinfo=UTC)


# --- 欄位缺漏與版型改變 ----------------------------------------------------------------


async def test_entries_missing_required_fields_are_skipped() -> None:
    a = adapter(serve("missing_fields.xml"))
    items = await a.fetch_since(None)
    assert sorted(i.url for i in items) == [
        "https://news.example.edu/posts/201",
        "https://news.example.edu/posts/203",
        "https://news.example.edu/posts/204",
    ]
    # 沒有連結、非 http 連結、沒有任何文字的項目：略過並計數
    assert a.last_skipped == 3


async def test_entry_without_date_has_no_published_at() -> None:
    items = await adapter(serve("missing_fields.xml")).fetch_since(None)
    assert by_url(items)["https://news.example.edu/posts/203"].published_at is None


async def test_title_only_entry_uses_title_as_content() -> None:
    items = await adapter(serve("missing_fields.xml")).fetch_since(None)
    item = by_url(items)["https://news.example.edu/posts/204"]
    assert item.content == "只有標題的公告"


async def test_all_entries_invalid_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        await adapter(serve("all_invalid.xml")).fetch_since(None)


async def test_html_page_instead_of_feed_raises_layout_changed() -> None:
    with pytest.raises(LayoutChangedError):
        await adapter(serve("not_a_feed.html", content_type="text/html")).fetch_since(None)


async def test_empty_feed_returns_no_items() -> None:
    a = adapter(serve("empty_channel.xml"))
    assert await a.fetch_since(None) == []
    assert a.last_skipped == 0


async def test_external_entities_are_not_expanded() -> None:
    items = await adapter(serve("xxe.xml")).fetch_since(None)
    assert len(items) == 1
    assert "root:" not in items[0].content


# --- 增量擷取 --------------------------------------------------------------------------


async def test_fetch_since_cursor_filters_older_items() -> None:
    a = adapter(serve("rss2_basic.xml"))
    items = await a.fetch_since("2026-10-17T11:00:00+00:00")
    assert [i.url for i in items] == ["https://news.example.edu/posts/101"]


async def test_cursor_boundary_is_inclusive() -> None:
    # 同一秒發布的項目不能漏掉；重複由冪等寫入（P1-T06）處理
    items = await adapter(serve("rss2_basic.xml")).fetch_since("2026-10-17T12:00:00+00:00")
    assert [i.url for i in items] == ["https://news.example.edu/posts/101"]


async def test_items_without_date_are_always_returned() -> None:
    items = await adapter(serve("missing_fields.xml")).fetch_since("2030-01-01T00:00:00+00:00")
    assert [i.url for i in items] == ["https://news.example.edu/posts/203"]


async def test_next_cursor_is_latest_published_time() -> None:
    a = adapter(serve("rss2_basic.xml"))
    items = await a.fetch_since(None)
    assert a.next_cursor(items, None) == "2026-10-17T12:00:00+00:00"
    # 沒有新項目時保留原 cursor，也不會倒退
    assert a.next_cursor([], "2026-10-18T00:00:00+00:00") == "2026-10-18T00:00:00+00:00"
    assert a.next_cursor(items, "2026-10-18T00:00:00+00:00") == "2026-10-18T00:00:00+00:00"


async def test_invalid_cursor_is_rejected() -> None:
    with pytest.raises(AdapterError) as exc:
        await adapter(serve("rss2_basic.xml")).fetch_since("昨天")
    assert exc.value.retryable is False


async def test_naive_cursor_is_rejected() -> None:
    with pytest.raises(AdapterError):
        await adapter(serve("rss2_basic.xml")).fetch_since("2026-10-17T12:00:00")


# --- HTTP 行為 -------------------------------------------------------------------------


async def test_request_sends_project_user_agent() -> None:
    seen: list[httpx2.Request] = []
    inner = serve("rss2_basic.xml")

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return inner(request)

    await adapter(handler).fetch_since(None)
    assert len(seen) == 1
    assert str(seen[0].url) == FEED_URL
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert "NCU-Resilience-AI" in USER_AGENT


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_server_errors_are_retryable(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status)

    with pytest.raises(SourceUnavailableError):
        await adapter(handler).fetch_since(None)


@pytest.mark.parametrize("status", [403, 404, 410])
async def test_client_errors_are_not_retryable(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status)

    with pytest.raises(AdapterError) as exc:
        await adapter(handler).fetch_since(None)
    assert exc.value.retryable is False


async def test_timeout_is_retryable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("timed out", request=request)

    with pytest.raises(SourceUnavailableError):
        await adapter(handler).fetch_since(None)


async def test_connection_error_is_retryable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TransportError("connection refused")

    with pytest.raises(SourceUnavailableError):
        await adapter(handler).fetch_since(None)


async def test_oversized_response_is_rejected() -> None:
    with pytest.raises(AdapterError) as exc:
        await adapter(serve("rss2_basic.xml"), max_bytes=100).fetch_since(None)
    assert exc.value.retryable is False


# --- 設定與 registry -------------------------------------------------------------------


def test_rss_type_is_registered() -> None:
    assert "rss" in registry.types()
    created = registry.create(config())
    assert isinstance(created, RssAdapter)
    assert isinstance(created, SourceAdapter)
    assert created.code == "test-feed"


@pytest.mark.parametrize(
    "options",
    [
        {"url": None},
        {"url": "ftp://news.example.edu/rss.xml"},
        {"url": "file:///etc/passwd"},
        {"url": FEED_URL, "timeout": 0},
        {"url": FEED_URL, "max_bytes": 0},
        {"url": FEED_URL, "unknown": 1},
    ],
)
def test_invalid_options_are_rejected(options: dict[str, Any]) -> None:
    cfg = SourceConfig(
        code="test-feed",
        type="rss",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={k: v for k, v in options.items() if v is not None},
    )
    with pytest.raises(ValueError):
        RssAdapter(cfg)
