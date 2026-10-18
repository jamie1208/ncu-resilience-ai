"""「列表頁＋內頁」型 HTML Adapter 的共用流程（P1-T16）。

子類別只需要實作兩件事：
- `parse_list`：從列表頁抽出各篇的編號、網址、標題與列表日期；
- `parse_detail`：從內頁抽出標題、發布時間與內文 HTML（必要欄位缺漏時拋出 LayoutChangedError）。

共用流程：
- robots.txt、限速、大小上限與錯誤分類沿用 `PageFetcher`（P1-T14）。
- 列表頁被 robots 禁止或回應 4xx 時拋出不可重試的錯誤。
- 只跟隨與列表頁同網域的內頁連結。
- 內頁已刪除（4xx）、轉址到其他網站、或被 robots 禁止時略過，計入 `last_skipped`。
- 發布時間優先用內頁的時間，其次用列表日期（台北時間 00:00）。
- 內頁沒有文字內文時，以標題作為內容（`text/plain`）。
- 預設的 cursor 是已擷取的最大數字編號；每輪由舊到新最多 `max_items` 篇。
  編號不是數字的網站覆寫 `select_new` 與 `next_cursor`。

來源設定（`SourceConfig.options`）：
- `list_url`（必填）：列表頁 http(s) 網址
- `max_items`：每輪最多抓幾篇內頁（1–50，預設 20）
- `min_interval`：同網域請求間隔秒數（≥ 5，預設 5）
- `timeout`：單次請求秒數（預設 15）；`max_bytes`：單頁大小上限（預設 2 MB）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ingestion.base import AdapterError, LayoutChangedError
from app.ingestion.html import taipei_midnight
from app.ingestion.http import MIN_REQUEST_INTERVAL, HostRateLimiter, PageFetcher, shared_limiter
from app.ingestion.types import RawItem, SourceConfig


class ListDetailOptions(BaseModel):
    """列表＋內頁型來源的設定；無效值在建立 Adapter 時拋出 ValueError。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    list_url: str
    max_items: int = Field(default=20, ge=1, le=50)
    min_interval: float = Field(default=MIN_REQUEST_INTERVAL, ge=MIN_REQUEST_INTERVAL)
    timeout: float = Field(default=15.0, gt=0, le=60)
    max_bytes: int = Field(default=2_000_000, gt=0)

    @field_validator("list_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("list_url 必須是 http(s) 絕對網址")
        return value


@dataclass(frozen=True)
class ListEntry:
    """列表頁上的一篇；`key` 是來源內的編號（作為 external_id），`published` 是列表日期，
    `tags` 是列表上的分類標籤（P1-T15）。
    """

    key: str
    url: str
    title: str
    published: date | None
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Detail:
    """內頁抽取結果；`published` 為 aware 時間或 None，`content_html` 為 None 表示沒有內文。"""

    title: str
    published: datetime | None
    content_html: str | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ListDetailAdapter(ABC):
    """列表＋內頁型 HTML Adapter 的基底類別。`last_skipped` 為上一輪略過的內頁數。"""

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
        self._options = ListDetailOptions.model_validate(dict(config.options))
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

    # --- 子類別實作 ---------------------------------------------------------------------

    @abstractmethod
    def parse_list(self, html: str, list_url: str, now: datetime) -> list[ListEntry]:
        """從列表頁抽出各篇；找不到任何一篇時拋出 LayoutChangedError。"""

    @abstractmethod
    def parse_detail(self, html: str) -> Detail:
        """從內頁抽出標題、時間與內文；必要欄位缺漏時拋出 LayoutChangedError。"""

    # --- cursor（預設：數字編號） --------------------------------------------------------

    def select_new(self, entries: Sequence[ListEntry], cursor: str | None) -> list[ListEntry]:
        """挑出 cursor 之後的新文章，由舊到新排序。"""
        since = self._parse_numeric(cursor)
        numbered = [e for e in entries if e.key.isdigit()]
        return sorted(
            (e for e in numbered if since is None or int(e.key) > since), key=lambda e: int(e.key)
        )

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """回傳本輪與上一輪 cursor 中最大的編號；cursor 不會倒退。"""
        ids = [int(i.external_id) for i in items if i.external_id and i.external_id.isdigit()]
        prev = self._parse_numeric(previous)
        if not ids or (prev is not None and prev >= max(ids)):
            return previous
        return str(max(ids))

    def _parse_numeric(self, cursor: str | None) -> int | None:
        if cursor is None:
            return None
        if not cursor.isdigit():
            raise AdapterError(f"來源 {self.code} 的 cursor 必須是文章編號")
        return int(cursor)

    # --- 共用流程 -----------------------------------------------------------------------

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        """抓取 cursor 之後的新文章內頁（由舊到新，最多 `max_items` 篇）。"""
        client = self._client or httpx2.AsyncClient()
        try:
            return await self._fetch(client, cursor)
        finally:
            if self._client is None:
                await client.aclose()

    async def _fetch(self, client: httpx2.AsyncClient, cursor: str | None) -> list[RawItem]:
        list_url = self._options.list_url
        if not await self._fetcher.allowed(client, list_url):
            raise AdapterError(f"來源 {self.code} 的列表頁被 robots.txt 禁止")
        page = await self._fetcher.get(client, list_url)
        if page.status >= 400:
            raise AdapterError(f"來源 {self.code} 的列表頁回應 HTTP {page.status}")
        fetched_at = self._clock()
        host = urlsplit(list_url).netloc
        entries: dict[str, ListEntry] = {}
        for entry in self.parse_list(page.text, list_url, fetched_at):
            if urlsplit(entry.url).netloc == host:
                entries.setdefault(entry.key, entry)
        new = self.select_new(list(entries.values()), cursor)[: self._options.max_items]
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

    async def _fetch_detail(
        self, client: httpx2.AsyncClient, entry: ListEntry, fetched_at: datetime
    ) -> RawItem | None:
        if not await self._fetcher.allowed(client, entry.url):
            return None
        page = await self._fetcher.get(client, entry.url)
        if page.status >= 400 or urlsplit(page.url).netloc != urlsplit(entry.url).netloc:
            return None  # 已刪除，或轉址到其他網站
        detail = self.parse_detail(page.text)
        if not detail.title:
            raise LayoutChangedError(f"來源 {self.code} 的內頁抽不到標題")
        return RawItem(
            source_code=self.code,
            external_id=entry.key,
            url=entry.url,
            title=detail.title,
            content=detail.content_html or detail.title,
            content_type="text/html" if detail.content_html else "text/plain",
            published_at=detail.published or taipei_midnight(entry.published),
            fetched_at=fetched_at,
            tags=entry.tags,
        )
