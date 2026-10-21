"""評測指標（P2-T05）：Macro-F1、Precision／Recall、配對式合併指標，以及 golden case 的判定。

P2-T10 加上計次指標 `unsupported_count`（S05：無 evidence_ids 被標 verified 的次數）。

無法計算時（例如沒有任何正類）回傳 None，由呼叫端視為未達標，不當成 0 或 1。
`must_not` 規則格式見 docs/architecture/skill-standard.md §7。
"""

from __future__ import annotations

import json
import re
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any


@dataclass(frozen=True)
class PRF:
    """Precision、Recall、F1 與正類的 gold 數量；無法計算的值為 None。"""

    precision: float | None
    recall: float | None
    f1: float | None
    support: int


def _check_lengths(gold: Sequence[Any], pred: Sequence[Any]) -> None:
    if len(gold) != len(pred):
        raise ValueError(f"gold 有 {len(gold)} 筆、pred 有 {len(pred)} 筆，長度必須相同")


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return 0.0 if (precision == 0.0 or recall == 0.0) else None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def precision_recall_f1(gold: Sequence[Any], pred: Sequence[Any], *, positive: Any) -> PRF:
    """以 `positive` 為正類的 Precision、Recall、F1。"""
    _check_lengths(gold, pred)
    tp = sum(1 for g, p in zip(gold, pred, strict=True) if g == positive and p == positive)
    predicted = sum(1 for p in pred if p == positive)
    support = sum(1 for g in gold if g == positive)
    precision = tp / predicted if predicted else None
    recall = tp / support if support else None
    return PRF(precision, recall, _f1(precision, recall), support)


def macro_f1(gold: Sequence[Hashable], pred: Sequence[Hashable]) -> float | None:
    """各類別 F1 的平均；類別為 gold 與 pred 出現過的所有值（含 None）。"""
    _check_lengths(gold, pred)
    if not gold:
        return None
    labels = list(dict.fromkeys([*gold, *pred]))
    scores = [precision_recall_f1(gold, pred, positive=label).f1 or 0.0 for label in labels]
    return sum(scores) / len(scores)


def accuracy(gold: Sequence[Any], pred: Sequence[Any]) -> float | None:
    """完全相等的比例。"""
    _check_lengths(gold, pred)
    if not gold:
        return None
    return sum(1 for g, p in zip(gold, pred, strict=True) if g == p) / len(gold)


def unsupported_count(
    outputs: Sequence[Mapping[str, Any] | None], *, field: str, positive: Any
) -> int:
    """`field` 等於 `positive`、卻沒有任何支持證據的輸出數（沒有通過驗證的輸出不計）。

    支持證據：`assessments` 中 stance 為 supports、且 evidence_id 也列在 `evidence_ids` 中的項目。
    """
    count = 0
    for output in outputs:
        if output is None or output.get(field) != positive:
            continue
        cited = set(output.get("evidence_ids") or [])
        assessments = output.get("assessments") or []
        supported = any(
            isinstance(x, Mapping)
            and x.get("stance") == "supports"
            and x.get("evidence_id") in cited
            for x in assessments
        )
        count += not supported
    return count


def _pairs[K: Hashable](groups: Mapping[K, Hashable]) -> set[frozenset[K]]:
    by_group: dict[Hashable, list[K]] = {}
    for item, group in groups.items():
        by_group.setdefault(group, []).append(item)
    return {frozenset(pair) for members in by_group.values() for pair in combinations(members, 2)}


def pairwise_precision_recall[K: Hashable](
    gold: Mapping[K, Hashable], pred: Mapping[K, Hashable]
) -> PRF:
    """配對式合併指標：把「同組的每一對」當成一個預測，比較 gold 與 pred 的配對集合。

    用於同事件合併（S04）：pred 漏掉的項目，gold 中涉及它的配對都算漏掉。
    """
    gold_pairs, pred_pairs = _pairs(gold), _pairs(pred)
    hit = len(gold_pairs & pred_pairs)
    precision = hit / len(pred_pairs) if pred_pairs else None
    recall = hit / len(gold_pairs) if gold_pairs else None
    return PRF(precision, recall, _f1(precision, recall), len(gold_pairs))


# --- golden case 判定 ----------------------------------------------------------------------

_MISSING = object()


def _get(output: Mapping[str, Any], path: str) -> Any:
    node: Any = output
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def rule_triggered(rule: Mapping[str, Any], output: Mapping[str, Any]) -> bool:
    """`must_not` 的一條規則是否成立（成立即案例失敗）。"""
    op, value = rule.get("op"), rule.get("value")
    if op == "matches":
        return re.search(str(value), json.dumps(output, ensure_ascii=False)) is not None
    if op not in ("eq", "in", "contains"):
        raise ValueError(f"不支援的 must_not op：{op!r}")
    actual = _get(output, str(rule.get("path", "")))
    if actual is _MISSING:
        return False
    if op == "eq":
        return bool(actual == value)
    if op == "in":
        return isinstance(value, list) and actual in value
    return isinstance(actual, list) and value in actual


@dataclass(frozen=True)
class CaseOutcome:
    """一筆 golden case 的判定；`failures` 是失敗原因的代碼（不含輸出內容）。"""

    passed: bool
    failures: tuple[str, ...]


def case_outcome(case: Mapping[str, Any], output: Mapping[str, Any] | None) -> CaseOutcome:
    """比對 expected（部分欄位相等）與 must_not；沒有通過驗證的輸出直接判定失敗。"""
    if output is None:
        return CaseOutcome(False, ("no_valid_output",))
    failures: list[str] = [
        f"expected:{key}"
        for key, want in (case.get("expected") or {}).items()
        if _get(output, key) != want
    ]
    for i, rule in enumerate(case.get("must_not") or []):
        if rule_triggered(rule, output):
            failures.append(f"must_not:{rule.get('note') or i}")
    return CaseOutcome(not failures, tuple(failures))
