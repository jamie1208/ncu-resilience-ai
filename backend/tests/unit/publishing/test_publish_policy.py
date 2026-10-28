"""P5-T10：公開規則引擎（app/publishing/policy.py、config/publish-policy.yaml）。

ADR-0015 的硬性邊界寫在程式裡，設定檔無法關閉；每條邊界各有正反案例：
1. 官方來源證實或使用者查證為 true 的事件才公開事件內容；其他只公開 P3-T07 的中性安全提醒；
2. 疑似指控（P3-T07 的指控規則命中）永不自動公開，只標記給管理端判斷；
3. S07 的推測原因（hypothesized）不公開，公開版小報告只放影響與建議；
4. Facebook 來源只公開 AI 摘要，不放原文與連結；
5. 公開內容一律經 PII 遮蔽。
附錄 A 的 A02、A05 不會公開事件內容。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.ai.runner.validate import find_pii
from app.alerts.pii import MASK
from app.domain.states import VerificationStatus
from app.publishing.policy import (
    ActionInput,
    ConfirmedBy,
    PublicationInput,
    PublicLevel,
    PublicSource,
    PublishPolicy,
    ReportInput,
    evaluate,
    load_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
POLICY_PATH = REPO_ROOT / "config" / "publish-policy.yaml"

OFFICIAL = PublicSource(
    source_code="ncu-lib",
    source_name="中央大學圖書館",
    url="https://lib.ncu.edu.tw/news/1",
)
FACEBOOK = PublicSource(
    source_code="facebook",
    source_name="Facebook NCU 社團",
    url="https://www.facebook.com/groups/NCUgroup/posts/1",
)
FB_ORIGINAL = "我今天在總圖三樓看到天花板漏水，整排座位都濕了"


@pytest.fixture(scope="module")
def policy() -> PublishPolicy:
    return load_policy(POLICY_PATH)


def make_report(**overrides: Any) -> ReportInput:
    values: dict[str, Any] = {
        "impacts": [
            {
                "dimension": "safety",
                "score": 2,
                "uncertainty": "low",
                "reason": "漏水區域地面濕滑",
                "evidence_ids": ["a1"],
            }
        ],
        "causes": [
            {
                "cause_id": "c1",
                "description": "屋頂防水層老化",
                "basis": "hypothesized",
                "label": "推測，尚未查證",
                "quotes": [],
                "to_verify": "請確認維修紀錄",
                "evidence_ids": ["a1"],
            }
        ],
        "measures": [
            {
                "action": "避開三樓漏水區域",
                "audience": "students",
                "horizon": "short",
                "priority": "high",
                "kind": "respond",
                "cause_id": None,
                "cause_basis": None,
                "responsible_unit": None,
                "evidence_ids": ["a1"],
            }
        ],
    }
    values.update(overrides)
    return ReportInput(**values)


def make_input(**overrides: Any) -> PublicationInput:
    values: dict[str, Any] = {
        "event_id": uuid.UUID(int=1),
        "event_type": "facility",
        "title": "總圖書館三樓漏水",
        "summary": "總圖書館三樓天花板漏水，部分座位暫停使用。",
        "risk_level": "medium",
        "verification": VerificationStatus.VERIFIED,
        "confirmed_by": ConfirmedBy.OFFICIAL,
        "location_name": "總圖書館",
        "occurrence_date": date(2026, 10, 27),
        "sources": (OFFICIAL,),
        "report": make_report(),
    }
    values.update(overrides)
    return PublicationInput(**values)


def dumped(decision: Any) -> str:
    return json.dumps(decision.content, ensure_ascii=False)


# --- 設定檔 ---


def test_policy_file_loads(policy: PublishPolicy) -> None:
    assert policy.policy_version
    assert set(policy.safety_notice_levels) <= {"low", "medium", "high", "critical"}


@pytest.mark.parametrize(
    "key",
    [
        "publish_unverified_content",
        "publish_allegations",
        "publish_hypothesized_causes",
        "publish_facebook_original",
        "mask_pii",
    ],
)
def test_hard_boundaries_cannot_be_configured(key: str) -> None:
    """設定檔不接受任何關閉硬性邊界的欄位。"""
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw[key] = key != "mask_pii"
    with pytest.raises(ValidationError):
        PublishPolicy.model_validate(raw)


def test_unknown_risk_level_in_config_is_rejected() -> None:
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw["safety_notice_levels"] = ["urgent"]
    with pytest.raises(ValidationError):
        PublishPolicy.model_validate(raw)


# --- 邊界 1：只有官方證實或使用者查證為 true 才公開事件內容 ---


@pytest.mark.parametrize("confirmed_by", [ConfirmedBy.OFFICIAL, ConfirmedBy.USER])
def test_confirmed_event_publishes_content(
    policy: PublishPolicy, confirmed_by: ConfirmedBy
) -> None:
    decision = evaluate(make_input(confirmed_by=confirmed_by), policy)
    assert decision.level is PublicLevel.CONTENT
    assert decision.content is not None
    assert decision.content["title"] == "總圖書館三樓漏水"
    assert decision.content["summary"] == "總圖書館三樓天花板漏水，部分座位暫停使用。"
    assert decision.content["location"] == "總圖書館"
    assert decision.content["occurrence_date"] == "2026-10-27"
    assert decision.policy_version == policy.policy_version


def test_confirmed_event_without_risk_assessment_still_publishes_content(
    policy: PublishPolicy,
) -> None:
    """官方公告（例如閉館）常沒有風險評估，仍公開事件內容。"""
    decision = evaluate(make_input(risk_level=None, report=None), policy)
    assert decision.level is PublicLevel.CONTENT
    assert decision.content is not None
    assert decision.content["report"] is None


def test_first_hand_verified_without_official_or_user_is_only_safety_notice(
    policy: PublishPolicy,
) -> None:
    """兩份第一手來源支持（P2-T10 規則）不是官方證實，也不是使用者查證。"""
    decision = evaluate(make_input(confirmed_by=ConfirmedBy.FIRST_HAND, risk_level="high"), policy)
    assert decision.level is PublicLevel.SAFETY_NOTICE
    assert "not_confirmed" in decision.reasons
    assert "總圖書館三樓漏水" not in dumped(decision)


@pytest.mark.parametrize(
    "verification", [VerificationStatus.UNVERIFIED, VerificationStatus.DISPUTED]
)
def test_unverified_or_disputed_is_neutral_safety_notice(
    policy: PublishPolicy, verification: VerificationStatus
) -> None:
    decision = evaluate(
        make_input(verification=verification, confirmed_by=None, risk_level="high"), policy
    )
    assert decision.level is PublicLevel.SAFETY_NOTICE
    assert decision.content is not None
    text = decision.content["notice"]
    assert "一般安全提醒" in text
    assert "目前無法確認是否屬實" in text
    assert "總圖書館" in text  # 地點
    body = dumped(decision)
    assert "總圖書館三樓漏水" not in body  # 標題
    assert "天花板漏水" not in body  # 摘要
    assert "避開三樓漏水區域" not in body  # 小報告
    assert "lib.ncu.edu.tw" not in body  # 來源
    assert set(decision.content) == {"kind", "event_type", "label", "notice"}


def test_confirmed_by_ignored_unless_verification_is_verified(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(
            verification=VerificationStatus.DISPUTED,
            confirmed_by=ConfirmedBy.USER,
            risk_level="high",
        ),
        policy,
    )
    assert decision.level is PublicLevel.SAFETY_NOTICE


def test_unverified_below_notice_threshold_is_not_public(policy: PublishPolicy) -> None:
    low = next(
        lv for lv in ("low", "medium", "high", "critical") if lv not in policy.safety_notice_levels
    )
    decision = evaluate(
        make_input(verification=VerificationStatus.UNVERIFIED, confirmed_by=None, risk_level=low),
        policy,
    )
    assert decision.level is PublicLevel.NONE
    assert decision.content is None
    assert "below_threshold" in decision.reasons


def test_unverified_without_risk_assessment_is_not_public(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(verification=VerificationStatus.UNVERIFIED, confirmed_by=None, risk_level=None),
        policy,
    )
    assert decision.level is PublicLevel.NONE
    assert "no_risk_assessment" in decision.reasons


def test_false_event_is_never_public(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(verification=VerificationStatus.FALSE, confirmed_by=ConfirmedBy.OFFICIAL),
        policy,
    )
    assert decision.level is PublicLevel.NONE
    assert decision.content is None
    assert "verification_false" in decision.reasons


def test_excluded_event_type_is_not_public(policy: PublishPolicy) -> None:
    decision = evaluate(make_input(event_type="none"), policy)
    assert decision.level is PublicLevel.NONE
    assert "excluded_event_type" in decision.reasons


def test_unknown_event_type_raises(policy: PublishPolicy) -> None:
    with pytest.raises(ValueError):
        evaluate(make_input(event_type="alien"), policy)


# --- 邊界 2：疑似指控永不自動公開 ---


def test_allegation_in_title_blocks_content_even_if_officially_confirmed(
    policy: PublishPolicy,
) -> None:
    decision = evaluate(
        make_input(event_type="security", title="警衛在停車場偷走機車", risk_level="high"),
        policy,
    )
    assert decision.level is PublicLevel.SAFETY_NOTICE
    assert decision.needs_review
    assert "possible_allegation" in decision.reasons
    body = dumped(decision)
    assert "警衛" not in body
    assert "偷" not in body
    assert "總圖書館" not in body  # 疑似指控時不寫地點


@pytest.mark.parametrize(
    "field",
    ["summary", "impact_reason", "measure_action"],
)
def test_allegation_anywhere_in_public_text_blocks_content(
    policy: PublishPolicy, field: str
) -> None:
    accusation = "店員詐騙同學"
    report = make_report()
    if field == "impact_reason":
        report = make_report(impacts=[{**report.impacts[0], "reason": accusation}])
    elif field == "measure_action":
        report = make_report(measures=[{**report.measures[0], "action": accusation}])
    item = make_input(report=report)
    if field == "summary":
        item = replace(item, summary=accusation)
    decision = evaluate(item, policy)
    assert decision.level is not PublicLevel.CONTENT
    assert decision.needs_review
    assert accusation not in dumped(decision)


def test_no_allegation_does_not_need_review(policy: PublishPolicy) -> None:
    decision = evaluate(make_input(), policy)
    assert not decision.needs_review
    assert "possible_allegation" not in decision.reasons


def test_a02_single_post_theft_allegation_publishes_no_event_content(
    policy: PublishPolicy,
) -> None:
    """A02 社群單篇失竊指控：未核實；只可有中性防竊提醒，不公開個人指控。"""
    decision = evaluate(
        make_input(
            event_type="security",
            title="松苑餐廳老闆偷了我放在門口的腳踏車",
            summary="有人在社群指稱松苑餐廳老闆偷走腳踏車。",
            risk_level="high",
            verification=VerificationStatus.UNVERIFIED,
            confirmed_by=None,
            location_name="松苑餐廳",
            sources=(FACEBOOK,),
            report=None,
        ),
        policy,
    )
    assert decision.level is PublicLevel.SAFETY_NOTICE
    assert decision.needs_review
    body = dumped(decision)
    for leaked in ("松苑餐廳", "老闆", "偷", "腳踏車", "facebook"):
        assert leaked not in body
    assert "治安" in body


def test_a05_malicious_accusation_publishes_no_event_content(policy: PublishPolicy) -> None:
    """A05 匿名惡意誣指個人：阻擋對外指控；管理端標記異常。"""
    decision = evaluate(
        make_input(
            event_type="security",
            title="陳老師在研究室性騷擾學生 0912-345-678",
            summary="匿名投稿指稱陳老師性騷擾學生。",
            risk_level="critical",
            verification=VerificationStatus.UNVERIFIED,
            confirmed_by=None,
            location_name="科學一館",
            sources=(),
            report=None,
        ),
        policy,
    )
    assert decision.level is not PublicLevel.CONTENT
    assert decision.needs_review
    body = dumped(decision)
    for leaked in ("陳老師", "性騷擾", "0912", "科學一館"):
        assert leaked not in body


# --- 邊界 3：S07 推測原因不公開 ---


def test_public_report_contains_impacts_and_measures_but_no_causes(
    policy: PublishPolicy,
) -> None:
    decision = evaluate(make_input(), policy)
    assert decision.content is not None
    report = decision.content["report"]
    assert set(report) == {"impacts", "measures"}
    assert report["impacts"][0]["reason"] == "漏水區域地面濕滑"
    assert report["measures"][0]["action"] == "避開三樓漏水區域"
    body = dumped(decision)
    assert "屋頂防水層老化" not in body
    assert "hypothesized" not in body
    assert "推測" not in body


def test_measure_based_on_hypothesized_cause_is_not_public(policy: PublishPolicy) -> None:
    base = make_report().measures[0]
    report = make_report(
        measures=[
            {**base, "action": "更換屋頂防水層", "cause_id": "c1", "cause_basis": "hypothesized"},
            {**base, "action": "張貼漏水警示", "cause_id": "c2", "cause_basis": "confirmed"},
            base,
        ]
    )
    decision = evaluate(make_input(report=report), policy)
    assert decision.content is not None
    actions = [m["action"] for m in decision.content["report"]["measures"]]
    assert actions == ["張貼漏水警示", "避開三樓漏水區域"]
    assert "更換屋頂防水層" not in dumped(decision)


def test_public_measures_keep_responsible_unit_and_drop_internal_fields(
    policy: PublishPolicy,
) -> None:
    base = make_report().measures[0]
    report = make_report(
        measures=[{**base, "audience": "school", "responsible_unit": "總務處營繕組"}]
    )
    decision = evaluate(make_input(report=report), policy)
    assert decision.content is not None
    measure = decision.content["report"]["measures"][0]
    assert measure["responsible_unit"] == "總務處營繕組"
    assert "evidence_ids" not in measure
    assert "cause_id" not in measure
    assert "cause_basis" not in measure


# --- 邊界 4：Facebook 只公開 AI 摘要，不放原文與連結 ---


def test_facebook_source_is_not_listed_and_link_is_not_public(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(confirmed_by=ConfirmedBy.USER, sources=(OFFICIAL, FACEBOOK)), policy
    )
    assert decision.level is PublicLevel.CONTENT
    assert decision.content is not None
    assert decision.content["sources"] == [
        {"name": "中央大學圖書館", "url": "https://lib.ncu.edu.tw/news/1"}
    ]
    body = dumped(decision)
    assert "facebook" not in body.lower()


def test_facebook_only_event_publishes_ai_summary_without_source(
    policy: PublishPolicy,
) -> None:
    decision = evaluate(make_input(confirmed_by=ConfirmedBy.USER, sources=(FACEBOOK,)), policy)
    assert decision.content is not None
    assert decision.content["summary"] == "總圖書館三樓天花板漏水，部分座位暫停使用。"
    assert decision.content["sources"] == []
    assert FB_ORIGINAL not in dumped(decision)


def test_only_official_sources_are_listed(policy: PublishPolicy) -> None:
    """匿名投稿（P5-T13）與其他非官方來源都不列出，原文與連結永不公開。"""
    anonymous = PublicSource(source_code="anonymous-report", source_name="匿名回報", url=None)
    cwa = PublicSource(source_code="cwa-alerts", source_name="中央氣象署", url=None)
    decision = evaluate(make_input(sources=(anonymous, cwa, OFFICIAL)), policy)
    assert decision.content is not None
    assert [s["name"] for s in decision.content["sources"]] == ["中央氣象署", "中央大學圖書館"]


# --- 邊界 5：公開內容一律經 PII 遮蔽 ---


def test_content_is_pii_masked(policy: PublishPolicy) -> None:
    pii = "請聯絡 a1234567@cc.ncu.edu.tw 或 0912-345-678"
    base = make_report()
    report = make_report(
        impacts=[{**base.impacts[0], "reason": pii}],
        measures=[{**base.measures[0], "action": pii, "responsible_unit": pii}],
    )
    decision = evaluate(
        make_input(title=f"總圖書館漏水 {pii}", summary=pii, location_name=pii, report=report),
        policy,
    )
    assert decision.level is PublicLevel.CONTENT
    body = dumped(decision)
    assert MASK in body
    assert find_pii(body) == []


def test_safety_notice_is_pii_masked(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(
            verification=VerificationStatus.UNVERIFIED,
            confirmed_by=None,
            risk_level="high",
            location_name="宿舍 0912-345-678",
        ),
        policy,
    )
    assert decision.level is PublicLevel.SAFETY_NOTICE
    assert find_pii(dumped(decision)) == []


def test_clean_content_is_unchanged_by_masking(policy: PublishPolicy) -> None:
    decision = evaluate(make_input(), policy)
    assert MASK not in dumped(decision)


def test_evaluate_is_deterministic(policy: PublishPolicy) -> None:
    item = make_input()
    assert evaluate(item, policy) == evaluate(item, policy)


# --- 改善進度（P5-T11）：同樣經過硬性邊界 ---

UPDATED = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)


def make_action(**overrides: Any) -> ActionInput:
    values: dict[str, Any] = {
        "title": "更換三樓天花板防水層",
        "state": "handling",
        "due_date": date(2026, 11, 15),
        "updated_at": UPDATED,
        "cause_basis": "confirmed",
    }
    values.update(overrides)
    return ActionInput(**values)


def test_content_lists_progress_with_public_fields_only(policy: PublishPolicy) -> None:
    decision = evaluate(make_input(actions=(make_action(),)), policy)
    assert decision.level is PublicLevel.CONTENT
    assert decision.content is not None
    assert decision.content["progress"] == [
        {
            "title": "更換三樓天花板防水層",
            "state": "handling",
            "due_date": "2026-11-15",
            "updated_at": "2026-10-28T02:00:00+00:00",
        }
    ]


def test_progress_based_on_hypothesized_cause_is_not_public(policy: PublishPolicy) -> None:
    hypothesized = make_action(title="確認是否為施工挖斷水管", cause_basis="hypothesized")
    decision = evaluate(make_input(actions=(make_action(), hypothesized)), policy)
    assert decision.content is not None
    assert [a["title"] for a in decision.content["progress"]] == ["更換三樓天花板防水層"]
    assert "施工" not in dumped(decision)


def test_allegation_in_action_title_blocks_content(policy: PublishPolicy) -> None:
    accusation = "調查警衛偷竊座位上的筆電"
    decision = evaluate(make_input(actions=(make_action(title=accusation),)), policy)
    assert decision.level is not PublicLevel.CONTENT
    assert decision.needs_review
    assert accusation not in dumped(decision)


def test_progress_is_pii_masked(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(actions=(make_action(title="聯絡承包商 0912-345-678 排定維修"),)), policy
    )
    assert decision.level is PublicLevel.CONTENT
    assert find_pii(dumped(decision)) == []


def test_safety_notice_has_no_progress(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(
            verification=VerificationStatus.UNVERIFIED,
            confirmed_by=None,
            risk_level="high",
            actions=(make_action(),),
        ),
        policy,
    )
    assert decision.level is PublicLevel.SAFETY_NOTICE
    assert "更換" not in dumped(decision)
