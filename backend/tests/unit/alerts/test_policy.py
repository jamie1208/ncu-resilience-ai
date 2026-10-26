"""P3-T07：個人通知規則（app/alerts/policy.py、app/alerts/pii.py、config/notification-policy.yaml）。

使用者 2026-10-21 決定：
1. 只有風險分級為高或極高的事件才通知使用者本人；
2. 依查證狀態分三種呈現：verified 或有官方來源 → 事實模板（事實＋防護建議）；
   unverified、disputed → 中性「一般安全提醒」，只寫類別、地點、日期與防護建議，不帶事件敘述；
   false → 不通知（已發出的由 P3-T08 撤回）；
3. 指控：未核實內容只走固定模板，不可能寫成事實；另以設定檔關鍵字偵測疑似指控，
   標記 possible_allegation 給管理端，仍送中性提醒；
4. 全部事件類別都適用，只排除 none（非事件）。
通知內容一律先經 PII 遮蔽。附錄 A 的 A02、A05 不得把指控寫成事實。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.ai.runner.validate import find_pii
from app.alerts.pii import mask_pii
from app.alerts.policy import (
    NotificationInput,
    NotificationPolicy,
    Template,
    evaluate,
    load_policy,
)
from app.domain.states import VerificationStatus

REPO_ROOT = Path(__file__).resolve().parents[4]
POLICY_PATH = REPO_ROOT / "config" / "notification-policy.yaml"


@pytest.fixture(scope="module")
def policy() -> NotificationPolicy:
    return load_policy()


def make_input(**overrides: object) -> NotificationInput:
    base = NotificationInput(
        event_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        event_type="facility",
        title="工程一館停電",
        risk_level="high",
        verification=VerificationStatus.VERIFIED,
        location_name="工程一館",
        occurrence_date=date(2026, 10, 21),
        has_official_source=False,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --- 設定檔 ---


def test_default_policy_file_loads(policy: NotificationPolicy) -> None:
    assert POLICY_PATH.is_file()
    assert policy.policy_version
    assert policy.notify_levels == ["high", "critical"]
    assert policy.excluded_event_types == ["none"]


def test_policy_rejects_unknown_keys(tmp_path: Path) -> None:
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw["auto_publish"] = True
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_policy(path)


def test_policy_requires_label_and_advice_for_every_event_type(tmp_path: Path) -> None:
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    del raw["event_types"]["fire"]
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_policy(path)


def test_env_example_has_no_auto_publish_switch() -> None:
    assert "AUTO_PUBLISH_ENABLED" not in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


# --- 門檻：只通知高與極高 ---


@pytest.mark.parametrize("level", ["high", "critical"])
def test_high_and_critical_notify(policy: NotificationPolicy, level: str) -> None:
    decision = evaluate(make_input(risk_level=level), policy)
    assert decision.notify
    assert decision.template is Template.FACTUAL
    assert decision.body


@pytest.mark.parametrize("level", ["low", "medium"])
def test_low_and_medium_never_notify(policy: NotificationPolicy, level: str) -> None:
    for verification in VerificationStatus:
        decision = evaluate(
            make_input(risk_level=level, verification=verification, has_official_source=True),
            policy,
        )
        assert not decision.notify
        assert decision.template is None
        assert decision.body is None
        assert "below_threshold" in decision.reasons


def test_missing_risk_level_does_not_notify(policy: NotificationPolicy) -> None:
    decision = evaluate(make_input(risk_level=None), policy)
    assert not decision.notify
    assert "no_risk_assessment" in decision.reasons


# --- 類別 ---


@pytest.mark.parametrize(
    "event_type",
    ["facility", "weather", "security", "traffic", "health", "fire", "academic", "infosec"],
)
def test_every_event_type_can_notify(policy: NotificationPolicy, event_type: str) -> None:
    assert evaluate(make_input(event_type=event_type), policy).notify


def test_non_event_never_notifies(policy: NotificationPolicy) -> None:
    decision = evaluate(make_input(event_type="none", risk_level="critical"), policy)
    assert not decision.notify
    assert "excluded_event_type" in decision.reasons


def test_unknown_event_type_is_rejected(policy: NotificationPolicy) -> None:
    with pytest.raises(ValueError):
        evaluate(make_input(event_type="gossip"), policy)


# --- 查證狀態 ---


def test_verified_uses_factual_template(policy: NotificationPolicy) -> None:
    decision = evaluate(make_input(), policy)
    assert decision.template is Template.FACTUAL
    assert decision.body is not None
    assert "工程一館停電" in decision.body
    assert "工程一館" in decision.body
    assert "2026-10-21" in decision.body
    assert policy.event_types["facility"].advice in decision.body


@pytest.mark.parametrize(
    "verification", [VerificationStatus.UNVERIFIED, VerificationStatus.DISPUTED]
)
def test_unverified_and_disputed_use_general_template_without_narrative(
    policy: NotificationPolicy, verification: VerificationStatus
) -> None:
    decision = evaluate(make_input(verification=verification, title="宿舍水管爆裂淹水"), policy)
    assert decision.notify
    assert decision.template is Template.GENERAL_SAFETY
    assert decision.body is not None
    assert "宿舍水管爆裂淹水" not in decision.body
    assert "未經查證" in decision.body
    assert policy.event_types["facility"].label in decision.body
    assert policy.event_types["facility"].advice in decision.body


def test_official_source_uses_factual_template_even_if_unverified(
    policy: NotificationPolicy,
) -> None:
    decision = evaluate(
        make_input(verification=VerificationStatus.UNVERIFIED, has_official_source=True), policy
    )
    assert decision.template is Template.FACTUAL
    assert "official_source" in decision.reasons


def test_false_never_notifies_even_with_official_source(policy: NotificationPolicy) -> None:
    decision = evaluate(
        make_input(
            verification=VerificationStatus.FALSE, risk_level="critical", has_official_source=True
        ),
        policy,
    )
    assert not decision.notify
    assert "verification_false" in decision.reasons


def test_missing_location_and_date_render_placeholders(policy: NotificationPolicy) -> None:
    for verification in (VerificationStatus.VERIFIED, VerificationStatus.UNVERIFIED):
        decision = evaluate(
            make_input(verification=verification, location_name=None, occurrence_date=None),
            policy,
        )
        assert decision.body is not None
        assert "None" not in decision.body
        assert "$" not in decision.body


def test_decision_records_policy_version(policy: NotificationPolicy) -> None:
    assert evaluate(make_input(), policy).policy_version == policy.policy_version


def test_evaluate_is_deterministic(policy: NotificationPolicy) -> None:
    item = make_input(verification=VerificationStatus.UNVERIFIED)
    assert evaluate(item, policy) == evaluate(item, policy)


# --- 指控（附錄 A 的 A02、A05） ---


def test_a02_single_post_theft_allegation_is_not_stated_as_fact(
    policy: NotificationPolicy,
) -> None:
    """A02 社群單篇失竊指控：未核實；產生中性防竊提醒，不呈現個人指控。"""
    decision = evaluate(
        make_input(
            event_type="security",
            title="松苑餐廳老闆偷了我放在門口的腳踏車",
            verification=VerificationStatus.UNVERIFIED,
            location_name="松苑餐廳",
        ),
        policy,
    )
    assert decision.notify
    assert decision.template is Template.GENERAL_SAFETY
    assert decision.body is not None
    # 疑似指控時連地點也不寫：地點可能就是被指控的店家
    for fragment in ("老闆", "偷了", "腳踏車", "松苑餐廳"):
        assert fragment not in decision.body
    assert "未經查證" in decision.body
    assert policy.event_types["security"].advice in decision.body
    assert "possible_allegation" in decision.reasons


def test_a05_malicious_accusation_against_individual_is_not_stated_as_fact(
    policy: NotificationPolicy,
) -> None:
    """A05 社群貼文惡意誣指個人：通知不呈現指控，管理端標記異常。"""
    decision = evaluate(
        make_input(
            event_type="security",
            title="陳老師在研究室性騷擾學生 0912-345-678",
            risk_level="critical",
            verification=VerificationStatus.UNVERIFIED,
            location_name="科學一館",
        ),
        policy,
    )
    assert decision.notify
    assert decision.template is Template.GENERAL_SAFETY
    assert decision.body is not None
    for fragment in ("陳老師", "性騷擾", "0912"):
        assert fragment not in decision.body
    assert "possible_allegation" in decision.reasons


def test_verified_allegation_still_uses_general_template(policy: NotificationPolicy) -> None:
    """規格 §6.4：涉及可識別個人的指控預設阻擋具體內容，由使用者人工判斷。"""
    decision = evaluate(
        make_input(event_type="security", title="某店家店員詐騙學生"),
        policy,
    )
    assert decision.template is Template.GENERAL_SAFETY
    assert decision.body is not None
    assert "詐騙學生" not in decision.body
    assert "possible_allegation" in decision.reasons


def test_non_allegation_title_is_not_flagged(policy: NotificationPolicy) -> None:
    decision = evaluate(make_input(title="颱風來襲停課"), policy)
    assert "possible_allegation" not in decision.reasons


def test_below_threshold_allegation_is_still_flagged(policy: NotificationPolicy) -> None:
    decision = evaluate(
        make_input(risk_level="medium", title="陳老師偷東西"),
        policy,
    )
    assert not decision.notify
    assert "possible_allegation" in decision.reasons


# --- PII 遮蔽 ---


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("聯絡 0912-345-678", "0912"),
        ("寄信到 someone@ncu.edu.tw", "someone@"),
        ("學號 109502001 遺失學生證", "109502001"),
        ("身分證 A123456789 掉了", "A123456789"),
        ("電話 (03)422-7151", "422-7151"),
    ],
)
def test_mask_pii_removes_identifiers(text: str, leaked: str) -> None:
    masked = mask_pii(text)
    assert leaked not in masked
    assert not find_pii(masked)


def test_mask_pii_keeps_ordinary_text() -> None:
    text = "工程一館 10 月 21 日停電，預計 3 小時後恢復"
    assert mask_pii(text) == text


def test_mask_pii_only_applies_existing_patterns() -> None:
    # 使用者 2026-10-22 決定：只沿用 PII_PATTERNS，不遮蔽「姓氏＋稱謂」
    text = "王小明同學的機車被拖吊"
    assert mask_pii(text) == text


def test_factual_body_is_pii_masked(policy: NotificationPolicy) -> None:
    decision = evaluate(
        make_input(title="工程一館停電，洽詢 03-422-7151 或 ops@ncu.edu.tw"), policy
    )
    assert decision.body is not None
    assert not find_pii(decision.body)
    assert "ops@ncu.edu.tw" not in decision.body


def test_every_notified_body_is_pii_free(policy: NotificationPolicy) -> None:
    for verification in VerificationStatus:
        for official in (False, True):
            decision = evaluate(
                make_input(
                    verification=verification,
                    has_official_source=official,
                    title="李同學 0912345678 回報停電",
                    location_name="工程一館",
                ),
                policy,
            )
            if decision.body is not None:
                assert not find_pii(decision.body)
