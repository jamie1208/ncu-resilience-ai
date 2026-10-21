"""查證規則（P2-T10）：由每份證據的立場決定事件的查證狀態。

規則優先：最終狀態只由這裡的確定性規則決定。S05 evidence-verifier 只提供每份證據的立場
（`Stance`）與陳述性質（`ClaimType`），以及一個建議狀態；建議狀態只記錄是否一致，不會被採用。
判準依標記規範第 4 節（`data/labeling/guidelines.md`，ADR-0009）：

1. 有官方來源（`ncu-*`、`cwa-alerts`）表態時，以最新一份為準：
   支持 → verified；否認 → false；說法矛盾 → disputed。是否官方只看 source_code，不看模型的判斷。
2. 沒有官方表態時：
   - 支持與反對並存 → disputed；
   - 兩份以上第一手來源支持 → verified；
   - 其他 → unverified（轉述、謠言不算第一手；謠言的支持也不構成爭議）。
3. 規格 A08：verified 一定附上支持的 evidence_ids；沒有就降為 unverified。

狀態變更一律經 `app/domain/states.py` 的 `transition()`。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.domain.states import EventStatus, VerificationStatus, transition
from app.labeling.models import ClaimType

OFFICIAL_PREFIX = "ncu-"
OFFICIAL_SOURCES = frozenset({"cwa-alerts"})
MIN_FIRST_HAND = 2
"""沒有官方來源時，至少要幾份互相獨立的第一手來源支持才算 verified。"""


class Stance(StrEnum):
    """一份證據對事件的立場（S05 的判斷）。"""

    SUPPORTS = "supports"  # 說明事件發生
    CONTRADICTS = "contradicts"  # 說法和事件互相矛盾（時間、地點、事情對不上，或說沒看到）
    REFUTES = "refutes"  # 明確否認事件發生（例如官方澄清）
    UNRELATED = "unrelated"  # 和這個事件無關


_AGAINST = frozenset({Stance.CONTRADICTS, Stance.REFUTES})


@dataclass(frozen=True)
class Assessment:
    """一份證據的判斷；`source_code`、`published_at` 取自輸入，不取自模型。"""

    evidence_id: str
    source_code: str
    stance: Stance
    claim_type: ClaimType | None
    published_at: datetime | None = None


@dataclass(frozen=True)
class Decision:
    """規則的判定。

    `rule` 是命中的規則代碼；`agrees` 表示是否與模型建議一致（沒有建議時為 None）。
    """

    status: VerificationStatus
    rule: str
    evidence_ids: tuple[str, ...]
    suggestion: VerificationStatus | None = None
    agrees: bool | None = None


def is_official_source(source_code: str) -> bool:
    """是否為官方來源：中央大學各單位（`ncu-*`）與中央氣象署（`cwa-alerts`）。"""
    return source_code.startswith(OFFICIAL_PREFIX) or source_code in OFFICIAL_SOURCES


def _ids(items: Sequence[Assessment]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.evidence_id for x in items))


def _unique(assessments: Sequence[Assessment]) -> list[Assessment]:
    seen: set[str] = set()
    unique = []
    for x in assessments:
        if x.evidence_id not in seen:
            seen.add(x.evidence_id)
            unique.append(x)
    return unique


def _official(
    items: Sequence[Assessment],
) -> tuple[VerificationStatus, str, tuple[str, ...]] | None:
    stated = [
        x for x in items if is_official_source(x.source_code) and x.stance is not Stance.UNRELATED
    ]
    if not stated:
        return None
    # 最新一份為準；沒有時間的視為最早，同一時間依輸入順序取最後一份
    latest = max(
        enumerate(stated),
        key=lambda p: (p[1].published_at is not None, p[1].published_at or datetime.min, p[0]),
    )[1]
    if latest.stance is Stance.SUPPORTS:
        return VerificationStatus.VERIFIED, "official_supports", (latest.evidence_id,)
    if latest.stance is Stance.REFUTES:
        return VerificationStatus.FALSE, "official_refutes", (latest.evidence_id,)
    return VerificationStatus.DISPUTED, "official_contradicts", (latest.evidence_id,)


def _decide(items: Sequence[Assessment]) -> tuple[VerificationStatus, str, tuple[str, ...]]:
    official = _official(items)
    if official is not None:
        return official
    supporters = [
        x for x in items if x.stance is Stance.SUPPORTS and x.claim_type is not ClaimType.RUMOR
    ]
    against = [x for x in items if x.stance in _AGAINST]
    if supporters and against:
        return VerificationStatus.DISPUTED, "conflicting_sources", _ids([*supporters, *against])
    first_hand = [x for x in supporters if x.claim_type is ClaimType.FIRST_HAND]
    if len(first_hand) >= MIN_FIRST_HAND:
        return VerificationStatus.VERIFIED, "independent_first_hand", _ids(first_hand)
    return VerificationStatus.UNVERIFIED, "insufficient_evidence", _ids(supporters)


def decide(
    assessments: Sequence[Assessment], *, suggestion: VerificationStatus | None = None
) -> Decision:
    """依規則決定查證狀態；`suggestion`（模型建議）只用來記錄是否一致。"""
    items = _unique(assessments)
    status, rule, evidence_ids = _decide(items)
    if status is VerificationStatus.VERIFIED and not evidence_ids:  # 規格 A08 的最後防線
        status, rule = VerificationStatus.UNVERIFIED, "no_supporting_evidence"
    agrees = None if suggestion is None else suggestion is status
    return Decision(status, rule, evidence_ids, suggestion, agrees)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assessments_from_output(
    output: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]
) -> list[Assessment]:
    """把 S05 輸出的 `assessments` 對應回輸入證據；不存在或重複的 evidence_id 略過。"""
    by_id = {str(e["evidence_id"]): e for e in evidence}
    items: list[Assessment] = []
    seen: set[str] = set()
    for raw in output.get("assessments") or []:
        eid = str(raw.get("evidence_id"))
        if eid not in by_id or eid in seen:
            continue
        seen.add(eid)
        claim = raw.get("claim_type")
        source = by_id[eid]
        items.append(
            Assessment(
                evidence_id=eid,
                source_code=str(source["source_code"]),
                stance=Stance(raw["stance"]),
                claim_type=ClaimType(claim) if claim is not None else None,
                published_at=_parse_time(source.get("published_at")),
            )
        )
    return items


def apply_verification(
    current: VerificationStatus, decision: Decision
) -> VerificationStatus | None:
    """回傳新的查證狀態；沒有改變時回傳 None（狀態機不允許自我轉移）。"""
    if decision.status is current:
        return None
    return transition(current, decision.status)


_EVENT_FOR = {
    VerificationStatus.UNVERIFIED: EventStatus.UNVERIFIED,
    VerificationStatus.VERIFIED: EventStatus.VERIFIED,
    VerificationStatus.DISPUTED: EventStatus.DISPUTED,
    VerificationStatus.FALSE: EventStatus.FALSE,
}
_FOLLOW_UP = frozenset({EventStatus.MONITORING, EventStatus.RESOLVED})


def event_status_for(current: EventStatus, verification: VerificationStatus) -> EventStatus | None:
    """查證結果對應的事件狀態；不需要改變時回傳 None，不合法時拋出 StateTransitionError。

    已進入後續追蹤（monitoring、resolved）的事件，查證結果為 verified 或 unverified 時不改變；
    為 disputed、false 時依狀態機改判（resolved 不允許，需先重新開啟）。
    """
    target = _EVENT_FOR[verification]
    if target is current:
        return None
    if current in _FOLLOW_UP and target in (EventStatus.VERIFIED, EventStatus.UNVERIFIED):
        return None
    return transition(current, target)
