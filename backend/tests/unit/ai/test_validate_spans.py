"""P2-T06：evidence_spans 的原句必須確實出現在該證據中（使用者 2026-10-20 決定）。

模型只抄錄原句（{evidence_id, quote}），字元位置由程式計算；比對前會把連續空白視為一個空格。
這是通用規則：任何 Skill 的輸出只要有 evidence_spans 都適用（S01、S02）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.ai.runner.loader import Skill
from app.ai.runner.validate import locate_quote, validate_output

SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "evidence_ids", "evidence_spans", "reason", "abstain_reason"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "abstain"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_spans": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["evidence_id", "quote"],
                "properties": {"evidence_id": {"type": "string"}, "quote": {"type": "string"}},
            },
        },
        "reason": {"type": "string"},
        "abstain_reason": {"type": ["string", "null"]},
    },
}
SKILL = Skill(
    name="demo",
    version="0.1.0",
    schema_version="1",
    description="",
    root=Path("."),
    instructions="",
    references=(),
    input_schema={},
    output_schema=SCHEMA,
)
TEXTS = {
    "e1": "宿舍網路將於 10/25（六）02:00–06:00\n進行設備更新，期間暫停服務。",
    "e2": "圖書館寒假開館時間調整。",
}


def output(*spans: dict[str, str]) -> dict[str, Any]:
    return {
        "status": "ok",
        "evidence_ids": ["e1"],
        "evidence_spans": list(spans),
        "reason": "宿舍網路暫停服務。",
        "abstain_reason": None,
    }


def codes(out: dict[str, Any]) -> set[str]:
    return {i.code for i in validate_output(out, SKILL, set(TEXTS), evidence_texts=TEXTS)}


def test_exact_quote_passes() -> None:
    assert codes(output({"evidence_id": "e1", "quote": "期間暫停服務"})) == set()


def test_whitespace_differences_are_ignored() -> None:
    quote = "02:00–06:00 進行設備更新"  # 原文是換行
    assert codes(output({"evidence_id": "e1", "quote": quote})) == set()


def test_quote_not_in_evidence_is_rejected() -> None:
    assert codes(output({"evidence_id": "e1", "quote": "全校停課"})) == {"span"}


def test_quote_from_another_evidence_is_rejected() -> None:
    assert codes(output({"evidence_id": "e1", "quote": "寒假開館時間調整"})) == {"span"}


def test_span_with_unknown_evidence_id() -> None:
    assert "evidence" in codes(output({"evidence_id": "e9", "quote": "期間暫停服務"}))


def test_empty_quote_is_rejected() -> None:
    assert codes(output({"evidence_id": "e1", "quote": "  "})) == {"span"}


def test_issue_detail_does_not_contain_the_quote() -> None:
    issues = validate_output(
        output({"evidence_id": "e1", "quote": "全校停課"}), SKILL, set(TEXTS), evidence_texts=TEXTS
    )
    assert all("全校停課" not in i.detail for i in issues)


def test_without_texts_spans_are_not_checked() -> None:
    # 舊的呼叫方式（P2-T04）不傳 evidence_texts：不檢查原句
    out = output({"evidence_id": "e1", "quote": "全校停課"})
    assert {i.code for i in validate_output(out, SKILL, set(TEXTS))} == set()


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("期間暫停服務", (34, 40)),
        ("02:00–06:00 進行設備更新", (15, 33)),
        ("不存在", None),
    ],
)
def test_locate_quote_returns_character_offsets(quote: str, expected: Any) -> None:
    span = locate_quote(TEXTS["e1"], quote)
    assert span == expected
    if span is not None:
        start, end = span
        assert " ".join(TEXTS["e1"][start:end].split()) == " ".join(quote.split())
