"""巢狀證據的業務驗證（P2-T12）。

S07 每個原因、S08 每項措施各自帶 `evidence_ids`／`evidence_spans`；
Runner 除了最外層，也要檢查巢狀的 evidence_id 存在、原句確實出現在證據中。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ai.runner.loader import load_skill
from app.ai.runner.validate import validate_output

REPO = Path(__file__).resolve().parents[4]
EVIDENCE = {"e1": "總務處說明：昨日停電原因為變電箱老舊跳脫，已更換設備。"}


def _cause(**overrides: Any) -> dict[str, Any]:
    cause = {
        "cause_id": "c1",
        "description": "變電箱老舊跳脫",
        "basis": "confirmed",
        "evidence_ids": ["e1"],
        "evidence_spans": [{"evidence_id": "e1", "quote": "停電原因為變電箱老舊跳脫"}],
        "to_verify": None,
    }
    return cause | overrides


def _output(*causes: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "causes": list(causes),
        "missing_information": [],
        "evidence_ids": ["e1"],
        "reason": "官方說明了停電原因。",
        "abstain_reason": None,
    }


def _issues(output: dict[str, Any]) -> list[tuple[str, str]]:
    skill = load_skill(REPO / "skills" / "root-cause-analyst")
    issues = validate_output(output, skill, EVIDENCE.keys(), evidence_texts=EVIDENCE)
    return [(i.code, i.detail) for i in issues]


def test_valid_nested_evidence_passes() -> None:
    assert _issues(_output(_cause())) == []


def test_nested_unknown_evidence_id_is_rejected() -> None:
    issues = _issues(_output(_cause(evidence_ids=["e1", "e9"])))
    assert [k for k, _ in issues] == ["evidence"]
    assert "/causes/0/evidence_ids" in issues[0][1]


def test_nested_span_with_unknown_evidence_id_is_rejected() -> None:
    spans = [{"evidence_id": "e9", "quote": "停電原因為變電箱老舊跳脫"}]
    issues = _issues(_output(_cause(evidence_spans=spans)))
    assert [k for k, _ in issues] == ["evidence"]
    assert "/causes/0/evidence_spans/0" in issues[0][1]


def test_nested_span_not_in_evidence_is_rejected() -> None:
    spans = [{"evidence_id": "e1", "quote": "廠商施工不當導致停電"}]
    issues = _issues(_output(_cause(evidence_spans=spans)))
    assert [k for k, _ in issues] == ["span"]
    assert "/causes/0/evidence_spans/0" in issues[0][1]


def test_each_nested_item_is_checked() -> None:
    bad = _cause(cause_id="c2", basis="hypothesized", evidence_ids=["e7"], to_verify="查維修紀錄")
    issues = _issues(_output(_cause(), bad))
    assert len(issues) == 1
    assert "/causes/1/evidence_ids" in issues[0][1]
