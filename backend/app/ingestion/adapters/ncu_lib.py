"""中央大學圖書館最新消息 Adapter（P1-T16，ADR-0010），來源類型 `ncu_lib`。

列表頁 `https://www.lib.ncu.edu.tw/libboard/board_all.php`（2026-10-18 確認）是表格：每列一個
`MM/DD` 日期（沒有年份）與連到 `libboard/news_content.php?NewsID=<編號>` 的標題。頁面有
`<base href="/">`，相對連結從網站根目錄解析；導覽選單裡也有同格式但沒有日期的連結，不算公告。
內頁 `#libContent` 中有 `h1` 標題、「發表單位」「發表日期：YYYY-MM-DD HH:MM:SS」，其後是內文。

列表日期沒有年份：取擷取當下（台北時間）的年份，晚於今天時視為去年。內頁的發表日期優先。
cursor 為已擷取的最大 NewsID（`ListDetailAdapter` 的預設行為）。設定見 `list_detail` 模組。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from app.ingestion.adapters.list_detail import Detail, ListDetailAdapter, ListEntry
from app.ingestion.base import LayoutChangedError
from app.ingestion.html import TAIPEI, clean, first
from app.ingestion.registry import registry

LIST_URL = "https://www.lib.ncu.edu.tw/libboard/board_all.php"
_DETAIL_FILE = "news_content.php"
_MONTH_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_PUBLISHED = re.compile(r"發表日期：\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)")
_LEADING_STAMP = re.compile(r"^\s*\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?")


def _list_date(text: str, now: datetime) -> date | None:
    found = _MONTH_DAY.match(clean(text))
    if found is None:
        return None
    today = now.astimezone(TAIPEI).date()
    try:
        day = date(today.year, int(found[1]), int(found[2]))
    except ValueError:
        return None
    return day if day <= today else day.replace(year=today.year - 1)


def parse_list(html: str, list_url: str, now: datetime) -> list[ListEntry]:
    """抽出公告表格的各列；找不到任何一列時拋出 LayoutChangedError。"""
    tree = LexborHTMLParser(html)
    base_node = first(tree, "base[href]")
    base = urljoin(list_url, base_node.attributes.get("href") or "") if base_node else list_url
    entries: list[ListEntry] = []
    for row in tree.css("tr"):
        cells = row.css("td")
        anchor = first(row, f'a[href*="{_DETAIL_FILE}?NewsID="]')
        if len(cells) < 2 or anchor is None:
            continue
        published = _list_date(cells[0].text(), now)
        if published is None:
            continue
        url = urljoin(base, (anchor.attributes.get("href") or "").strip())
        news_id = parse_qs(urlsplit(url).query).get("NewsID", [""])[0]
        title = clean(anchor.text())
        if news_id.isdigit() and title:
            entries.append(ListEntry(key=news_id, url=url, title=title, published=published))
    if not entries:
        raise LayoutChangedError("圖書館列表頁找不到任何公告")
    return entries


def parse_detail(html: str) -> Detail:
    """抽出內頁標題、發表時間與內文；沒有 `#libContent` 或標題時拋出 LayoutChangedError。"""
    tree = LexborHTMLParser(html)
    box = first(tree, "#libContent")
    heading = first(box, "h1") if box is not None else None
    title = clean(heading.text()) if heading is not None else ""
    if box is None or not title:
        raise LayoutChangedError("圖書館內頁找不到標題")
    stamp = _PUBLISHED.search(clean(box.text()))
    published = datetime.fromisoformat(stamp[1]).replace(tzinfo=TAIPEI) if stamp else None
    for noise in box.css('style, script, h1, font, a[href^="mailto"]'):
        noise.decompose()
    body = _LEADING_STAMP.sub("", box.inner_html or "", count=1).strip()
    has_text = bool(clean(LexborHTMLParser(body).text())) if body else False
    return Detail(title=title, published=published, content_html=body if has_text else None)


@registry.register("ncu_lib")
class NcuLibAdapter(ListDetailAdapter):
    """圖書館最新消息 Adapter。"""

    def parse_list(self, html: str, list_url: str, now: datetime) -> list[ListEntry]:
        """見模組層級的 `parse_list`。"""
        return parse_list(html, list_url, now)

    def parse_detail(self, html: str) -> Detail:
        """見模組層級的 `parse_detail`。"""
        return parse_detail(html)
