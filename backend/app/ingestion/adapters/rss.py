"""RSS／Atom Adapter（P1-T02）。

以 httpx2 下載 feed（timeout、User-Agent、大小上限由本模組控制），再交給 feedparser 解析。
feedparser 只會拿到 bytes，不會自行連線；HTML 由 feedparser 預設清洗。

來源設定（`SourceConfig.options`）：
- `url`（必填）：feed 的 http(s) 網址
- `timeout`：單次請求秒數，預設 10
- `max_bytes`：回應大小上限，預設 5 MB

cursor 是最新一篇發布時間的 ISO 8601 字串（aware UTC）。邊界採「大於等於」，
寧可重複也不漏，重複由冪等寫入（P1-T06）處理。沒有發布時間的項目每輪都會回傳。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import feedparser
import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.ingestion.base import AdapterError, LayoutChangedError, SourceUnavailableError
from app.ingestion.http import USER_AGENT
from app.ingestion.registry import registry
from app.ingestion.types import RawItem, SourceConfig

__all__ = ["USER_AGENT", "RssAdapter", "RssOptions"]

_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9"
_RETRYABLE_STATUS = frozenset({408, 425, 429})
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})


class RssOptions(BaseModel):
    """RSS 來源的設定；無效值在建立 Adapter 時拋出 ValueError。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    timeout: float = Field(default=10.0, gt=0, le=60)
    max_bytes: int = Field(default=5_000_000, gt=0)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")) or len(value) <= len("https://"):
            raise ValueError("url 必須是 http(s) 網址")
        return value


def _utc_now() -> datetime:
    return datetime.now(UTC)


@registry.register("rss")
class RssAdapter:
    """RSS 2.0／Atom feed Adapter。`last_skipped` 為上一輪因欄位缺漏而略過的項目數。"""

    code: str

    def __init__(
        self,
        config: SourceConfig,
        *,
        client: httpx2.AsyncClient | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.code = config.code
        self._options = RssOptions.model_validate(dict(config.options))
        self._client = client
        self._clock = clock
        self.last_skipped = 0

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        """下載並解析 feed，回傳發布時間不早於 cursor 的項目。"""
        since = self._parse_cursor(cursor)
        body, headers = await self._download()
        parsed = feedparser.parse(body, response_headers=headers)
        if not parsed.version and not parsed.entries:
            raise LayoutChangedError(f"來源 {self.code} 的回應不是 RSS／Atom feed")

        fetched_at = self._clock()
        items: list[RawItem] = []
        for entry in parsed.entries:
            item = self._to_item(entry, fetched_at)
            if item is not None:
                items.append(item)
        self.last_skipped = len(parsed.entries) - len(items)
        if parsed.entries and not items:
            raise LayoutChangedError(
                f"來源 {self.code} 的 {len(parsed.entries)} 個項目都缺少必要欄位"
            )

        if since is None:
            return items
        return [i for i in items if i.published_at is None or i.published_at >= since]

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """回傳本輪與上一輪 cursor 中最新的發布時間；cursor 不會倒退。"""
        times = [i.published_at for i in items if i.published_at is not None]
        prev = self._parse_cursor(previous)
        if not times or (prev is not None and prev >= max(times)):
            return previous
        return max(times).astimezone(UTC).isoformat()

    def _parse_cursor(self, cursor: str | None) -> datetime | None:
        if cursor is None:
            return None
        try:
            value = datetime.fromisoformat(cursor)
        except ValueError:
            raise AdapterError(f"來源 {self.code} 的 cursor 不是 ISO 8601 時間") from None
        if value.tzinfo is None:
            raise AdapterError(f"來源 {self.code} 的 cursor 缺少時區")
        return value

    async def _download(self) -> tuple[bytes, dict[str, str]]:
        client = self._client or httpx2.AsyncClient()
        try:
            return await self._get(client)
        finally:
            if self._client is None:
                await client.aclose()

    async def _get(self, client: httpx2.AsyncClient) -> tuple[bytes, dict[str, str]]:
        opts = self._options
        try:
            async with client.stream(
                "GET",
                opts.url,
                headers={"User-Agent": USER_AGENT, "Accept": _ACCEPT},
                timeout=opts.timeout,
                follow_redirects=True,
            ) as response:
                status = response.status_code
                if status >= 500 or status in _RETRYABLE_STATUS:
                    raise SourceUnavailableError(f"來源 {self.code} 回應 HTTP {status}")
                if status >= 400:
                    raise AdapterError(f"來源 {self.code} 回應 HTTP {status}")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > opts.max_bytes:
                        raise AdapterError(f"來源 {self.code} 的回應超過 {opts.max_bytes} bytes")
                    chunks.append(chunk)
                headers = {
                    "content-type": response.headers.get("content-type", ""),
                    "content-location": str(response.url),
                }
                return b"".join(chunks), headers
        except httpx2.TransportError as exc:
            raise SourceUnavailableError(
                f"來源 {self.code} 連線失敗（{type(exc).__name__}）"
            ) from exc
        except httpx2.HTTPError as exc:
            raise AdapterError(f"來源 {self.code} 請求失敗（{type(exc).__name__}）") from exc

    def _to_item(self, entry: feedparser.FeedParserDict, fetched_at: datetime) -> RawItem | None:
        link = _text(entry.get("link"))
        title = _text(entry.get("title"))
        content, content_type = _content(entry)
        if content is None and title is not None:
            content, content_type = title, "text/plain"
        if link is None or content is None:
            return None
        published = _time(entry.get("published_parsed")) or _time(entry.get("updated_parsed"))
        try:
            return RawItem(
                source_code=self.code,
                external_id=_text(entry.get("id")) or link,
                url=link,
                title=title,
                content=content,
                content_type=content_type,
                published_at=published,
                fetched_at=fetched_at,
            )
        except ValidationError:
            return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _content(entry: feedparser.FeedParserDict) -> tuple[str | None, str]:
    """優先取全文（content），沒有時取摘要（summary）。"""
    candidates: list[object] = []
    full = entry.get("content")
    if isinstance(full, list):
        candidates.extend(full)
    candidates.append(entry.get("summary_detail"))
    for detail in candidates:
        if isinstance(detail, dict):
            value = _text(detail.get("value"))
            if value is not None:
                return value, "text/html" if detail.get("type") in _HTML_TYPES else "text/plain"
    return _text(entry.get("summary")), "text/plain"


def _time(value: object) -> datetime | None:
    # feedparser 的 *_parsed 一律是 UTC 的 struct_time
    if isinstance(value, time.struct_time):
        return datetime(*value[:6], tzinfo=UTC)
    return None
