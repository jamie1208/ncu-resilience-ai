"""S07／S08 的衍生欄位（P2-T12）：假設與事實區分的自動代理指標，評測時併入輸出再比對。

- S07 root-cause-analyst：`cause_basis`（原因全為 confirmed／hypothesized，或兩者都有 mixed；
  abstain 或沒有原因為 None）、`unsupported_confirmed`（有 confirmed 原因卻沒有逐字原句）。
- S08 solution-planner：`basis_by_cause`（措施回填的原因性質，依 cause_id 排序成
  「c1=confirmed;c2=hypothesized」，同一原因回填不一致記為 conflict）、
  `hypotheses_investigated`（每個待查證原因都至少有一項 investigate 措施）。
- S09 trend-forecaster（P7-T01）：`explained_signals`（有解釋的統計訊號，依 signal_id 排序成
  「s1;s3」，abstain 為 None）、`caveats_complete`（標註聲量≠事件數，覆蓋率不是 full 時也標註
  limited_coverage）。
- S10 scenario-simulator（P7-T02）：`explained_measures`（有解釋的措施，依 measure_id 排序成
  「m1;m2」，abstain 為 None）、`limitation_set`（限制標註依代碼排序，abstain 時同樣比對）、
  `hypothetical_flagged`（標明假設情境、不是成效預測）。
- S12 series-analyst（P8-T05）：`patterns_supported`（每個規律都至少引用 2 起不同的子事件；
  沒有規律時為 True）、`cited_events`（所有區塊引用的子事件代號，排序去重的清單，用來檢查
  是否引用輸入中不存在的事件）。
- S14 impact-evaluator（P8-T15）：`improvement_valid`（ok 時改善百分比為
  `0 ≤ low ≤ high ≤ improvement_cap` 且至少 1 項風險，abstain 時為 null）、`cause_coverage`
  （「covered=c1;c2|uncovered=c3」，原因代號排序）、`hypothesized_not_asserted`（文字沒有把推測
  寫成已確定的說法）。abstain 時同樣計算。
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


def trend_forecast_fields(output: Mapping[str, Any]) -> dict[str, Any]:
    """S09 輸出的衍生欄位；`caveats_complete` 在 abstain 時同樣檢查。"""
    caveats = set(output.get("caveats") or [])
    complete = "volume_not_event_count" in caveats and (
        output.get("coverage_level") == "full" or "limited_coverage" in caveats
    )
    explained: str | None = None
    if output.get("status") == "ok":
        ids = {str(e.get("signal_id")) for e in _items(output, "explanations")}
        explained = ";".join(sorted(ids))
    return {"explained_signals": explained, "caveats_complete": complete}


def scenario_fields(output: Mapping[str, Any]) -> dict[str, Any]:
    """S10 輸出的衍生欄位；`limitation_set` 與 `hypothetical_flagged` 在 abstain 時同樣計算。"""
    limitations = {str(x) for x in output.get("limitations") or []}
    explained: str | None = None
    if output.get("status") == "ok":
        ids = {str(a.get("measure_id")) for a in _items(output, "assumptions")}
        explained = ";".join(sorted(ids))
    return {
        "explained_measures": explained,
        "limitation_set": ";".join(sorted(limitations)),
        "hypothetical_flagged": "hypothetical_not_prediction" in limitations,
    }


_SERIES_SECTIONS = ("burst_explanations", "location_groups", "patterns", "causes")


def series_analysis_fields(output: Mapping[str, Any]) -> dict[str, Any]:
    """S12 輸出的衍生欄位；abstain 時同樣計算。"""
    patterns = _items(output, "patterns")
    supported = all(len({str(e) for e in p.get("event_ids") or []}) >= 2 for p in patterns)
    cited = {
        str(e)
        for key in _SERIES_SECTIONS
        for x in _items(output, key)
        for e in x.get("event_ids") or []
    }
    return {"patterns_supported": supported, "cited_events": sorted(cited)}


def impact_fields(output: Mapping[str, Any]) -> dict[str, Any]:
    """S14 輸出的衍生欄位；abstain 時同樣計算。上限讀 `config/impact.yaml`。"""
    from app.simulations.impact import (  # 只在評測時載入
        asserts_hypothesis,
        improvement_valid,
        load_impact_config,
        output_texts,
    )

    def ids(key: str) -> str:
        return ";".join(sorted({str(c) for c in output.get(key) or []}))

    return {
        "improvement_valid": improvement_valid(output, load_impact_config().improvement_cap),
        "cause_coverage": f"covered={ids('covered_causes')}|uncovered={ids('uncovered_causes')}",
        "hypothesized_not_asserted": not asserts_hypothesis(output_texts(output)),
    }
