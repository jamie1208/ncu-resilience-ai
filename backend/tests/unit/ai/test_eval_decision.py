"""S07／S08 的衍生欄位（P2-T12）：假設與事實區分的自動代理指標。

- S07 `cause_basis`：原因全為 confirmed／全為 hypothesized／兩者都有（mixed）；
  沒有原因或 abstain 為 None。
  `unsupported_confirmed`：有 confirmed 原因卻沒有逐字原句。
- S08 `basis_by_cause`：措施回填的原因性質，依 cause_id 排序成「c1=confirmed;c2=hypothesized」；
  同一原因回填不一致記為 conflict。
  `hypotheses_investigated`：每個待查證原因都至少有一項 investigate 措施。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.ai.eval import runner
from app.ai.eval.derived import root_cause_fields, solution_plan_fields

REPO = Path(__file__).resolve().parents[4]
SPAN = [{"evidence_id": "e1", "quote": "原因"}]


def _cause(cause_id: str, basis: str, spans: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "cause_id": cause_id,
        "description": "…",
        "basis": basis,
        "evidence_ids": ["e1"],
        "evidence_spans": SPAN if spans is None else spans,
        "to_verify": None if basis == "confirmed" else "查證",
    }


def _rca(*causes: dict[str, Any], status: str = "ok") -> dict[str, Any]:
    return {"status": status, "causes": list(causes)}


@pytest.mark.parametrize(
    ("bases", "expected"),
    [
        (["confirmed"], "confirmed"),
        (["confirmed", "confirmed"], "confirmed"),
        (["hypothesized"], "hypothesized"),
        (["hypothesized", "confirmed"], "mixed"),
        ([], None),
    ],
)
def test_cause_basis(bases: list[str], expected: str | None) -> None:
    output = _rca(*(_cause(f"c{i}", b) for i, b in enumerate(bases, 1)))
    assert root_cause_fields(output)["cause_basis"] == expected


def test_abstain_has_no_basis() -> None:
    fields = root_cause_fields(_rca(status="abstain"))
    assert fields == {"cause_basis": None, "unsupported_confirmed": False}


def test_confirmed_without_quote_is_unsupported() -> None:
    assert root_cause_fields(_rca(_cause("c1", "confirmed", spans=[])))["unsupported_confirmed"]
    assert not root_cause_fields(_rca(_cause("c1", "confirmed")))["unsupported_confirmed"]
    # 待查證的原因沒有原句不算（但仍需要 evidence_ids）
    assert not root_cause_fields(_rca(_cause("c1", "hypothesized", spans=[])))[
        "unsupported_confirmed"
    ]


def _measure(cause_id: str | None, basis: str | None, kind: str = "respond") -> dict[str, Any]:
    return {"cause_id": cause_id, "cause_basis": basis, "kind": kind}


def _plan(*measures: dict[str, Any], status: str = "ok") -> dict[str, Any]:
    return {"status": status, "measures": list(measures)}


def test_basis_by_cause_is_sorted_and_deduplicated() -> None:
    output = _plan(
        _measure("c2", "hypothesized", "investigate"),
        _measure("c1", "confirmed"),
        _measure("c1", "confirmed", "prevent"),
        _measure(None, None, "respond"),
    )
    fields = solution_plan_fields(output)
    assert fields["basis_by_cause"] == "c1=confirmed;c2=hypothesized"
    assert fields["hypotheses_investigated"] is True


def test_inconsistent_basis_is_conflict() -> None:
    output = _plan(_measure("c1", "confirmed"), _measure("c1", "hypothesized", "investigate"))
    assert solution_plan_fields(output)["basis_by_cause"] == "c1=conflict"


def test_measures_without_causes_give_empty_string() -> None:
    fields = solution_plan_fields(_plan(_measure(None, None)))
    assert fields == {"basis_by_cause": "", "hypotheses_investigated": True}


def test_hypothesis_without_investigation_is_flagged() -> None:
    output = _plan(_measure("c1", "hypothesized", "prevent"))
    assert solution_plan_fields(output)["hypotheses_investigated"] is False


def test_abstain_plan_has_no_fields() -> None:
    fields = solution_plan_fields(_plan(status="abstain"))
    assert fields == {"basis_by_cause": None, "hypotheses_investigated": None}


@pytest.mark.parametrize(
    ("name", "deriver", "fields"),
    [
        ("root-cause-analyst", root_cause_fields, {"cause_basis", "unsupported_confirmed"}),
        (
            "solution-planner",
            solution_plan_fields,
            {"basis_by_cause", "hypotheses_investigated"},
        ),
    ],
)
def test_registered_and_declared(name: str, deriver: Any, fields: set[str]) -> None:
    assert runner.DERIVERS[name] is deriver
    text = (REPO / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1])["metadata"]
    declared = {f.strip() for f in meta["derived_fields"].split(",")}
    assert declared == fields
