"""中央大學新聞網 Adapter（P1-T12，ADR-0007），來源類型 `ncu_news`。

新聞網 `https://ncusec.ncu.edu.tw/news/` 有三個列表：中大新聞 `headlines.php`（全部新聞）、
焦點新聞 `focus-news.php`、學術新聞 `academic-news.php`。三個列表的卡片版型不同
（`div.release-list-card`、`a.box-card`），共通點是卡片連結指向同一個內頁
`headlines_content.php?H_ID=<編號>`，連結內有標題 `h3`、分類 `.tab-box`、日期 `.date-box`。
內頁固定為 `section.main-detail-box` 中的 `h3.main-title`、「發布日期：」與
`.main-detail-content-box`。

擷取原則與 RPage Adapter（P1-T03）相同：每個網域每個 Adapter 實例檢查一次 robots.txt；
同網域請求間隔 ≥ 5 秒、User-Agent 標示專案；只跟隨同網域的內頁連結。網站的 robots.txt
實際回傳一般 HTML 頁面（2026-10-18），解析後沒有規則，視為允許。

去重與 cursor：`H_ID` 是三個列表共用的流水號，作為 `external_id`。同一篇出現在多個列表時
只抓一次內頁，分類取各列表標籤的聯集，寫入 `RawItem.tags`（P1-T15）；`categories` 屬性
保留為舊介面，內容與各項目的 tags 相同。
cursor 是已擷取的最大 `H_ID`；每輪由舊到新最多 `max_items` 篇。
只讀各列表第一頁，不跟隨分頁、不回溯歷史新聞。

來源設定（`SourceConfig.options`）：
- `list_urls`（必填）：列表頁 http(s) 網址，必須在同一網域
- `max_items`：每輪最多抓幾篇內頁（1–50，預設 20）
- `min_interval`：同網域請求間隔秒數（≥ 5，預設 5）
- `timeout`：單次請求秒數（預設 15）；`max_bytes`：單頁大小上限（預設 2 MB）
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Self
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, model_validator
from selectolax.lexbor import LexborHTMLParser

from app.ingestion.base import AdapterError, LayoutChangedError
from app.ingestion.html import clean, find_date, first, taipei_midnight
from app.ingestion.http import MIN_REQUEST_INTERVAL, HostRateLimiter, PageFetcher, shared_limiter
from app.ingestion.registry import registry
from app.ingestion.types import RawItem, SourceConfig

_DETAIL_FILE = "headlines_content.php"


class NcuNewsOptions(BaseModel):
    """新聞網來源的設定；無效值在建立 Adapter 時拋出 ValueError。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    list_urls: tuple[str, ...]
    max_items: int = Field(default=20, ge=1, le=50)
    min_interval: float = Field(default=MIN_REQUEST_INTERVAL, ge=MIN_REQUEST_INTERVAL)
    timeout: float = Field(default=15.0, gt=0, le=60)
    max_bytes: int = Field(default=2_000_000, gt=0)

    @model_validator(mode="after")
    def _check_lists(self) -> Self:
        if not self.list_urls:
            raise ValueError("list_urls 至少要有一個列表頁")
        for url in self.list_urls:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("列表頁必須是 http(s) 絕對網址")
        if len({urlsplit(u).netloc for u in self.list_urls}) > 1:
            raise ValueError("所有列表頁必須在同一網域（H_ID 才是共用的流水號）")
        return self


@dataclass(frozen=True)
class NewsEntry:
    """列表頁上的一則新聞；`published`、`category` 為卡片上顯示的日期與分類（沒有時為 None）。"""

    news_id: int
    url: str
    title: str
    published: date | None
    category: str | None


@dataclass(frozen=True)
class NewsDetail:
    """內頁抽取結果。

    `published` 為 None 表示內頁不顯示日期；
    `content_html` 為 None 表示沒有文字內文（例如只有圖片）。
    """

    title: str
    published: date | None
    content_html: str | None


def _detail_id(url: str, list_url: str) -> int | None:
    """同網域內頁網址的 H_ID；不是內頁或在他站時回傳 None。"""
    parts, base = urlsplit(url), urlsplit(list_url)
    if parts.scheme != base.scheme or parts.netloc != base.netloc:
        return None
    if parts.path.rsplit("/", 1)[-1] != _DETAIL_FILE:
        return None
    values = parse_qs(parts.query).get("H_ID", [])
    return int(values[0]) if len(values) == 1 and values[0].isdigit() else None


def _canonical(url: str, news_id: int) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}?H_ID={news_id}"


def parse_list(html: str, list_url: str) -> list[NewsEntry]:
    """解析列表頁，回傳同網域、有標題的新聞卡片（依頁面順序、去重）。

    找不到任何新聞卡片時拋出 LayoutChangedError。
    """
    entries: dict[int, NewsEntry] = {}
    for anchor in LexborHTMLParser(html).css("a[href]"):
        heading = first(anchor, "h3")
        title = clean(heading.text()) if heading is not None else ""
        if not title:
            continue  # 選單、分頁、輪播圖等沒有標題的連結
        url = urljoin(list_url, (anchor.attributes.get("href") or "").strip())
        news_id = _detail_id(url, list_url)
        if news_id is None or news_id in entries:
            continue
        tab = first(anchor, ".tab-box")
        day = first(anchor, ".date-box")
        entries[news_id] = NewsEntry(
            news_id=news_id,
            url=_canonical(url, news_id),
            title=title,
            published=find_date(day.text()) if day is not None else None,
            category=(clean(tab.text()) or None) if tab is not None else None,
        )
    if not entries:
        raise LayoutChangedError("列表頁沒有任何新聞卡片（headlines_content.php 連結）")
    return list(entries.values())


