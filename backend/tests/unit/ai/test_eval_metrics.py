"""P2-T05：評測指標（Macro-F1、Precision／Recall、配對式合併指標）與案例判定。"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.eval.metrics import (
    accuracy,
    case_outcome,
    macro_f1,
    pairwise_precision_recall,
    precision_recall_f1,
    rule_triggered,
)

# --- Macro-F1 ------------------------------------------------------------------------------


def test_macro_f1_perfect() -> None:
    assert macro_f1(["a", "b", "c"], ["a", "b", "c"]) == 1.0


def test_macro_f1_averages_per_class() -> None:
    gold = ["a", "a", "b", "b"]
    pred = ["a", "b", "b", "b"]
    # a：P 1、R 0.5、F1 2/3；b：P 2/3、R 1、F1 0.8
    assert macro_f1(gold, pred) == pytest.approx((2 / 3 + 0.8) / 2)


def test_macro_f1_counts_labels_only_in_predictions() -> None:
    # 預測出 gold 沒有的類別：該類別 F1 為 0，拉低平均
    assert macro_f1(["a", "a"], ["a", "x"]) == pytest.approx((2 / 3 + 0.0) / 2)


def test_macro_f1_treats_none_as_a_label() -> None:
    assert macro_f1([None, "a"], [None, "a"]) == 1.0
    worse = macro_f1([None, "a"], ["a", "a"])
    assert worse is not None and worse < 1.0


def test_macro_f1_requires_same_length() -> None:
    with pytest.raises(ValueError):
        macro_f1(["a"], ["a", "b"])


def test_macro_f1_empty_is_undefined() -> None:
    assert macro_f1([], []) is None


# --- Precision／Recall ----------------------------------------------------------------------


def test_precision_recall_f1() -> None:
    gold = [True, True, False, False, True]
    pred = [True, False, True, False, True]
    prf = precision_recall_f1(gold, pred, positive=True)
    assert prf.precision == pytest.approx(2 / 3)
    assert prf.recall == pytest.approx(2 / 3)
    assert prf.f1 == pytest.approx(2 / 3)
    assert prf.support == 3


def test_recall_undefined_without_positive_gold() -> None:
    prf = precision_recall_f1([False, False], [False, True], positive=True)
    assert prf.recall is None
    assert prf.precision == 0.0


def test_precision_undefined_without_positive_predictions() -> None:
    prf = precision_recall_f1([True, False], [False, False], positive=True)
    assert prf.precision is None
    assert prf.recall == 0.0
    assert prf.f1 == 0.0


def test_accuracy() -> None:
    assert accuracy(["a", "b", "c", "d"], ["a", "b", "x", "d"]) == 0.75
    assert accuracy([], []) is None


# --- 配對式合併指標 --------------------------------------------------------------------------


def test_pairwise_perfect() -> None:
    gold = {"1": "g1", "2": "g1", "3": "g2"}
    pred = {"1": "p", "2": "p", "3": "q"}  # 代號不同，分組相同
    prf = pairwise_precision_recall(gold, pred)
    assert prf.precision == 1.0 and prf.recall == 1.0


def test_pairwise_over_merge_lowers_precision() -> None:
    gold = {"1": "a", "2": "a", "3": "b", "4": "b"}
    pred = {"1": "x", "2": "x", "3": "x", "4": "x"}  # 全部併成一組
    prf = pairwise_precision_recall(gold, pred)
    # 預測 6 對，其中正確 2 對；gold 2 對全被找到
    assert prf.precision == pytest.approx(2 / 6)
    assert prf.recall == 1.0


def test_pairwise_under_merge_lowers_recall() -> None:
    gold = {"1": "a", "2": "a", "3": "a"}
    pred = {"1": "x", "2": "x", "3": "y"}
    prf = pairwise_precision_recall(gold, pred)
    assert prf.precision == 1.0
    assert prf.recall == pytest.approx(1 / 3)


def test_pairwise_ignores_items_missing_from_prediction() -> None:
    gold = {"1": "a", "2": "a", "3": "a"}
    pred = {"1": "x", "2": "x"}  # 第 3 筆沒有預測：gold 中涉及它的配對算漏掉
    prf = pairwise_precision_recall(gold, pred)
    assert prf.precision == 1.0
    assert prf.recall == pytest.approx(1 / 3)


def test_pairwise_no_pairs_is_undefined() -> None:
    prf = pairwise_precision_recall({"1": "a", "2": "b"}, {"1": "x", "2": "y"})
    assert prf.precision is None and prf.recall is None


# --- 案例判定 -------------------------------------------------------------------------------

OUT: dict[str, Any] = {
    "status": "ok",
    "announcement_type": "maintenance",
    "evidence_ids": ["e1"],
    "reason": "停水公告。",
    "nested": {"level": 2},
}


@pytest.mark.parametrize(
    ("rule", "hit"),
    [
        ({"path": "announcement_type", "op": "eq", "value": "maintenance"}, True),
        ({"path": "announcement_type", "op": "eq", "value": "event"}, False),
        ({"path": "announcement_type", "op": "in", "value": ["event", "maintenance"]}, True),
        ({"path": "evidence_ids", "op": "contains", "value": "e1"}, True),
        ({"path": "evidence_ids", "op": "contains", "value": "e9"}, False),
        ({"path": "reason", "op": "contains", "value": "停水"}, False),  # contains 只看陣列
        ({"op": "matches", "value": r"09\d{2}"}, False),
        ({"op": "matches", "value": "停水"}, True),
        ({"path": "nested.level", "op": "eq", "value": 2}, True),
        ({"path": "missing.field", "op": "eq", "value": None}, False),
    ],
)
def test_rule_triggered(rule: dict[str, Any], hit: bool) -> None:
    assert rule_triggered(rule, OUT) is hit


def test_rule_unknown_op() -> None:
    with pytest.raises(ValueError):
        rule_triggered({"path": "x", "op": "regex", "value": "y"}, OUT)


def test_case_passes() -> None:
    case = {
        "expected": {"status": "ok", "announcement_type": "maintenance"},
        "must_not": [{"path": "announcement_type", "op": "eq", "value": "event"}],
    }
    outcome = case_outcome(case, OUT)
    assert outcome.passed is True
    assert outcome.failures == ()


def test_case_expected_mismatch() -> None:
    case = {
        "expected": {"announcement_type": "event"},
        "must_not": [{"op": "matches", "value": "x^"}],
    }
    outcome = case_outcome(case, OUT)
    assert outcome.passed is False
    assert outcome.failures == ("expected:announcement_type",)


def test_case_must_not_uses_note() -> None:
    rule = {"path": "evidence_ids", "op": "contains", "value": "e1", "note": "不得引用 e1"}
    outcome = case_outcome({"expected": {}, "must_not": [rule]}, OUT)
    assert outcome.passed is False
    assert outcome.failures == ("must_not:不得引用 e1",)


def test_case_without_output_fails() -> None:
    outcome = case_outcome({"expected": {"status": "abstain"}, "must_not": []}, None)
    assert outcome.passed is False
    assert outcome.failures == ("no_valid_output",)
