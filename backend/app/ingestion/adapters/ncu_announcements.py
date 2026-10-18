"""中央大學 RPage 公告 Adapter（P1-T03），來源類型 `rpage`。

校園公告、秘書室、國際處等網站都是 RPage 系統：列表頁連到 `/p/406-<站台>-<文章編號>,r<分類>.php`
內頁。列表版型不只一種（表格、listBS），共通點是公告連結位於 `.module-rcglist .mtitle a`；
內頁則固定為 `.module-detail` 中的 `h2.hdline`、`.ptinfoproperty_date`、`.meditor`。

擷取原則（docs/governance/source-inventory.md「共通擷取原則」）：
- 每個請求前檢查 robots.txt（每個網域每個 Adapter 實例抓一次）；
- 同一網域請求間隔 ≥ 5 秒（`HostRateLimiter`），User-Agent 標示專案；
- 只跟隨同一網域的內頁連結。

cursor 是已擷取的最大文章編號。每輪只抓編號更大的內頁，且由舊到新最多 `max_items` 篇，
讓 cursor 逐步前進而不跳過。發布日期優先取內頁，內頁不顯示時取列表頁同一列的日期；
只有日期，發布時間視為台北時間當日 00:00。兩處都沒有日期時 published_at 為 None。

來源設定（`SourceConfig.options`）：
- `list_url` 或 `list_urls`（擇一必填）：列表頁 http(s) 網址；同一網站的多個公告分類用
  `list_urls`，必須在同一網域（文章編號是站台內共用的流水號，才能共用 cursor）
- `max_items`：每輪最多抓幾篇內頁（1–50，預設 20）
- `min_interval`：同網域請求間隔秒數（≥ 5，預設 5）
- `timeout`：單次請求秒數（預設 15）；`max_bytes`：單頁大小上限（預設 2 MB）
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Self
from urllib.parse import urljoin, urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, model_validator
from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.ingestion.base import AdapterError, LayoutChangedError
from app.ingestion.http import MIN_REQUEST_INTERVAL, HostRateLimiter, PageFetcher, shared_limiter
from app.ingestion.registry import registry
from app.ingestion.types import RawItem, SourceConfig

TAIPEI = timezone(timedelta(hours=8))  # 台灣沒有日光節約時間
_DETAIL_PATH = re.compile(r"^/p/406-\d+-(?P<post>\d+),r\d+\.php$")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


class RPageOptions(BaseModel):
    """RPage 來源的設定；無效值在建立 Adapter 時拋出 ValueError。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    list_url: str | None = None
    list_urls: tuple[str, ...] | None = None
    max_items: int = Field(default=20, ge=1, le=50)
    min_interval: float = Field(default=MIN_REQUEST_INTERVAL, ge=MIN_REQUEST_INTERVAL)
    timeout: float = Field(default=15.0, gt=0, le=60)
    max_bytes: int = Field(default=2_000_000, gt=0)

    @model_validator(mode="after")
    def _check_lists(self) -> Self:
        urls = self.lists
        if not urls:
            raise ValueError("必須設定 list_url 或 list_urls 其中之一")
        if self.list_url is not None and self.list_urls is not None:
            raise ValueError("list_url 與 list_urls 只能擇一")
        for url in urls:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("列表頁必須是 http(s) 絕對網址")
        if len({urlsplit(u).netloc for u in urls}) > 1:
            raise ValueError("所有列表頁必須在同一網域")
        return self

    @property
    def lists(self) -> tuple[str, ...]:
        """要擷取的列表頁網址。"""
        if self.list_urls is not None:
            return self.list_urls
        return (self.list_url,) if self.list_url is not None else ()


@dataclass(frozen=True)
class ListEntry:
    """列表頁上的一則公告；`published` 為同一列顯示的日期（沒有時為 None）。"""

    post_id: int
    url: str
    title: str
    published: date | None


@dataclass(frozen=True)
class DetailPage:
    """內頁抽取結果。

    `published` 為 None 表示內頁不顯示日期；`content_html` 為 None 表示沒有內文（例如只有附件）。
    """

    title: str
    published: date | None
    content_html: str | None


def _first(node: LexborHTMLParser | LexborNode, selector: str) -> LexborNode | None:
    found = node.css(selector)
    return found[0] if found else None


def _clean(text: str) -> str:
    return " ".join(text.split())


def _find_date(text: str) -> date | None:
    found = _DATE.search(text)
    if found is None:
        return None
    try:
        return date(int(found[1]), int(found[2]), int(found[3]))
    except ValueError:
        return None


def _row_of(anchor: LexborNode) -> LexborNode:
    """公告連結所在的「一列」：表格的 tr，或 listBS 的 .d-item；都沒有時用 .mtitle。"""
    node: LexborNode | None = anchor.parent
    title_box = node
    while node is not None and node.tag not in ("tr", "body"):
        if "d-item" in (node.attributes.get("class") or "").split():
            return node
        node = node.parent
    if node is not None and node.tag == "tr":
        return node
    return title_box if title_box is not None else anchor


