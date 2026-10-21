"""風險引擎（P2-T11）：由五個面向的分數計算加權分數與分級。

數值計算由程式完成，模型只提供分項判斷與解釋（AGENTS.md）：
S06 risk-assessor 給五個面向的分數（0–100）與不確定性，這裡依 `config/risk-policy.yaml`
計算加權分數、套用 critical_override 與保底規則，決定分級。
`skills/risk-assessor/scripts/calculate_risk.py` 直接呼叫這裡的函式，兩邊的結果一定相同。

- 分數以 Decimal 計算，四捨五入到小數 2 位，同一輸入永遠得到同一結果；
- 覆寫規則只會提高分級，不會降低；只有真的提高分級的規則才記在 `rules`；
- 不看查證狀態（規格 §6.2：風險是「若事件成立」的影響）。
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.ai import RiskAssessment

DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "config" / "risk-policy.yaml"
DIMENSIONS = ("personal_safety", "operations", "property", "reputation", "scope")
"""五個面向（規格 §6.1）：人身安全、營運、財產、聲譽、影響範圍。"""
CENT = Decimal("0.01")


class Level(BaseModel):
    """一個分級：加權分數 ≥ min 時適用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    min: float = Field(ge=0, le=100)


class CriticalOverride(BaseModel):
    """某一面向達到 min 時，分級至少為 level。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: str
    min: float = Field(ge=0, le=100)
    level: str


class Floor(BaseModel):
    """任一面向達到 min 時，分級至少為 level。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min: float = Field(ge=0, le=100)
    level: str


class RiskPolicy(BaseModel):
    """`config/risk-policy.yaml` 的內容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=32)
    weights: dict[str, float]
    levels: list[Level]
    critical_override: CriticalOverride
    floor: Floor
    high_risk_levels: list[str]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if set(self.weights) != set(DIMENSIONS):
            raise ValueError(f"weights 必須剛好包含五個面向：{'、'.join(DIMENSIONS)}")
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("weights 不可為負")
        if not math.isclose(sum(self.weights.values()), 1.0, abs_tol=1e-9):
            raise ValueError("weights 的總和必須為 1")
        mins = [lv.min for lv in self.levels]
        if not mins or mins[0] != 0 or any(b <= a for a, b in pairwise(mins)):
            raise ValueError("levels 必須從 0 開始，min 嚴格遞增")
        names = [lv.name for lv in self.levels]
        if len(set(names)) != len(names):
            raise ValueError("levels 的名稱不可重複")
        if self.critical_override.dimension not in DIMENSIONS:
            raise ValueError("critical_override.dimension 必須是五個面向之一")
        for name in [self.critical_override.level, self.floor.level, *self.high_risk_levels]:
            if name not in names:
                raise ValueError(f"分級 {name!r} 沒有定義在 levels 中")
        return self

    def rank(self, level: str) -> int:
        """分級的順位（越大越嚴重）。"""
        return [lv.name for lv in self.levels].index(level)


@dataclass(frozen=True)
class RiskResult:
    """計算結果。`weighted_level` 是只看加權分數的分級，`level` 是套用覆寫規則後的分級。"""

    score: Decimal
    weighted_level: str
    level: str
    rules: tuple[str, ...]
    high_risk: bool
    policy_version: str


def load_policy(path: Path = DEFAULT_POLICY) -> RiskPolicy:
    """讀取並驗證風險政策。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RiskPolicy.model_validate(raw)


def _checked(scores: Mapping[str, float]) -> dict[str, Decimal]:
    if set(scores) != set(DIMENSIONS):
        raise ValueError(f"分數必須剛好包含五個面向：{'、'.join(DIMENSIONS)}")
    checked: dict[str, Decimal] = {}
    for dim in DIMENSIONS:
        value = float(scores[dim])
        if math.isnan(value) or not 0 <= value <= 100:
            raise ValueError(f"{dim} 的分數必須在 0–100 之間")
        checked[dim] = Decimal(str(value))
    return checked


def calculate_risk(scores: Mapping[str, float], policy: RiskPolicy) -> RiskResult:
    """依政策計算加權分數與分級；分數不合法時拋出 ValueError。"""
    values = _checked(scores)
    total = sum((Decimal(str(policy.weights[d])) * values[d] for d in DIMENSIONS), start=Decimal(0))
    score = total.quantize(CENT, rounding=ROUND_HALF_UP)
    weighted = next(lv.name for lv in reversed(policy.levels) if score >= Decimal(str(lv.min)))
    level, rules = weighted, []
    override = policy.critical_override
    hit_override = values[override.dimension] >= Decimal(str(override.min))
    if hit_override and policy.rank(override.level) > policy.rank(level):
        level = override.level
        rules.append("critical_override")
    hit_floor = any(v >= Decimal(str(policy.floor.min)) for v in values.values())
    if hit_floor and policy.rank(policy.floor.level) > policy.rank(level):
        level = policy.floor.level
        rules.append("floor")
    return RiskResult(
        score=score,
        weighted_level=weighted,
        level=level,
        rules=tuple(rules),
        high_risk=level in policy.high_risk_levels,
        policy_version=policy.policy_version,
    )


def _scores(output: Mapping[str, Any]) -> dict[str, float] | None:
    dims = output.get("dimensions")
    if output.get("status") != "ok" or not isinstance(dims, Mapping):
        return None
    return {d: float(dims[d]["score"]) for d in DIMENSIONS if isinstance(dims.get(d), Mapping)}


def derived_fields(output: Mapping[str, Any], policy: RiskPolicy | None = None) -> dict[str, Any]:
    """S06 輸出經引擎計算後的欄位（評測與管線共用）；abstain 時都是 None。"""
    scores = _scores(output)
    if scores is None:
        return {"risk_score": None, "risk_level": None, "high_risk": None}
    result = calculate_risk(scores, policy or load_policy())
    return {
        "risk_score": float(result.score),
        "risk_level": result.level,
        "high_risk": result.high_risk,
    }


def assessment_record(
    event_id: uuid.UUID,
    output: Mapping[str, Any],
    policy: RiskPolicy,
    *,
    ai_run_id: uuid.UUID | None = None,
) -> RiskAssessment:
    """建立一筆 risk_assessments 資料列（不寫入）；abstain 的輸出不能建立。

    只存分數、不確定性與命中的規則；模型寫的理由不存（可能含來源內容）。
    """
    scores = _scores(output)
    if scores is None:
        raise ValueError("S06 沒有給出分數（abstain），不能建立風險評估")
    result = calculate_risk(scores, policy)
    dims = output["dimensions"]
    dimensions: dict[str, Any] = {
        d: {"score": dims[d]["score"], "uncertainty": dims[d]["uncertainty"]} for d in DIMENSIONS
    }
    dimensions["_rules"] = list(result.rules)
    dimensions["_weighted_level"] = result.weighted_level
    return RiskAssessment(
        event_id=event_id,
        dimensions=dimensions,
        score=result.score,
        level=result.level,
        policy_version=result.policy_version,
        ai_run_id=ai_run_id,
    )
