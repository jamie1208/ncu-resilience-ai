"""S07／S08 的衍生欄位（P2-T12）：假設與事實區分的自動代理指標，評測時併入輸出再比對。

- S07 root-cause-analyst：`cause_basis`（原因全為 confirmed／hypothesized，或兩者都有 mixed；
  abstain 或沒有原因為 None）、`unsupported_confirmed`（有 confirmed 原因卻沒有逐字原句）。
- S08 solution-planner：`basis_by_cause`（措施回填的原因性質，依 cause_id 排序成
  「c1=confirmed;c2=hypothesized」，同一原因回填不一致記為 conflict）、
  `hypotheses_investigated`（每個待查證原因都至少有一項 investigate 措施）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _items(output: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = output.get(key) or []
    return [x for x in value if isinstance(x, Mapping)]


def root_cause_fields(output: Mapping[str, Any]) -> dict[str, Any]:
    """S07 輸出的衍生欄位。"""
    causes = _items(output, "causes") if output.get("status") == "ok" else []
    bases = {c.get("basis") for c in causes}
    basis: str | None
    if not bases:
        basis = None
    elif len(bases) > 1:
        basis = "mixed"
    else:
        basis = str(bases.pop())
    unsupported = any(c.get("basis") == "confirmed" and not c.get("evidence_spans") for c in causes)
    return {"cause_basis": basis, "unsupported_confirmed": unsupported}


def solution_plan_fields(output: Mapping[str, Any]) -> dict[str, Any]:
    """S08 輸出的衍生欄位；abstain 時都是 None。"""
    if output.get("status") != "ok":
        return {"basis_by_cause": None, "hypotheses_investigated": None}
    by_cause: dict[str, set[str]] = {}
    investigated: set[str] = set()
    for m in _items(output, "measures"):
        cause_id = m.get("cause_id")
        if cause_id is None:
            continue
        by_cause.setdefault(str(cause_id), set()).add(str(m.get("cause_basis")))
        if m.get("kind") == "investigate":
            investigated.add(str(cause_id))
    labels = {c: (b.pop() if len(b) == 1 else "conflict") for c, b in by_cause.items()}
    hypotheses = {c for c, b in labels.items() if b == "hypothesized"}
    return {
        "basis_by_cause": ";".join(f"{c}={labels[c]}" for c in sorted(labels)),
        "hypotheses_investigated": hypotheses <= investigated,
    }