def parse_list(html: str, list_url: str) -> list[ListEntry]:
    """解析列表頁，回傳同網域的公告內頁（依頁面順序、去重）。

    找不到任何公告時拋出 LayoutChangedError。
    """
    tree = LexborHTMLParser(html)
    modules = tree.css(".module-rcglist")
    if not modules:
        raise LayoutChangedError("列表頁找不到 .module-rcglist")
    base = urlsplit(list_url)
    entries: dict[int, ListEntry] = {}
    for module in modules:
        for anchor in module.css(".mtitle a"):
            href = anchor.attributes.get("href")
            if not href:
                continue
            url = urljoin(list_url, href.strip())
            parts = urlsplit(url)
            match = _DETAIL_PATH.match(parts.path)
            if parts.scheme != base.scheme or parts.netloc != base.netloc or match is None:
                continue
            post_id = int(match["post"])
            if post_id not in entries:
                row_date = _find_date(_row_of(anchor).text())
                entries[post_id] = ListEntry(post_id, url, _clean(anchor.text()), row_date)
    if not entries:
        raise LayoutChangedError("列表頁沒有任何公告內頁連結")
    return list(entries.values())


def parse_detail(html: str) -> DetailPage:
    """解析內頁；缺標題時拋出 LayoutChangedError。

    發布日期可能不存在（國際處等網站的內頁不顯示），由呼叫端改用列表頁的日期。
    """
    module = _first(LexborHTMLParser(html), ".module-detail")
    if module is None:
        raise LayoutChangedError("內頁找不到 .module-detail")
    heading = _first(module, "h2.hdline")
    title = _clean(heading.text()) if heading is not None else ""
    if not title:
        raise LayoutChangedError("內頁缺少標題 h2.hdline")
    date_node = _first(module, ".ptinfoproperty_date")
    published = _find_date(date_node.text()) if date_node is not None else None
    body = _first(module, ".meditor")
    content = body.html if body is not None and body.text().strip() else None
    return DetailPage(title, published, content)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@registry.register("rpage")
class RPageAdapter:
    """RPage 公告 Adapter。

    `last_skipped` 為上一輪略過的內頁數（已刪除、robots 禁止、轉址到他站）。
    """

    code: str

    def __init__(
        self,
        config: SourceConfig,
        *,
        client: httpx2.AsyncClient | None = None,
        limiter: HostRateLimiter = shared_limiter,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.code = config.code
        self._options = RPageOptions.model_validate(dict(config.options))
        self._client = client
        self._clock = clock
        self._fetcher = PageFetcher(
            config.code,
            limiter=limiter,
            min_interval=self._options.min_interval,
            timeout=self._options.timeout,
            max_bytes=self._options.max_bytes,
        )
        self.last_skipped = 0

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        """抓取編號大於 cursor 的公告內頁（由舊到新，最多 `max_items` 篇）。"""
        since = self._parse_cursor(cursor)
        client = self._client or httpx2.AsyncClient()
        try:
            return await self._fetch(client, since)
        finally:
            if self._client is None:
                await client.aclose()

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """回傳本輪與上一輪 cursor 中最大的文章編號；cursor 不會倒退。"""
        ids = [int(i.external_id) for i in items if i.external_id and i.external_id.isdigit()]
        prev = self._parse_cursor(previous)
        if not ids or (prev is not None and prev >= max(ids)):
            return previous
        return str(max(ids))

    async def _fetch(self, client: httpx2.AsyncClient, since: int | None) -> list[RawItem]:
        entries: dict[int, ListEntry] = {}
        for list_url in self._options.lists:
            for entry in await self._fetch_list(client, list_url):
                entries.setdefault(entry.post_id, entry)  # 多個分類都有時，取先出現的

        new = sorted(
            (e for e in entries.values() if since is None or e.post_id > since),
            key=lambda e: e.post_id,
        )[: self._options.max_items]
        fetched_at = self._clock()
        items: list[RawItem] = []
        skipped = 0
        for entry in new:
            item = await self._fetch_detail(client, entry, fetched_at)
            if item is None:
                skipped += 1
            else:
                items.append(item)
        self.last_skipped = skipped
        return items

    async def _fetch_list(self, client: httpx2.AsyncClient, list_url: str) -> list[ListEntry]:
        if not await self._fetcher.allowed(client, list_url):
            raise AdapterError(f"來源 {self.code} 的列表頁被 robots.txt 禁止")
        page = await self._fetcher.get(client, list_url)
        if page.status >= 400:
            raise AdapterError(f"來源 {self.code} 的列表頁回應 HTTP {page.status}")
        return parse_list(page.text, list_url)

    async def _fetch_detail(
        self, client: httpx2.AsyncClient, entry: ListEntry, fetched_at: datetime
    ) -> RawItem | None:
        if not await self._fetcher.allowed(client, entry.url):
            return None
        page = await self._fetcher.get(client, entry.url)
        if page.status >= 400 or urlsplit(page.url).netloc != urlsplit(entry.url).netloc:
            return None  # 已刪除，或轉址到其他網站
        detail = parse_detail(page.text)
        day = detail.published or entry.published
        published = (
            datetime.combine(day, datetime.min.time(), tzinfo=TAIPEI) if day is not None else None
        )
        return RawItem(
            source_code=self.code,
            external_id=str(entry.post_id),
            url=entry.url,
            title=detail.title,
            content=detail.content_html or detail.title,
            content_type="text/html" if detail.content_html else "text/plain",
            published_at=published,
            fetched_at=fetched_at,
        )

    def _parse_cursor(self, cursor: str | None) -> int | None:
        if cursor is None:
            return None
        if not cursor.isdigit():
            raise AdapterError(f"來源 {self.code} 的 cursor 必須是文章編號")
        return int(cursor)
