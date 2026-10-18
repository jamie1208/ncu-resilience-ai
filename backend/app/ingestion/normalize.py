"""擷取內容的標準化（規格 F02：時間標準化、內容雜湊、語系）。

- 時間：一律轉成 aware UTC；沒有時區的時間視為台北時間（校方網站與匯入資料皆是）。
  `parse_datetime` 解析西元年、民國年、上午／下午與全形數字，供人工匯入等文字日期使用。
- 文字：NFC、全形英數與符號轉半形（中文常用的全形標點保留）、移除不可見字元、
  空白與空行收斂。HTML 轉成以空行分段的純文字。
- `normalized_text` = 標題 + 空行 + 內文；`content_hash` = sha256(normalized_text)。
  只有排版、空白、全半形不同的內容會得到相同雜湊，不會產生新版本。
- 語系：以字元統計判斷 `zh`／`en`，無法判斷時為 `und`。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict
from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.ingestion.types import RawItem

TAIPEI = timezone(timedelta(hours=8))  # 台灣沒有日光節約時間
ROC_YEAR_OFFSET = 1911

Language = Literal["zh", "en", "und"]

# --- 時間 ------------------------------------------------------------------------------


def to_utc(value: datetime) -> datetime:
    """轉成 aware UTC；沒有時區的時間視為台北時間。"""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=TAIPEI)
    return value.astimezone(UTC)


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_DATE = re.compile(
    r"(?<!\d)(?:中華民國|民國)?\s*(?P<y>\d{1,4})\s*(?:年|[-/.])\s*"
    r"(?P<m>\d{1,2})\s*(?:月|[-/.])\s*(?P<d>\d{1,2})(?!\d)\s*日?"
)
_TIME = re.compile(
    r"(?P<ampm>上午|下午|AM|PM|am|pm)?\s*(?P<h>\d{1,2})\s*(?::|時)\s*(?P<min>\d{1,2})"
    r"(?:\s*:\s*(?P<s>\d{1,2})|\s*分)?"
)


def parse_datetime(text: str) -> datetime | None:
    """解析文字中的日期時間，回傳 aware UTC；無法解析或日期時間無效時回傳 None。

    支援西元年（2026-10-17、2026/10/17、2026年10月17日）、民國年（115/10/17、
    民國115年10月17日）、ISO 8601、上午／下午與全形數字。只有日期時視為台北時間 00:00。
    """
    value = _halfwidth(text, keep=frozenset()).strip()
    if _ISO.match(value):
        try:
            return to_utc(datetime.fromisoformat(value))
        except ValueError:
            return None
    found = _DATE.search(value)
    if found is None:
        return None
    year = int(found["y"])
    if len(found["y"]) < 4:  # 民國年
        if year < 1:
            return None
        year += ROC_YEAR_OFFSET
    hour = minute = second = 0
    rest = value[: found.start()] + " " + value[found.end() :]
    clock = _TIME.search(rest)
    if clock is not None:
        hour, minute = int(clock["h"]), int(clock["min"])
        second = int(clock["s"]) if clock["s"] else 0
        ampm = clock["ampm"]
        if ampm is not None:
            if not 1 <= hour <= 12:
                return None
            pm = ampm in ("下午", "PM", "pm")
            hour = hour % 12 + (12 if pm else 0)
    try:
        local = datetime(year, int(found["m"]), int(found["d"]), hour, minute, second)
    except ValueError:
        return None
    return to_utc(local)


# --- 文字清洗 --------------------------------------------------------------------------

# 中文常用的全形標點：保留，其他全形 ASCII（U+FF01–FF5E）轉半形
_KEEP_FULLWIDTH = frozenset("！（），：；？")
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_SPACES = re.compile(r"[^\S\n]+")  # 換行以外的空白（含全形空白、不換行空白）
_BLANK_LINES = re.compile(r"\n{3,}")


def _halfwidth(text: str, *, keep: frozenset[str]) -> str:
    chars = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E and ch not in keep:
            chars.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            chars.append(" ")
        else:
            chars.append(ch)
    return "".join(chars)


def clean_text(text: str) -> str:
    """清洗純文字：NFC、全形英數轉半形、移除不可見與控制字元、收斂空白與空行。"""
    text = unicodedata.normalize("NFC", text)
    text = _halfwidth(text, keep=_KEEP_FULLWIDTH)
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE)
    text = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip("\n")


_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "head", "iframe", "svg"})
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "header", "footer", "aside", "main", "nav",
        "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "figure", "figcaption",
        "hr", "address",
    }
)  # fmt: skip
_CELL_TAGS = frozenset({"td", "th"})


def _collect(node: LexborNode, out: list[str], *, preformatted: bool) -> None:
    child = node.child
    while child is not None:
        tag = child.tag
        if tag == "-text":
            text = child.text_content or ""
            out.append(text if preformatted else _SPACES.sub(" ", text.replace("\n", " ")))
        elif tag is None or tag.startswith("-") or tag in _SKIP_TAGS:
            pass  # 註解、腳本、樣式
        elif tag == "br":
            out.append("\n")
        elif tag in _BLOCK_TAGS:
            out.append("\n\n")
            _collect(child, out, preformatted=preformatted or tag == "pre")
            out.append("\n\n")
        elif tag in _CELL_TAGS:
            out.append(" ")
            _collect(child, out, preformatted=preformatted)
            out.append(" ")
        else:
            _collect(child, out, preformatted=preformatted)
        child = child.next


def html_to_text(html: str) -> str:
    """HTML 轉純文字：區塊元素以空行分段、`<br>` 換行；略過腳本、樣式與註解。"""
    tree = LexborHTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return ""
    out: list[str] = []
    _collect(root, out, preformatted=False)
    return clean_text("".join(out))


# --- 語系與雜湊 ------------------------------------------------------------------------

_LATIN_WORD = re.compile(r"[A-Za-z]+")


def _is_han(ch: str) -> bool:
    code = ord(ch)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF


def _is_other_script(ch: str) -> bool:
    code = ord(ch)
    return 0x3040 <= code <= 0x30FF or 0xAC00 <= code <= 0xD7AF  # 假名、韓文


def detect_language(text: str) -> Language:
    """以字元統計判斷語系：漢字數與英文單字數比較，較多者勝；假名或韓文較多、或無法判斷時為 und。"""
    han = sum(1 for ch in text if _is_han(ch))
    other = sum(1 for ch in text if _is_other_script(ch))
    words = len(_LATIN_WORD.findall(text))
    if han > words and han > other:
        return "zh"
    if words > han and words > other:
        return "en"
    return "und"


def content_hash(normalized_text: str) -> str:
    """正規化文字的 SHA-256（UTF-8，hex 64 字元）。"""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


# --- 整篇標準化 ------------------------------------------------------------------------


class NormalizedItem(BaseModel):
    """標準化後的一篇文章，供冪等寫入（P1-T06）與後續 AI 處理使用；建立後不可修改。

    `text` 仍是來源內容，屬於不可信輸入，送進模型前必須以 <untrusted_source> 包裹。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_code: str
    external_id: str | None
    url: str
    title: str | None
    text: str
    language: Language
    content_hash: str
    published_at: AwareDatetime | None
    fetched_at: AwareDatetime
    tags: frozenset[str] = frozenset()


def normalize_item(raw: RawItem) -> NormalizedItem:
    """標準化 Adapter 的原始項目；標題與內文清洗後都沒有文字時拋出 ValueError。"""
    title = clean_text(raw.title) if raw.title else ""
    body = html_to_text(raw.content) if raw.content_type == "text/html" else clean_text(raw.content)
    parts = [title] if title else []
    if body and body != title:
        parts.append(body)
    text = "\n\n".join(parts)
    if not text:
        raise ValueError(f"來源 {raw.source_code} 的項目清洗後沒有任何文字")
    return NormalizedItem(
        source_code=raw.source_code,
        external_id=raw.external_id,
        url=raw.url,
        title=title or None,
        text=text,
        language=detect_language(text),
        content_hash=content_hash(text),
        published_at=to_utc(raw.published_at) if raw.published_at is not None else None,
        fetched_at=to_utc(raw.fetched_at),
        tags=frozenset(t for t in (clean_text(tag) for tag in raw.tags) if t),
    )
