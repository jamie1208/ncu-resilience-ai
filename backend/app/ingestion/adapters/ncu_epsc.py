"""環境保護暨安全衛生中心最新消息 Adapter（P1-T16，ADR-0010），來源類型 `ncu_epsc`。

列表頁 `https://ncu.edu.tw/epsc/index.php/index/News.html`（2026-10-18 確認）的 `#textList li a`
連到 `News_more.html?id=<hex 編號>`，連結內有標題 `h3` 與「發布日期： YYYY-MM-DD」。列表不依時間
排序（有置頂）。內頁有 `.pageTitle h2` 標題、`.pageData .date` 發佈日期與 `.articleBox` 內文。

文章編號是 hex 字串，無法比大小，所以 cursor 改為「最新列表日期｜當日已擷取的編號」，
例如 `2026-10-16|bbbb…,cccc…`：日期較新的、或同一天但還沒擷取過的才算新文章，由舊到新排序。
挑選與 cursor 都以**列表日期**為準（內頁日期可能不同），沒有列表日期時才用內頁日期。
沒有日期的文章只在第一輪（cursor 為 None）擷取。設定見 `list_detail` 模組。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from app.ingestion.adapters.list_detail import Detail, ListDetailAdapter, ListEntry
from app.ingestion.base import AdapterError, LayoutChangedError
from app.ingestion.html import TAIPEI, clean, find_date, first, taipei_midnight
from app.ingestion.registry import registry
from app.ingestion.types import RawItem

LIST_URL = "https://ncu.edu.tw/epsc/index.php/index/News.html"
_DETAIL_FILE = "News_more.html"
_KEY = re.compile(r"^[0-9a-f]+$")
_CURSOR = re.compile(r"^(\d{4}-\d{2}-\d{2})\|([0-9a-f]+(?:,[0-9a-f]+)*)$")

Cursor = tuple[date, frozenset[str]]


def parse_list(html: str, list_url: str, now: datetime) -> list[ListEntry]:
    """抽出列表的各篇；找不到任何一篇時拋出 LayoutChangedError。"""
    tree = LexborHTMLParser(html)
    entries: list[ListEntry] = []
    for anchor in tree.css(f'#textList li a[href*="{_DETAIL_FILE}?id="]'):
        url = urljoin(list_url, (anchor.attributes.get("href") or "").strip())
        key = parse_qs(urlsplit(url).query).get("id", [""])[0]
        heading = first(anchor, "h3")
        title = clean(heading.text()) if heading is not None else ""
        stamp = first(anchor, ".date")
        if _KEY.match(key) and title:
            entries.append(
                ListEntry(
                    key=key,
                    url=url,
                    title=title,
                    published=find_date(stamp.text()) if stamp is not None else None,
                )
            )
    if not entries:
        raise LayoutChangedError("環安中心列表頁找不到任何消息")
    return entries


def parse_detail(html: str) -> Detail:
    """抽出內頁標題、發佈日期與內文；沒有標題時拋出 LayoutChangedError。"""
    tree = LexborHTMLParser(html)
    heading = first(tree, ".pageTitle h2")
    title = clean(heading.text()) if heading is not None else ""
    if not title:
        raise LayoutChangedError("環安中心內頁找不到標題")
    stamp = first(tree, ".pageData .date")
    published = taipei_midnight(find_date(stamp.text())) if stamp is not None else None
    body = first(tree, ".articleBox")
    content = body.html if body is not None and clean(body.text()) else None
    return Detail(title=title, published=published, content_html=content)


@registry.register("ncu_epsc")
class NcuEpscAdapter(ListDetailAdapter):
    """環安中心最新消息 Adapter（以日期為 cursor）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._list_dates: dict[str, date] = {}

    def parse_list(self, html: str, list_url: str, now: datetime) -> list[ListEntry]:
        """見模組層級的 `parse_list`；同時記下各篇的列表日期，供 cursor 使用。"""
        entries = parse_list(html, list_url, now)
        self._list_dates = {e.key: e.published for e in entries if e.published is not None}
        return entries

    def parse_detail(self, html: str) -> Detail:
        """見模組層級的 `parse_detail`。"""
        return parse_detail(html)

    def _parse_date_cursor(self, cursor: str | None) -> Cursor | None:
        if cursor is None:
            return None
        found = _CURSOR.match(cursor)
        try:
            if found is None:
                raise ValueError
            return date.fromisoformat(found[1]), frozenset(found[2].split(","))
        except ValueError:
            raise AdapterError(f"來源 {self.code} 的 cursor 必須是「YYYY-MM-DD|編號,…」") from None

    def select_new(self, entries: Sequence[ListEntry], cursor: str | None) -> list[ListEntry]:
        """日期較新、或同一天但還沒擷取過的文章，依（日期、編號）由舊到新。"""
        since = self._parse_date_cursor(cursor)

        def is_new(entry: ListEntry) -> bool:
            if since is None:
                return True
            if entry.published is None:
                return False
            day, seen = since
            return entry.published > day or (entry.published == day and entry.key not in seen)

        return sorted(
            (e for e in entries if is_new(e)), key=lambda e: (e.published or date.min, e.key)
        )

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """記下本輪最新的發布日期與當日的編號；與上一輪同一天時合併編號，日期不會倒退。"""
        days: dict[date, set[str]] = {}
        for item in items:
            if not item.external_id:
                continue
            day = self._list_dates.get(item.external_id)
            if day is None and item.published_at is not None:
                day = item.published_at.astimezone(TAIPEI).date()
            if day is not None:
                days.setdefault(day, set()).add(item.external_id)
        if not days:
            return previous
        prev = self._parse_date_cursor(previous)
        latest = max(days)
        if prev is not None and prev[0] > latest:
            return previous
        ids = days[latest] | (set(prev[1]) if prev is not None and prev[0] == latest else set())
        return f"{latest.isoformat()}|{','.join(sorted(ids))}"
