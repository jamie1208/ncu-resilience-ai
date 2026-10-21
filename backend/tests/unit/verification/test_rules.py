"""P2-T10：查證規則（app/verification/rules.py）。

規則優先：最終查證狀態只由確定性規則決定，模型（S05）只提供每份證據的立場與陳述性質、以及建議狀態。
判準依標記規範第 4 節（ADR-0009）：
- 官方來源以最新一份的立場為準：支持 → verified；否認 → false；矛盾 → disputed；
- 非官方：支持與反對並存 → disputed；兩份以上第一手來源支持 → verified；
  其餘 → unverified；
- 規格 A08：沒有支持證據的主張不得標記 verified。
狀態變更一律經 app/domain/states.py 的 transition()。
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.domain.states import EventStatus, StateTransitionError, VerificationStatus
from app.labeling.models import ClaimType
from app.verification.rules import (
    Assessment,
    Stance,
    apply_verification,
    assessments_from_output,
    decide,
    event_status_for,
    is_official_source,
)

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "evidence-verifier"

V = VerificationStatus
S = Stance
C = ClaimType
T1 = datetime(2026, 10, 19, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 20, 1, tzinfo=UTC)


def a(
    eid: str,
    source: str,
    stance: Stance,
    claim: ClaimType | None = ClaimType.FIRST_HAND,
    at: datetime | None = None,
) -> Assessment:
    return Assessment(eid, source, stance, claim, at)


# --- 官方來源 -------------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["ncu-announcements", "ncu-cc", "ncu-news", "cwa-alerts"])
def test_official_sources(code: str) -> None:
    assert is_official_source(code)


@pytest.mark.parametrize("code", ["facebook", "ncuer", "news-ncu", "cwa"])
def test_non_official_sources(code: str) -> None:
    assert not is_official_source(code)


def test_official_support_is_verified_even_against_social_contradiction() -> None:
    d = decide([a("e1", "ncu-cc", S.SUPPORTS, C.OFFICIAL), a("e2", "facebook", S.CONTRADICTS)])
    assert d.status is V.VERIFIED
    assert d.rule == "official_supports"
    assert d.evidence_ids == ("e1",)


def test_official_claim_type_from_model_does_not_make_social_post_official() -> None:
    # 模型把社群貼文標成 official 也沒用：是否官方只看 source_code
    d = decide([a("e1", "facebook", S.SUPPORTS, C.OFFICIAL)])
    assert d.status is V.UNVERIFIED


def test_latest_official_refutation_makes_it_false() -> None:
    d = decide(
        [
            a("e1", "ncu-announcements", S.SUPPORTS, C.OFFICIAL, T1),
            a("e2", "ncu-announcements", S.REFUTES, C.OFFICIAL, T2),
        ]
    )
    assert d.status is V.FALSE
    assert d.rule == "official_refutes"
    assert d.evidence_ids == ("e2",)


def test_latest_official_support_wins_over_earlier_refutation() -> None:
    d = decide(
        [
            a("e1", "ncu-osa-life", S.REFUTES, C.OFFICIAL, T1),
            a("e2", "ncu-osa-life", S.SUPPORTS, C.OFFICIAL, T2),
        ]
    )
    assert d.status is V.VERIFIED


def test_official_refutation_of_social_rumor_is_false() -> None:
    d = decide(
        [a("e1", "facebook", S.SUPPORTS, C.HEARSAY, T1), a("e2", "ncu-aa", S.REFUTES, None, T2)]
    )
    assert d.status is V.FALSE


def test_official_contradiction_is_disputed() -> None:
    d = decide([a("e1", "facebook", S.SUPPORTS), a("e2", "ncu-cc", S.CONTRADICTS, C.OFFICIAL)])
    assert d.status is V.DISPUTED
    assert d.rule == "official_contradicts"


def test_unrelated_official_evidence_is_ignored() -> None:
    d = decide([a("e1", "ncu-news", S.UNRELATED, C.OFFICIAL), a("e2", "facebook", S.SUPPORTS)])
    assert d.status is V.UNVERIFIED


# --- 非官方來源 -----------------------------------------------------------------------------


def test_single_first_hand_is_unverified() -> None:
    d = decide([a("e1", "facebook", S.SUPPORTS)])
    assert d.status is V.UNVERIFIED
    assert d.rule == "insufficient_evidence"
    assert d.evidence_ids == ("e1",)


def test_two_independent_first_hand_are_verified() -> None:
    d = decide([a("e1", "facebook", S.SUPPORTS), a("e2", "facebook", S.SUPPORTS)])
    assert d.status is V.VERIFIED
    assert d.rule == "independent_first_hand"
    assert d.evidence_ids == ("e1", "e2")


@pytest.mark.parametrize("claim", [C.HEARSAY, C.RUMOR])
def test_hearsay_and_rumor_do_not_count_as_first_hand(claim: ClaimType) -> None:
    d = decide([a("e1", "facebook", S.SUPPORTS), a("e2", "facebook", S.SUPPORTS, claim)])
    assert d.status is V.UNVERIFIED


def test_same_evidence_twice_is_not_independent() -> None:
    d = decide([a("e1", "facebook", S.SUPPORTS), a("e1", "facebook", S.SUPPORTS)])
    assert d.status is V.UNVERIFIED


def test_support_and_contradiction_are_disputed() -> None:
    d = decide(
        [
            a("e1", "facebook", S.SUPPORTS),
            a("e2", "facebook", S.SUPPORTS),
            a("e3", "facebook", S.CONTRADICTS),
        ]
    )
    assert d.status is V.DISPUTED
    assert d.rule == "conflicting_sources"
    assert d.evidence_ids == ("e1", "e2", "e3")


def test_rumor_support_does_not_create_dispute() -> None:
    d = decide([a("e1", "facebook", S.SUPPORTS, C.RUMOR), a("e2", "facebook", S.CONTRADICTS)])
    assert d.status is V.UNVERIFIED


def test_only_contradiction_is_unverified() -> None:
    d = decide([a("e1", "facebook", S.REFUTES)])
    assert d.status is V.UNVERIFIED


def test_no_evidence_is_unverified() -> None:
    d = decide([])
    assert d.status is V.UNVERIFIED
    assert d.evidence_ids == ()


# --- 規格 A08 與模型建議 ---------------------------------------------------------------------

ALL_STANCES = list(Stance)
SOURCES = ["facebook", "ncu-cc"]
CLAIMS = [*ClaimType, None]


def test_a08_verified_always_has_supporting_evidence() -> None:
    """窮舉 1～2 份證據的所有組合：verified 時 evidence_ids 一定非空、且都是支持的證據。"""
    singles = [(src, st, cl) for src in SOURCES for st in ALL_STANCES for cl in CLAIMS]
    combos = [(x,) for x in singles] + list(itertools.product(singles, repeat=2))
    for combo in combos:
        items = [a(f"e{i}", src, st, cl, T1) for i, (src, st, cl) in enumerate(combo)]
        d = decide(items)
        if d.status is V.VERIFIED:
            supporting = {x.evidence_id for x in items if x.stance is S.SUPPORTS}
            assert d.evidence_ids, combo
            assert set(d.evidence_ids) <= supporting, combo


def test_model_suggestion_never_overrides_rules() -> None:
    d = decide([a("e1", "facebook", S.SUPPORTS, C.HEARSAY)], suggestion=V.VERIFIED)
    assert d.status is V.UNVERIFIED
    assert d.suggestion is V.VERIFIED
    assert d.agrees is False


def test_agreement_is_recorded() -> None:
    d = decide([a("e1", "ncu-cc", S.SUPPORTS, C.OFFICIAL)], suggestion=V.VERIFIED)
    assert d.agrees is True
    assert decide([a("e1", "ncu-cc", S.SUPPORTS)]).agrees is None


# --- 模型輸出 → Assessment -----------------------------------------------------------------

EVIDENCE: list[dict[str, Any]] = [
    {
        "evidence_id": "e1",
        "source_code": "facebook",
        "text": "x",
        "published_at": "2026-10-19T01:00:00Z",
    },
    {"evidence_id": "e2", "source_code": "ncu-cc", "text": "y", "published_at": None},
]


def test_assessments_from_output_joins_input_evidence() -> None:
    output = {
        "assessments": [
            {"evidence_id": "e1", "stance": "supports", "claim_type": "first_hand"},
            {"evidence_id": "e2", "stance": "refutes", "claim_type": "official"},
        ]
    }
    items = assessments_from_output(output, EVIDENCE)
    assert items == [
        Assessment(
            "e1", "facebook", S.SUPPORTS, C.FIRST_HAND, datetime(2026, 10, 19, 1, tzinfo=UTC)
        ),
        Assessment("e2", "ncu-cc", S.REFUTES, C.OFFICIAL, None),
    ]


def test_assessments_from_output_drops_unknown_and_duplicate_evidence() -> None:
    output = {
        "assessments": [
            {"evidence_id": "e9", "stance": "supports", "claim_type": "official"},
            {"evidence_id": "e1", "stance": "supports", "claim_type": "first_hand"},
            {"evidence_id": "e1", "stance": "supports", "claim_type": "first_hand"},
        ]
    }
    assert [x.evidence_id for x in assessments_from_output(output, EVIDENCE)] == ["e1"]


def test_assessments_from_abstain_output_is_empty() -> None:
    assert assessments_from_output({"status": "abstain", "assessments": []}, EVIDENCE) == []
    assert assessments_from_output({}, EVIDENCE) == []


# --- 狀態機 -----------------------------------------------------------------------------------


def test_apply_verification_uses_state_machine() -> None:
    assert apply_verification(V.UNVERIFIED, decide([a("e1", "ncu-cc", S.SUPPORTS)])) is V.VERIFIED
    # 狀態沒有改變時不轉移（自我轉移在狀態機中是非法的）
    assert apply_verification(V.UNVERIFIED, decide([])) is None


@pytest.mark.parametrize(
    ("current", "verification", "expected"),
    [
        (EventStatus.UNVERIFIED, V.VERIFIED, EventStatus.VERIFIED),
        (EventStatus.UNVERIFIED, V.UNVERIFIED, None),
        (EventStatus.VERIFIED, V.FALSE, EventStatus.FALSE),
        (EventStatus.FALSE, V.VERIFIED, EventStatus.VERIFIED),
        (EventStatus.DISPUTED, V.UNVERIFIED, EventStatus.UNVERIFIED),
        (EventStatus.MONITORING, V.FALSE, EventStatus.FALSE),
        (EventStatus.MONITORING, V.VERIFIED, None),
        (EventStatus.MONITORING, V.UNVERIFIED, None),
        (EventStatus.RESOLVED, V.VERIFIED, None),
    ],
)
def test_event_status_for(
    current: EventStatus, verification: VerificationStatus, expected: EventStatus | None
) -> None:
    assert event_status_for(current, verification) == expected


@pytest.mark.parametrize(
    ("current", "verification"),
    [
        (EventStatus.DETECTED, V.VERIFIED),
        (EventStatus.ANALYZING, V.VERIFIED),
        (EventStatus.RESOLVED, V.FALSE),
        (EventStatus.ARCHIVED, V.VERIFIED),
    ],
)
def test_event_status_for_rejects_illegal_transitions(
    current: EventStatus, verification: VerificationStatus
) -> None:
    with pytest.raises(StateTransitionError):
        event_status_for(current, verification)


# --- 與 S05 golden cases 一致 ------------------------------------------------------------------


def _golden() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    cases = [
        json.loads(line)
        for line in (SKILL / "tests" / "cases.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    pairs = []
    for case in cases:
        rec = SKILL / "tests" / "recorded" / f"{case['id']}.json"
        pairs.append((case, json.loads(rec.read_text("utf-8"))["output"]))
    return pairs


def test_rules_reproduce_expected_status_from_recorded_assessments() -> None:
    """錄製回應的逐份判斷經過規則後，必須得到 golden case 的預期狀態；模型的建議只是參考。"""
    mismatched = []
    for case, output in _golden():
        expected = case["expected"].get("suggested_status")
        if output.get("status") != "ok" or expected is None:
            continue
        items = assessments_from_output(output, case["input"]["evidence"])
        decision = decide(items, suggestion=V(output["suggested_status"]))
        if decision.status.value != expected:
            mismatched.append((case["id"], decision.status.value, expected))
        if decision.status is V.VERIFIED:
            assert decision.evidence_ids, case["id"]
    assert mismatched == []
