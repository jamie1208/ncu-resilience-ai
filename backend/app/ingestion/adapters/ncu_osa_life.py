"""學務處生活輔導組公告 Adapter（P1-T16，ADR-0010），來源類型 `ncu_osa_life`。

首頁 `https://military.ncu.edu.tw/`（2026-10-18 確認）的公告表格每列是「公告日期 YYYY-MM-DD｜
公告類別｜公告標題」，標題連到 `post_detail.php?no=<編號>`；置頂公告排在最前面，所以頁面順序
不是時間順序。內頁是「公告日期／公告類別／公告標題／附加檔案／公告內容」的兩欄表格，
附加檔案不算內文。

cursor 為已擷取的最大公告編號（`ListDetailAdapter` 的預設行為）。設定見 `list_detail` 模組。
列表上的公告類別寫入 `RawItem.tags`（P1-T15）。
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.ingestion.adapters.list_detail import Detail, ListDetailAdapter, ListEntry
from app.ingestion.base import LayoutChangedError
from app.ingestion.html import clean, find_date, first, taipei_midnight
from app.ingestion.registry import registry

LIST_URL = "https://military.ncu.edu.tw/"
_DETAIL_FILE = "post_detail.php"


def parse_list(html: str, list_url: str, now: datetime) -> list[ListEntry]:
    """抽出公告表格的各列（含置頂）；找不到任何一列時拋出 LayoutChangedError。"""
    tree = LexborHTMLParser(html)
    entries: list[ListEntry] = []
    for row in tree.css("table tr"):
        cells = row.css("td")
        anchor = first(row, f'a[href*="{_DETAIL_FILE}?no="]')
        if len(cells) < 3 or anchor is None:
            continue
        url = urljoin(list_url, (anchor.attributes.get("href") or "").strip())
        number = parse_qs(urlsplit(url).query).get("no", [""])[0]
        title = clean(anchor.attributes.get("title") or anchor.text())
        category = clean(cells[1].text())
        if number.isdigit() and title:
            entries.append(
                ListEntry(
                    key=number,
                    url=url,
                    title=title,
                    published=find_date(cells[0].text()),
                    tags=frozenset({category}) if category else frozenset(),
                )
            )
    if not entries:
        raise LayoutChangedError("生輔組列表頁找不到任何公告")
    return entries


def _fields(tree: LexborHTMLParser) -> dict[str, LexborNode]:
    """內頁的「欄位名稱：」→ 值的儲存格。"""
    fields: dict[str, LexborNode] = {}
    for row in tree.css("table tr"):
        cells = row.css("td")
        if len(cells) >= 2:
            label = clean(cells[0].text()).rstrip("：:")
            if label:
                fields.setdefault(label, cells[1])
    return fields


def parse_detail(html: str) -> Detail:
    """抽出內頁標題、公告日期與公告內容；沒有標題時拋出 LayoutChangedError。"""
    fields = _fields(LexborHTMLParser(html))
    title_cell = fields.get("公告標題")
    title = clean(title_cell.text()) if title_cell is not None else ""
    if not title:
        raise LayoutChangedError("生輔組內頁找不到公告標題")
    date_cell = fields.get("公告日期")
    published = taipei_midnight(find_date(date_cell.text())) if date_cell is not None else None
    body_cell = fields.get("公告內容")
    content: str | None = None
    if body_cell is not None and clean(body_cell.text()):
        content = (body_cell.inner_html or "").strip()
    return Detail(title=title, published=published, content_html=content)


@registry.register("ncu_osa_life")
class NcuOsaLifeAdapter(ListDetailAdapter):
    """學務處生活輔導組公告 Adapter。"""

    def parse_list(self, html: str, list_url: str, now: datetime) -> list[ListEntry]:
        """見模組層級的 `parse_list`。"""
        return parse_list(html, list_url, now)

    def parse_detail(self, html: str) -> Detail:
        """見模組層級的 `parse_detail`。"""
        return parse_detail(html)
