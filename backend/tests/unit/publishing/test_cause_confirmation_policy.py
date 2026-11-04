"""P7-T07：人工確認原因後，公開規則把措施依據視為 confirmed（ADR-0017）。

- 措施依據的原因經人工確認（`ReportInput.confirmed_cause_ids`）時可以公開；原因本身仍不公開；
- 由這類措施建立的改善措施（`ActionInput.cause_confirmed`）也可以公開；
- 疑似指控、PII 遮蔽、查證門檻不變；沒有人工確認時與 P5-T10 完全相同。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.alerts.pii import MASK
from app.domain.states import VerificationStatus
from app.publishing.policy import (
    ConfirmedBy,
    PublicLevel,
    PublishPolicy,
    load_policy,
)
from app.publishing.policy import evaluate as evaluate_policy
from tests.unit.publishing.test_publish_policy import (
    POLICY_PATH,
    dumped,
    make_action,
    make_input,
    make_report,
)


@pytest.fixture(scope="module")
def policy() -> PublishPolicy:
    return load_policy(POLICY_PATH)


def hypothesized_measure(action: str = "更換屋頂防水層", cause_id: str = "c1") -> dict[str, Any]:
    base = dict(make_report().measures[0])
    return {
        **base,
        "action": action,
        "audience": "school",
        "cause_id": cause_id,
        "cause_basis": "hypothesized",
        "responsible_unit": "總務處",
    }


def measures_of(decision: Any) -> list[str]:
    assert decision.content is not None
    return [m["action"] for m in decision.content["report"]["measures"]]


def test_confirmed_cause_releases_its_measures(policy: PublishPolicy) -> None:
    report = make_report(measures=[hypothesized_measure()], confirmed_cause_ids=frozenset({"c1"}))
    decision = evaluate_policy(make_input(report=report), policy)
    assert decision.level is PublicLevel.CONTENT
    assert measures_of(decision) == ["更換屋頂防水層"]


def test_released_measure_looks_like_confirmed_measure(policy: PublishPolicy) -> None:
    """公開頁不標示人工確認（使用者決定）：欄位與依據校方確認的措施相同。"""
    manual = make_report(measures=[hypothesized_measure()], confirmed_cause_ids=frozenset({"c1"}))
    official = make_report(measures=[{**hypothesized_measure(), "cause_basis": "confirmed"}])
    a = evaluate_policy(make_input(report=manual), policy)
    b = evaluate_policy(make_input(report=official), policy)
    assert a.content == b.content
    assert "人工" not in dumped(a)


def test_other_cause_confirmation_does_not_release(policy: PublishPolicy) -> None:
    report = make_report(measures=[hypothesized_measure()], confirmed_cause_ids=frozenset({"c2"}))
    decision = evaluate_policy(make_input(report=report), policy)
    assert measures_of(decision) == []
    assert "更換屋頂防水層" not in dumped(decision)


def test_without_confirmation_behaves_as_before(policy: PublishPolicy) -> None:
    report = make_report(measures=[hypothesized_measure()])
    assert report.confirmed_cause_ids == frozenset()
    decision = evaluate_policy(make_input(report=report), policy)
    assert measures_of(decision) == []


def test_cause_text_stays_private_after_confirmation(policy: PublishPolicy) -> None:
    report = make_report(measures=[hypothesized_measure()], confirmed_cause_ids=frozenset({"c1"}))
    body = dumped(evaluate_policy(make_input(report=report), policy))
    assert "屋頂防水層老化" not in body
    assert "請確認維修紀錄" not in body
    assert "hypothesized" not in body


def test_allegation_in_released_measure_blocks_content(policy: PublishPolicy) -> None:
    accusation = "調查警衛偷竊座位上的筆電"
    report = make_report(
        measures=[hypothesized_measure(accusation)], confirmed_cause_ids=frozenset({"c1"})
    )
    decision = evaluate_policy(make_input(report=report), policy)
    assert decision.level is not PublicLevel.CONTENT
    assert decision.needs_review
    assert "possible_allegation" in decision.reasons
    assert accusation not in dumped(decision)


def test_released_measure_is_pii_masked(policy: PublishPolicy) -> None:
    report = make_report(
        measures=[hypothesized_measure("請聯絡 0912-345-678 更換防水層")],
        confirmed_cause_ids=frozenset({"c1"}),
    )
    decision = evaluate_policy(make_input(report=report), policy)
    body = dumped(decision)
    assert "0912-345-678" not in body
    assert MASK in body


def test_confirmation_does_not_bypass_verification(policy: PublishPolicy) -> None:
    report = make_report(measures=[hypothesized_measure()], confirmed_cause_ids=frozenset({"c1"}))
    for item in (
        make_input(report=report, verification=VerificationStatus.UNVERIFIED, confirmed_by=None),
        make_input(report=report, confirmed_by=ConfirmedBy.FIRST_HAND),
    ):
        decision = evaluate_policy(item, policy)
        assert decision.level is not PublicLevel.CONTENT
        assert "更換屋頂防水層" not in dumped(decision)


def test_progress_from_confirmed_cause_is_public(policy: PublishPolicy) -> None:
    released = make_action(
        title="確認是否為施工挖斷水管", cause_basis="hypothesized", cause_confirmed=True
    )
    held = make_action(title="檢查管線老化", cause_basis="hypothesized")
    decision = evaluate_policy(make_input(actions=(released, held)), policy)
    assert decision.content is not None
    assert [a["title"] for a in decision.content["progress"]] == ["確認是否為施工挖斷水管"]
    assert "檢查管線老化" not in dumped(decision)


def test_allegation_in_released_action_blocks_content(policy: PublishPolicy) -> None:
    accusation = "調查警衛偷竊座位上的筆電"
    action = make_action(title=accusation, cause_basis="hypothesized", cause_confirmed=True)
    decision = evaluate_policy(make_input(actions=(action,)), policy)
    assert decision.level is not PublicLevel.CONTENT
    assert decision.needs_review
    assert accusation not in dumped(decision)
