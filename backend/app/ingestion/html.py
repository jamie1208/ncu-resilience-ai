"""HTML Adapter 共用的解析小工具（P1-T16）。

只放與網站無關的小函式；各網站的版型解析仍留在各自的 Adapter。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.ingestion.normalize import TAIPEI

_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

__all__ = ["TAIPEI", "clean", "find_date", "first", "taipei_midnight"]


def first(node: LexborHTMLParser | LexborNode, selector: str) -> LexborNode | None:
    """第一個符合 `selector` 的節點；沒有時回傳 None。"""
    found = node.css(selector)
    return found[0] if found else None


def clean(text: str) -> str:
    """把連續空白（含換行、全形空白）合併成一個半形空白，並去掉頭尾空白。"""
    return " ".join(text.split())


def find_date(text: str) -> date | None:
    """文字中第一個 `YYYY-MM-DD` 日期；沒有或日期無效時回傳 None。"""
    found = _DATE.search(text)
    if found is None:
        return None
    try:
        return date(int(found[1]), int(found[2]), int(found[3]))
    except ValueError:
        return None


def taipei_midnight(day: date | None) -> datetime | None:
    """只有日期時，視為台北時間當日 00:00。"""
    return datetime.combine(day, datetime.min.time(), tzinfo=TAIPEI) if day is not None else None
