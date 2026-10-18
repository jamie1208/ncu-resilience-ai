"""P1-T16：HTML Adapter 共用的解析小工具。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from selectolax.lexbor import LexborHTMLParser

from app.ingestion.html import TAIPEI, clean, find_date, first, taipei_midnight
from app.ingestion.normalize import TAIPEI as NORMALIZE_TAIPEI


def test_first_returns_first_match_or_none() -> None:
    tree = LexborHTMLParser("<div><p>一</p><p>二</p></div>")
    node = first(tree, "p")
    assert node is not None and node.text() == "一"
    assert first(tree, "span") is None


def test_clean_collapses_whitespace() -> None:
    assert clean("  停水\n\t公告　 \n") == "停水 公告"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("發布日期：2026-10-17", date(2026, 10, 17)),
        ("2026-10-17 15:22:04", date(2026, 10, 17)),
        ("公告 2026-02-30", None),  # 無效日期
        ("10/17", None),
        ("", None),
    ],
)
def test_find_date(text: str, expected: date | None) -> None:
    assert find_date(text) == expected


def test_taipei_midnight() -> None:
    assert taipei_midnight(date(2026, 10, 17)) == datetime(2026, 10, 16, 16, 0, tzinfo=UTC)
    assert taipei_midnight(None) is None


def test_taipei_is_the_normalize_constant() -> None:
    assert TAIPEI is NORMALIZE_TAIPEI