def parse_detail(html: str) -> NewsDetail:
    """解析內頁；缺內頁區塊或標題時拋出 LayoutChangedError。"""
    box = first(LexborHTMLParser(html), "section.main-detail-box")
    if box is None:
        raise LayoutChangedError("內頁找不到 section.main-detail-box")
    heading = first(box, "h3.main-title")
    title = clean(heading.text()) if heading is not None else ""
    if not title:
        raise LayoutChangedError("內頁缺少標題 h3.main-title")
    header = first(box, ".related-box")
    published = find_date(header.text()) if header is not None else None
    body = first(box, ".main-detail-content-box")
    content = body.html if body is not None and body.text().strip() else None
    return NewsDetail(title, published, content)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@registry.register("ncu_news")
class NcuNewsAdapter:
    """中央大學新聞網 Adapter。

    `last_skipped` 為上一輪略過的內頁數（已刪除、robots 禁止、轉址到他站）；
    `categories` 為上一輪產出項目的分類（external_id → 分類標籤）；P1-T15 起分類寫入
    `RawItem.tags`，這個屬性保留為舊介面（使用者 2026-10-18 決定），內容由 tags 推算。
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
        self._options = NcuNewsOptions.model_validate(dict(config.options))
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
        self.categories: dict[str, frozenset[str]] = {}

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        """抓取 H_ID 大於 cursor 的新聞內頁（由舊到新，最多 `max_items` 篇）。"""
        since = self._parse_cursor(cursor)
        client = self._client or httpx2.AsyncClient()
        try:
            return await self._fetch(client, since)
        finally:
            if self._client is None:
                await client.aclose()

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """回傳本輪與上一輪 cursor 中最大的 H_ID；cursor 不會倒退。"""
        ids = [int(i.external_id) for i in items if i.external_id and i.external_id.isdigit()]
        prev = self._parse_cursor(previous)
        if not ids or (prev is not None and prev >= max(ids)):
            return previous
        return str(max(ids))

    async def _fetch(self, client: httpx2.AsyncClient, since: int | None) -> list[RawItem]:
        entries: dict[int, NewsEntry] = {}
        labels: dict[int, set[str]] = {}
        for list_url in self._options.list_urls:
            for entry in await self._fetch_list(client, list_url):
                entries.setdefault(entry.news_id, entry)  # 多個列表都有時，取先出現的
                if entry.category is not None:
                    labels.setdefault(entry.news_id, set()).add(entry.category)

        new = sorted(
            (e for e in entries.values() if since is None or e.news_id > since),
            key=lambda e: e.news_id,
        )[: self._options.max_items]
        fetched_at = self._clock()
        items: list[RawItem] = []
        categories: dict[str, frozenset[str]] = {}
        skipped = 0
        for entry in new:
            tags = frozenset(labels.get(entry.news_id, ()))
            item = await self._fetch_detail(client, entry, fetched_at, tags)
            if item is None:
                skipped += 1
                continue
            items.append(item)
            categories[str(entry.news_id)] = item.tags
        self.last_skipped = skipped
        self.categories = categories
        return items

    async def _fetch_list(self, client: httpx2.AsyncClient, list_url: str) -> list[NewsEntry]:
        if not await self._fetcher.allowed(client, list_url):
            raise AdapterError(f"來源 {self.code} 的列表頁被 robots.txt 禁止")
        page = await self._fetcher.get(client, list_url)
        if page.status >= 400:
            raise AdapterError(f"來源 {self.code} 的列表頁回應 HTTP {page.status}")
        return parse_list(page.text, list_url)

    async def _fetch_detail(
        self,
        client: httpx2.AsyncClient,
        entry: NewsEntry,
        fetched_at: datetime,
        tags: frozenset[str],
    ) -> RawItem | None:
        if not await self._fetcher.allowed(client, entry.url):
            return None
        page = await self._fetcher.get(client, entry.url)
        if page.status >= 400 or urlsplit(page.url).netloc != urlsplit(entry.url).netloc:
            return None  # 已刪除，或轉址到其他網站
        detail = parse_detail(page.text)
        day = detail.published or entry.published
        published = taipei_midnight(day)
        return RawItem(
            source_code=self.code,
            external_id=str(entry.news_id),
            url=entry.url,
            title=detail.title,
            content=detail.content_html or detail.title,
            content_type="text/html" if detail.content_html else "text/plain",
            published_at=published,
            fetched_at=fetched_at,
            tags=tags,
        )

    def _parse_cursor(self, cursor: str | None) -> int | None:
        if cursor is None:
            return None
        if not cursor.isdigit():
            raise AdapterError(f"來源 {self.code} 的 cursor 必須是新聞編號 H_ID")
        return int(cursor)
