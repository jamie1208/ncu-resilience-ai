"""個資檢查不誤判 evidence_id（P2-T13 發現）。

管線以文章 id（32 位十六進位）當 evidence_id，可能含 9 位以上的連續數字，看起來像學號或電話。
evidence_id 是 Runner 指定的值，只有確實存在於輸入時才略過個資檢查；其他欄位照常檢查。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ai.runner.loader import load_skill
from app.ai.runner.validate import validate_output

REPO = Path(__file__).resolve().parents[4]
SKILL = load_skill(REPO / "skills" / "evidence-verifier")
DIGITS_ID = "0912345678ab4cdef0123456789abcde"  # 含手機號碼與學號樣式的連續數字
TEXT = "科學一館今天停電。"


def _output(evidence_id: str, reason: str = "官方公告說明停電。") -> dict[str, Any]:
    return {
        "status": "ok",
        "assessments": [
            {"evidence_id": evidence_id, "stance": "supports", "claim_type": "official"}
        ],
        "suggested_status": "verified",
        "confidence": 0.9,
        "evidence_ids": [evidence_id],
        "evidence_spans": [{"evidence_id": evidence_id, "quote": "科學一館今天停電"}],
        "reason": reason,
        "abstain_reason": None,
    }


def _codes(output: dict[str, Any], known: list[str]) -> list[str]:
    texts = dict.fromkeys(known, TEXT)
    return [i.code for i in validate_output(output, SKILL, known, evidence_texts=texts)]


def test_known_evidence_id_with_digits_is_not_pii() -> None:
    assert _codes(_output(DIGITS_ID), [DIGITS_ID]) == []


def test_unknown_evidence_id_is_still_checked_for_pii() -> None:
    output = _output(DIGITS_ID)
    output["assessments"][0]["evidence_id"] = "0912345678"

    assert "pii" in _codes(output, [DIGITS_ID])


def test_other_fields_are_still_checked_for_pii() -> None:
    assert "pii" in _codes(_output(DIGITS_ID, reason="聯絡 0912345678"), [DIGITS_ID])
