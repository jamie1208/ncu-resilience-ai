"""P1-T05：標準化（時間、文字清洗、語系、內容雜湊）。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.ingestion.normalize import (
    NormalizedItem,
    clean_text,
    content_hash,
    detect_language,
    html_to_text,
    normalize_item,
    parse_datetime,
    to_utc,
)
from app.ingestion.types import RawItem

NOW = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)
TAIPEI = timezone(timedelta(hours=8))


def raw(**overrides: Any) -> RawItem:
    fields: dict[str, Any] = {
        "source_code": "ncu-announcements",
        "external_id": "5003",
        "url": "https://www.ncu.edu.tw/p/406-1000-5003,r15.php?Lang=zh-tw",
        "title": "宿舍區 10/20 停水通知",
        "content": "因管線檢修，宿舍區 10/20 09:00–15:00 停水。",
        "content_type": "text/plain",
        "published_at": datetime(2026, 10, 16, 16, 0, tzinfo=UTC),
        "fetched_at": NOW,
    }
    fields.update(overrides)
    return RawItem(**fields)


# --- 時間 ------------------------------------------------------------------------------


def test_to_utc_converts_aware_datetime() -> None:
    value = to_utc(datetime(2026, 10, 17, 20, 0, tzinfo=TAIPEI))
    assert value == datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert value.tzinfo is UTC


def test_to_utc_treats_naive_as_taipei() -> None:
    # 無時區：校方網站與匯入資料都是台北時間
    assert to_utc(datetime(2026, 10, 17, 8, 0)) == datetime(2026, 10, 17, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 西元年
        ("2026-10-17", datetime(2026, 10, 16, 16, 0, tzinfo=UTC)),
        ("2026/10/17 14:30", datetime(2026, 10, 17, 6, 30, tzinfo=UTC)),
        ("2026.10.17 14:30:15", datetime(2026, 10, 17, 6, 30, 15, tzinfo=UTC)),
        ("2026年10月17日", datetime(2026, 10, 16, 16, 0, tzinfo=UTC)),
        # ISO 8601：帶時區照用，不帶時區視為台北時間
        ("2026-10-17T14:30:00+00:00", datetime(2026, 10, 17, 14, 30, tzinfo=UTC)),
        ("2026-10-17T14:30:00Z", datetime(2026, 10, 17, 14, 30, tzinfo=UTC)),
        ("2026-10-17T14:30:00", datetime(2026, 10, 17, 6, 30, tzinfo=UTC)),
        # 民國年（115 = 2026）
        ("115/10/17", datetime(2026, 10, 16, 16, 0, tzinfo=UTC)),
        ("115-10-17 08:00", datetime(2026, 10, 17, 0, 0, tzinfo=UTC)),
        ("115年10月17日", datetime(2026, 10, 16, 16, 0, tzinfo=UTC)),
        ("民國115年10月17日", datetime(2026, 10, 16, 16, 0, tzinfo=UTC)),
        ("中華民國 115 年 1 月 5 日", datetime(2026, 1, 4, 16, 0, tzinfo=UTC)),
        ("99/1/5", datetime(2010, 1, 4, 16, 0, tzinfo=UTC)),
        # 上午／下午
        ("115年10月17日 下午2:30", datetime(2026, 10, 17, 6, 30, tzinfo=UTC)),
        ("115年10月17日 上午12:05", datetime(2026, 10, 16, 16, 5, tzinfo=UTC)),
        ("115年10月17日 下午12:05", datetime(2026, 10, 17, 4, 5, tzinfo=UTC)),
        ("2026/10/17 下午2時30分", datetime(2026, 10, 17, 6, 30, tzinfo=UTC)),
        # 全形數字與符號
        ("１１５／１０／１７", datetime(2026, 10, 16, 16, 0, tzinfo=UTC)),
        ("２０２６－１０－１７　１４：３０", datetime(2026, 10, 17, 6, 30, tzinfo=UTC)),
        # 前後有文字
        ("發布日期 : 2026-10-17 ", datetime(2026, 10, 16, 16, 0, tzinfo=UTC)),
    ],
)
def test_parse_datetime(text: str, expected: datetime) -> None:
    value = parse_datetime(text)
    assert value == expected
    assert value is not None
    assert value.tzinfo is UTC


@pytest.mark.parametrize(
    "text",
    [
        "",
        "近期",
        "2026-13-01",  # 月份無效
        "115/02/30",  # 日期無效
        "2026-10-17 25:00",  # 時間無效
        "下午 13:00 2026-10-17",  # 下午 13 點無效
        "10/17",  # 缺年份
        "0/10/17",  # 民國 0 年
    ],
)
def test_parse_datetime_rejects_invalid(text: str) -> None:
    assert parse_datetime(text) is None


# --- 文字清洗 --------------------------------------------------------------------------


def test_clean_text_converts_fullwidth_alphanumerics() -> None:
    assert clean_text("ＮＣＵ　Ｅ１棟　３Ｆ") == "NCU E1棟 3F"


def test_clean_text_converts_fullwidth_symbols_but_keeps_cjk_punctuation() -> None:
    # 中文常用的全形標點（，：；！？（））保留，其他全形符號轉半形
    assert clean_text("停水，請注意！時間：１０／２０（一）") == "停水，請注意！時間：10/20（一）"
    assert clean_text("１００％　＃公告　ａ＠ｂ．ｃｏｍ") == "100% #公告 a@b.com"


def test_clean_text_normalizes_whitespace() -> None:
    text = "  第一行\t\t有  空白 \r\n\r\n\r\n\n第二段 內容\n  "
    assert clean_text(text) == "第一行 有 空白\n\n第二段 內容"


def test_clean_text_removes_invisible_characters() -> None:
    assert clean_text("停​水﻿通‍知\x00\x07") == "停水通知"


def test_clean_text_composes_unicode() -> None:
    # NFC：e + 組合重音 → é
    assert clean_text("Café") == "Café"


def test_clean_text_is_idempotent() -> None:
    once = clean_text("ＮＣＵ　停水​通知\r\n\r\n\r\n細節")
    assert clean_text(once) == once


def test_html_to_text_keeps_block_structure() -> None:
    html = (
        "<div class='meditor'><p>第一段 <strong>重點</strong></p>"
        "<p>第二段<br>換行</p><ul><li>項目一</li><li>項目二</li></ul>"
        "<table><tr><td>A</td><td>B</td></tr></table></div>"
    )
    assert html_to_text(html) == "第一段 重點\n\n第二段\n換行\n\n項目一\n\n項目二\n\nA B"


def test_html_to_text_drops_scripts_styles_and_decodes_entities() -> None:
    html = (
        "<style>p{color:red}</style><script>alert('x')</script>"
        "<p>A&amp;B&nbsp;&lt;C&gt;</p><!-- 註解 -->"
    )
    assert html_to_text(html) == "A&B <C>"


# --- 語系 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("宿舍區 10/20 停水通知", "zh"),
        ("A Sharp Bound for Cancellative Pairs of Set Families", "en"),
        ("[演講公告] A Sharp Bound for Cancellative Pairs", "en"),  # 英文為主
        ("【轉知】Summer School 2027 報名開始，請踴躍參加", "zh"),  # 中文為主
        ("2026-10-17 12:00", "und"),
        ("", "und"),
        ("こんにちは、ありがとう", "und"),  # 其他語言不猜
    ],
)
def test_detect_language(text: str, expected: str) -> None:
    assert detect_language(text) == expected


# --- 內容雜湊 --------------------------------------------------------------------------


def test_content_hash_is_sha256_hex_of_utf8() -> None:
    expected = hashlib.sha256("停水通知".encode()).hexdigest()
    assert content_hash("停水通知") == expected
    assert len(content_hash("x")) == 64


# --- normalize_item --------------------------------------------------------------------


def test_normalize_plain_item() -> None:
    item = normalize_item(raw())
    assert isinstance(item, NormalizedItem)
    assert item.source_code == "ncu-announcements"
    assert item.external_id == "5003"
    assert item.url == "https://www.ncu.edu.tw/p/406-1000-5003,r15.php?Lang=zh-tw"
    assert item.title == "宿舍區 10/20 停水通知"
    assert item.text == "宿舍區 10/20 停水通知\n\n因管線檢修，宿舍區 10/20 09:00–15:00 停水。"
    assert item.language == "zh"
    assert item.content_hash == content_hash(item.text)
    assert item.published_at == datetime(2026, 10, 16, 16, 0, tzinfo=UTC)
    assert item.fetched_at == NOW


def test_normalize_html_item() -> None:
    item = normalize_item(
        raw(
            content="<div class='meditor'><p>停水&nbsp;公告</p><p>請儲水</p></div>",
            content_type="text/html",
        )
    )
    assert item.text == "宿舍區 10/20 停水通知\n\n停水 公告\n\n請儲水"


def test_formatting_only_changes_keep_the_same_hash() -> None:
    a = normalize_item(raw(content="停水公告\n請儲水"))
    b = normalize_item(raw(content="  停水公告 \r\n請儲水​  "))
    c = normalize_item(raw(content="<p>停水公告<br>請儲水</p>", content_type="text/html"))
    assert a.content_hash == b.content_hash == c.content_hash


def test_fullwidth_and_halfwidth_versions_have_the_same_hash() -> None:
    a = normalize_item(raw(content="E1 棟 3F 停電"))
    b = normalize_item(raw(content="Ｅ１　棟　３Ｆ　停電"))
    assert a.content_hash == b.content_hash


def test_content_change_changes_hash() -> None:
    a = normalize_item(raw(content="10/20 停水"))
    b = normalize_item(raw(content="10/21 停水"))
    assert a.content_hash != b.content_hash


def test_title_change_changes_hash() -> None:
    assert (
        normalize_item(raw(title="停水")).content_hash
        != normalize_item(raw(title="停水（延期）")).content_hash
    )


def test_content_equal_to_title_is_not_duplicated() -> None:
    # Adapter 在沒有內文時會以標題當內文
    item = normalize_item(raw(title="講座延期", content="講座延期"))
    assert item.text == "講座延期"


def test_item_without_title_uses_content_only() -> None:
    item = normalize_item(raw(title=None))
    assert item.title is None
    assert item.text == "因管線檢修，宿舍區 10/20 09:00–15:00 停水。"


def test_empty_html_falls_back_to_title() -> None:
    item = normalize_item(raw(content="<p>&nbsp;</p>", content_type="text/html"))
    assert item.text == "宿舍區 10/20 停水通知"


def test_item_without_any_text_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_item(raw(title=None, content="<p>&nbsp;</p>", content_type="text/html"))


def test_normalize_keeps_times_as_aware_utc() -> None:
    item = normalize_item(raw(published_at=None))
    assert item.published_at is None
    assert item.fetched_at.tzinfo is UTC


def test_normalized_item_is_immutable() -> None:
    item: Any = normalize_item(raw())
    with pytest.raises(ValueError):
        item.text = "改過"
