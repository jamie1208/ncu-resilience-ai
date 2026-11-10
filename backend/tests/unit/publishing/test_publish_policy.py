"""P5-T10：公開規則引擎（app/publishing/policy.py、config/publish-policy.yaml）。

硬性邊界寫在程式裡，設定檔無法關閉；每條邊界各有正反案例（P8-T06 起依 ADR-0018、ADR-0020，
使用者 2026-11-05 同意修改，只限放寬的規則）：
1. 不是 false 的事件都公開 AI 摘要；只有官方來源證實或使用者查證為 true 標「已查證」，其他一律標
   「尚未查證」，不得呈現為已核實；
2. 疑似指控（P3-T07 的指控規則命中）照 AI 摘要公開，標記 needs_review（同步時產生公開提醒）；
3. 舊小報告的 S07 推測原因（hypothesized）不公開，公開版小報告只放影響與建議；
4. Facebook 來源只公開 AI 摘要，不放原文與連結；
5. 公開內容一律經 PII 遮蔽。
附錄 A 的 A02、A05 公開 AI 摘要（遮蔽個資、不含原文與連結），並標記給使用者判斷。

系列分析頁（`evaluate_series`）：只列公開中的子事件；規律與原因一律由規則加上「推測，尚未查證」
（人工確認的為「已確認」）；逐字證據、證據 ID、to_verify、依據的原因不公開；提到特定對象的比照
疑似指控標記；件數依公開的子事件重算。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
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
    BurstInput,
    ConfirmedBy,
    PublicationInput,
    PublicLevel,
    PublicSource,
    PublishPolicy,
    ReportInput,
    SeriesAnalysisInput,
    SeriesMemberInput,
    SeriesPublicationInput,
    evaluate,
    evaluate_series,
    load_policy,
    review_parts,
)
from app.series.timeline import before_after, build_timeline

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
    assert "none" in policy.excluded_event_types


@pytest.mark.parametrize(
    "key",
    [
        "publish_unverified_content",
        "publish_allegations",
        "publish_hypothesized_causes",
        "publish_facebook_original",
        "mask_pii",
        "publish_unlabeled_speculation",
        "remind_on_publish",
    ],
)
def test_hard_boundaries_cannot_be_configured(key: str) -> None:
    """設定檔不接受任何關閉硬性邊界的欄位。"""
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw[key] = key != "mask_pii"
    with pytest.raises(ValidationError):
        PublishPolicy.model_validate(raw)


def test_retired_safety_notice_levels_is_rejected() -> None:
    """P8-T06：未經證實的事件不再依風險等級改寫成安全提醒，舊設定不能再放回來。"""
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw["safety_notice_levels"] = ["high", "critical"]
    with pytest.raises(ValidationError):
        PublishPolicy.model_validate(raw)


# --- 邊界 1：不是 false 的事件都公開 AI 摘要；只有官方證實或使用者查證為 true 才標「已查證」 ---


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
    assert decision.content["verification"] == "verified"
    assert decision.content["verification_label"] == "已查證"
    assert decision.policy_version == policy.policy_version


def test_confirmed_event_without_risk_assessment_still_publishes_content(
    policy: PublishPolicy,
) -> None:
    """官方公告（例如閉館）常沒有風險評估，仍公開事件內容。"""
    decision = evaluate(make_input(risk_level=None, report=None), policy)
    assert decision.level is PublicLevel.CONTENT
    assert decision.content is not None
    assert decision.content["report"] is None


def test_first_hand_verified_without_official_or_user_is_labelled_unverified(
    policy: PublishPolicy,
) -> None:
    """兩份第一手來源支持（P2-T10 規則）不是官方證實，也不是使用者查證：公開但標「尚未查證」。"""
    decision = evaluate(make_input(confirmed_by=ConfirmedBy.FIRST_HAND, risk_level="high"), policy)
    assert decision.level is PublicLevel.CONTENT
    assert "not_confirmed" in decision.reasons
    assert decision.content is not None
    assert decision.content["verification"] == "unverified"
    assert decision.content["verification_label"] == "尚未查證"
    assert "已查證" not in dumped(decision)


@pytest.mark.parametrize(
    "verification", [VerificationStatus.UNVERIFIED, VerificationStatus.DISPUTED]
)
def test_unverified_or_disputed_publishes_ai_summary_labelled_unverified(
    policy: PublishPolicy, verification: VerificationStatus
) -> None:
    decision = evaluate(
        make_input(verification=verification, confirmed_by=None, risk_level="high"), policy
    )
    assert decision.level is PublicLevel.CONTENT
    assert decision.content is not None
    assert decision.content["title"] == "總圖書館三樓漏水"
    assert decision.content["summary"] == "總圖書館三樓天花板漏水，部分座位暫停使用。"
    assert decision.content["verification"] == verification.value
    assert decision.content["verification_label"] == "尚未查證"
    assert "已查證" not in dumped(decision)


def test_confirmed_by_ignored_unless_verification_is_verified(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(
            verification=VerificationStatus.DISPUTED,
            confirmed_by=ConfirmedBy.USER,
            risk_level="high",
        ),
        policy,
    )
    assert decision.content is not None
    assert decision.content["verification_label"] == "尚未查證"


@pytest.mark.parametrize("risk_level", ["low", "medium", None])
def test_unverified_low_risk_or_unassessed_is_still_public(
    policy: PublishPolicy, risk_level: str | None
) -> None:
    """ADR-0018 2-1：未查證事件全站公開（含低風險與尚未評估），只有 false 或封存時撤下。"""
    decision = evaluate(
        make_input(
            verification=VerificationStatus.UNVERIFIED, confirmed_by=None, risk_level=risk_level
        ),
        policy,
    )
    assert decision.level is PublicLevel.CONTENT
    assert decision.content is not None
    assert decision.content["verification_label"] == "尚未查證"


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


# --- 邊界 2：疑似指控照 AI 摘要公開，標記給使用者判斷（同步時產生公開提醒） ---


def test_allegation_in_title_is_published_and_flagged_for_review(
    policy: PublishPolicy,
) -> None:
    decision = evaluate(
        make_input(event_type="security", title="警衛在停車場偷走機車", risk_level="high"),
        policy,
    )
    assert decision.level is PublicLevel.CONTENT
    assert decision.needs_review
    assert "possible_allegation" in decision.reasons
    assert decision.content is not None
    assert decision.content["title"] == "警衛在停車場偷走機車"
    assert decision.content["location"] == "總圖書館"


@pytest.mark.parametrize(
    "field",
    ["summary", "location", "impact_reason", "measure_action"],
)
def test_allegation_anywhere_in_public_text_is_flagged_for_review(
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
    elif field == "location":
        item = replace(item, location_name=accusation)
    decision = evaluate(item, policy)
    assert decision.level is PublicLevel.CONTENT
    assert decision.needs_review
    assert "possible_allegation" in decision.reasons


def test_no_allegation_does_not_need_review(policy: PublishPolicy) -> None:
    decision = evaluate(make_input(), policy)
    assert not decision.needs_review
    assert "possible_allegation" not in decision.reasons


def test_a02_single_post_theft_allegation_publishes_ai_summary_only(
    policy: PublishPolicy,
) -> None:
    """A02 社群單篇失竊指控：照 AI 摘要公開並標「尚未查證」；不放社群原文與連結，標記給使用者。"""
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
    assert decision.level is PublicLevel.CONTENT
    assert decision.needs_review
    assert decision.content is not None
    assert decision.content["summary"] == "有人在社群指稱松苑餐廳老闆偷走腳踏車。"
    assert decision.content["verification_label"] == "尚未查證"
    assert decision.content["sources"] == []
    body = dumped(decision)
    for leaked in ("facebook", "Facebook", FACEBOOK.url or ""):
        assert leaked not in body


def test_a05_malicious_accusation_publishes_masked_ai_summary(policy: PublishPolicy) -> None:
    """A05 匿名惡意誣指個人：AI 摘要照公開但遮蔽個資、標「尚未查證」；管理端標記異常。"""
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
    assert decision.level is PublicLevel.CONTENT
    assert decision.needs_review
    assert decision.content is not None
    assert decision.content["verification_label"] == "尚未查證"
    body = dumped(decision)
    assert "0912" not in body
    assert find_pii(body) == []


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


def test_unverified_content_is_pii_masked(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(
            verification=VerificationStatus.UNVERIFIED,
            confirmed_by=None,
            risk_level="high",
            location_name="宿舍 0912-345-678",
        ),
        policy,
    )
    assert decision.level is PublicLevel.CONTENT
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


def test_allegation_in_action_title_is_flagged_for_review(policy: PublishPolicy) -> None:
    accusation = "調查警衛偷竊座位上的筆電"
    decision = evaluate(make_input(actions=(make_action(title=accusation),)), policy)
    assert decision.level is PublicLevel.CONTENT
    assert decision.needs_review
    assert "possible_allegation" in decision.reasons


def test_progress_is_pii_masked(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(actions=(make_action(title="聯絡承包商 0912-345-678 排定維修"),)), policy
    )
    assert decision.level is PublicLevel.CONTENT
    assert find_pii(dumped(decision)) == []


def test_unverified_content_keeps_label_with_progress(policy: PublishPolicy) -> None:
    decision = evaluate(
        make_input(
            verification=VerificationStatus.UNVERIFIED,
            confirmed_by=None,
            risk_level="high",
            actions=(make_action(),),
        ),
        policy,
    )
    assert decision.content is not None
    assert decision.content["verification_label"] == "尚未查證"
    assert [a["title"] for a in decision.content["progress"]] == ["更換三樓天花板防水層"]


# --- 系列分析頁（P8-T06，ADR-0018 決策 1-9、2-3，ADR-0020 決策 2） ---

E1, E2, E3 = (uuid.UUID(int=101), uuid.UUID(int=102), uuid.UUID(int=103))
P1, P2, P3 = (uuid.UUID(int=201), uuid.UUID(int=202), uuid.UUID(int=203))
HIDDEN = uuid.UUID(int=104)  # 不公開（例如已下架）的子事件
QUOTE = "我的腳踏車停在宿舍門口就不見了，鎖還在地上"


def member(event_id: uuid.UUID, pub_id: uuid.UUID, day: int, **overrides: Any) -> SeriesMemberInput:
    values: dict[str, Any] = {
        "event_id": event_id,
        "publication_id": pub_id,
        "title": f"宿舍腳踏車失竊（{day} 日）",
        "occurrence_date": date(2026, 10, day),
        "location": "女十四舍",
        "verified": False,
    }
    values.update(overrides)
    return SeriesMemberInput(**values)


def make_analysis(**overrides: Any) -> SeriesAnalysisInput:
    values: dict[str, Any] = {
        "summary": "宿舍區腳踏車失竊接連發生。",
        "location_groups": [
            {"label": "宿舍區", "reason": "都在宿舍門口", "event_ids": [str(E1), str(E2)]}
        ],
        "patterns": [
            {
                "pattern_id": "p1",
                "description": "多發生在週末夜間",
                "label": "推測，尚未查證",
                "event_ids": [str(E1), str(E2), str(HIDDEN)],
                "event_count": 3,
                "total": 4,
                "quotes": [{"evidence_id": "a1", "text": QUOTE}],
                "evidence_ids": ["a1"],
                "mentions_party": False,
            }
        ],
        "causes": [
            {
                "cause_id": "c1",
                "description": "車棚照明不足",
                "label": "推測，尚未查證",
                "to_verify": "請總務處確認照明紀錄",
                "event_ids": [str(E1)],
                "quotes": [{"evidence_id": "a1", "text": QUOTE}],
                "evidence_ids": ["a1"],
                "mentions_party": False,
            }
        ],
        "measures": [
            {
                "action": "加裝車棚照明",
                "audience": "school",
                "horizon": "mid",
                "priority": "high",
                "kind": "prevent",
                "cause_id": "c1",
                "responsible_unit": "總務處",
                "evidence_ids": ["a1"],
            }
        ],
    }
    values.update(overrides)
    return SeriesAnalysisInput(**values)


def make_series(**overrides: Any) -> SeriesPublicationInput:
    values: dict[str, Any] = {
        "series_id": uuid.UUID(int=9),
        "category": "security",
        "name": "宿舍腳踏車失竊",
        "members": (member(E1, P1, 3), member(E2, P2, 10), member(E3, P3, 17, verified=True)),
        "analysis": make_analysis(),
    }
    values.update(overrides)
    return SeriesPublicationInput(**values)


def test_series_publishes_public_members_with_labels(policy: PublishPolicy) -> None:
    decision = evaluate_series(make_series(), policy)
    assert decision.level is PublicLevel.CONTENT
    content = decision.content
    assert content is not None
    assert content["kind"] == "series"
    assert content["name"] == "宿舍腳踏車失竊"
    assert content["event_count"] == 3
    assert [e["id"] for e in content["events"]] == [str(P1), str(P2), str(P3)]
    assert [e["ordinal"] for e in content["events"]] == [1, 2, 3]
    assert [e["verification_label"] for e in content["events"]] == [
        "尚未查證",
        "尚未查證",
        "已查證",
    ]
    assert (content["first_date"], content["latest_date"]) == ("2026-10-03", "2026-10-17")


def test_series_without_public_members_is_not_public(policy: PublishPolicy) -> None:
    decision = evaluate_series(make_series(members=()), policy)
    assert decision.level is PublicLevel.NONE
    assert decision.content is None
    assert "no_public_events" in decision.reasons


def test_series_of_excluded_category_is_not_public(policy: PublishPolicy) -> None:
    decision = evaluate_series(make_series(category="none"), policy)
    assert decision.level is PublicLevel.NONE


def test_series_patterns_and_causes_always_carry_label_from_policy(
    policy: PublishPolicy,
) -> None:
    """未帶推測標籤不得公開：輸入的標籤被拿掉或改寫，公開時一律由規則加上。"""
    base = make_analysis()
    analysis = make_analysis(
        patterns=[{**base.patterns[0], "label": "已確認"}],
        causes=[{k: v for k, v in base.causes[0].items() if k != "label"}],
    )
    content = evaluate_series(make_series(analysis=analysis), policy).content
    assert content is not None
    assert [p["label"] for p in content["patterns"]] == ["推測，尚未查證"]
    assert [c["label"] for c in content["causes"]] == ["推測，尚未查證"]
    assert content["causes"][0]["decision"] == "speculative"


def test_confirmed_series_cause_is_labelled_confirmed(policy: PublishPolicy) -> None:
    analysis = replace(make_analysis(), confirmed_cause_ids=frozenset({"c1"}))
    decision = evaluate_series(make_series(analysis=analysis), policy)
    assert decision.content is not None
    assert decision.content["causes"][0]["label"] == "已確認"
    assert decision.content["causes"][0]["decision"] == "confirmed"
    # 規律仍是推測
    assert "speculation" in decision.reasons


def test_series_never_publishes_quotes_evidence_or_internal_ids(policy: PublishPolicy) -> None:
    decision = evaluate_series(make_series(), policy)
    body = dumped(decision)
    for leaked in (
        QUOTE,
        "evidence_ids",
        "quotes",
        "to_verify",
        "請總務處確認照明紀錄",
        "cause_id",
        "mentions_party",
        "reason",
        "都在宿舍門口",
    ):
        assert leaked not in body, leaked
    for internal in (E1, E2, E3, HIDDEN):
        assert str(internal) not in body


def test_series_counts_are_recomputed_from_public_members(policy: PublishPolicy) -> None:
    content = evaluate_series(make_series(), policy).content
    assert content is not None
    pattern = content["patterns"][0]
    assert pattern["related_ids"] == [str(P1), str(P2)]
    assert (pattern["event_count"], pattern["total"]) == (2, 3)
    group = content["location_groups"][0]
    assert (group["label"], group["event_count"], group["total"]) == ("宿舍區", 2, 3)


def test_series_pattern_needs_two_public_events(policy: PublishPolicy) -> None:
    base = make_analysis()
    analysis = make_analysis(patterns=[{**base.patterns[0], "event_ids": [str(E1), str(HIDDEN)]}])
    decision = evaluate_series(make_series(analysis=analysis), policy)
    assert decision.content is not None
    assert decision.content["patterns"] == []
    assert decision.content["pattern_status"] == "abstained"


def test_single_event_series_has_no_patterns_and_a_note(policy: PublishPolicy) -> None:
    decision = evaluate_series(make_series(members=(member(E1, P1, 3),)), policy)
    assert decision.content is not None
    assert decision.content["patterns"] == []
    assert decision.content["pattern_note"] == "要至少 2 起才會找規律"
    assert decision.content["event_count"] == 1


def test_series_measures_are_public_without_their_cause(policy: PublishPolicy) -> None:
    content = evaluate_series(make_series(), policy).content
    assert content is not None
    assert content["measures"] == [
        {
            "action": "加裝車棚照明",
            "audience": "school",
            "horizon": "mid",
            "priority": "high",
            "kind": "prevent",
            "responsible_unit": "總務處",
        }
    ]


def test_series_cause_mentioning_a_party_is_flagged_like_allegation(
    policy: PublishPolicy,
) -> None:
    base = make_analysis()
    analysis = make_analysis(causes=[{**base.causes[0], "description": "疑似警衛巡邏時段有空檔"}])
    decision = evaluate_series(make_series(analysis=analysis), policy)
    assert decision.level is PublicLevel.CONTENT
    assert decision.needs_review
    assert "mentions_party" in decision.reasons
    assert decision.content is not None
    assert decision.content["causes"][0]["label"] == "推測，尚未查證"


def test_series_stored_mentions_flag_is_respected(policy: PublishPolicy) -> None:
    base = make_analysis()
    analysis = make_analysis(causes=[{**base.causes[0], "mentions_party": True}])
    decision = evaluate_series(make_series(analysis=analysis), policy)
    assert decision.needs_review
    assert "mentions_party" in decision.reasons


def test_series_without_speculation_does_not_need_review(policy: PublishPolicy) -> None:
    decision = evaluate_series(make_series(analysis=None), policy)
    assert decision.level is PublicLevel.CONTENT
    assert not decision.needs_review
    assert "speculation" not in decision.reasons
    assert decision.content is not None
    assert decision.content["patterns"] == decision.content["causes"] == []


def test_series_allegation_in_progress_title_is_flagged(policy: PublishPolicy) -> None:
    action = make_action(title="調查警衛偷竊腳踏車")
    decision = evaluate_series(make_series(analysis=None, actions=(action,)), policy)
    assert decision.needs_review
    assert "possible_allegation" in decision.reasons


def test_series_content_is_pii_masked(policy: PublishPolicy) -> None:
    pii = "聯絡 0912-345-678"
    base = make_analysis()
    analysis = make_analysis(
        summary=pii,
        patterns=[{**base.patterns[0], "description": pii}],
        causes=[{**base.causes[0], "description": pii}],
        measures=[{**base.measures[0], "action": pii}],
    )
    decision = evaluate_series(
        make_series(
            name=f"失竊 {pii}",
            analysis=analysis,
            members=(member(E1, P1, 3, title=pii), member(E2, P2, 10)),
            actions=(make_action(title=pii),),
        ),
        policy,
    )
    assert find_pii(dumped(decision)) == []


def test_series_comparison_has_averages_and_caveat_but_no_ratio(policy: PublishPolicy) -> None:
    dates = [date(2026, 9, 1) + timedelta(days=3 * i) for i in range(20)]
    comparison = before_after(dates, date(2026, 9, 25), today=date(2026, 11, 10))
    decision = evaluate_series(make_series(comparisons=(("加裝車棚照明", comparison),)), policy)
    assert decision.content is not None
    [shown] = decision.content["comparisons"]
    assert shown["status"] == comparison.status
    assert shown["caveat"].startswith("變少不一定是措施的功勞")
    assert "change_ratio" not in shown


def test_series_bursts_and_timeline_are_programmatic(policy: PublishPolicy) -> None:
    timeline = build_timeline(
        [date(2026, 10, 3), date(2026, 10, 10)],
        granularity="week",
        range_key="all",
        today=date(2026, 10, 20),
    )
    burst = BurstInput(date(2026, 10, 5), 3, 0.4567)
    decision = evaluate_series(make_series(timeline=timeline, bursts=(burst,)), policy)
    assert decision.content is not None
    assert decision.content["bursts"] == [
        {"period_start": "2026-10-05", "count": 3, "expected": 0.46}
    ]
    points = decision.content["timeline"]["points"]
    assert [p["count"] for p in points] == [p.count for p in timeline.points]


def test_evaluate_series_is_deterministic(policy: PublishPolicy) -> None:
    item = make_series()
    assert evaluate_series(item, policy) == evaluate_series(item, policy)


def test_review_parts_change_only_with_speculation_or_allegation(policy: PublishPolicy) -> None:
    first = evaluate_series(make_series(), policy)
    longer = evaluate_series(
        make_series(
            members=(*make_series().members, member(uuid.UUID(int=105), uuid.UUID(int=205), 24))
        ),
        policy,
    )
    changed = evaluate_series(
        make_series(
            analysis=make_analysis(
                causes=[{**make_analysis().causes[0], "description": "車棚沒有監視器"}]
            )
        ),
        policy,
    )
    parts = [review_parts(d.content, d.reasons) for d in (first, longer, changed)]
    assert parts[0] == parts[1]  # 只多了一起事件，推測內容沒變
    assert parts[0] != parts[2]
    assert review_parts(evaluate(make_input(), policy).content, ()) == []
    flagged = evaluate(make_input(title="警衛在停車場偷走機車"), policy)
    assert review_parts(flagged.content, flagged.reasons) != []
