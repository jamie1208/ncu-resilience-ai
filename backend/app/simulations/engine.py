"""What-if 引擎（P7-T02，規格 §8.3、F09、A09）。

第一版用透明規則：`baseline × Π(1 − effect_i)`。

- `baseline` 是時間窗內的基準件數（被回報、被彙整成事件的數量，不是實際發生次數）；
- 每項措施的效果是使用者給的「降低比例」假設區間 [low, high]（0–1），不是實測成效；
  效果未知（None）的措施不猜數字，排除在計算之外並列在 `excluded_measure_ids`；
  全部未知時回傳 `not_estimable`（無法估計），不自造百分比；
- 點估計取各區間中點；上下界為效果全取下限（最保守）與全取上限（最樂觀）；
- 分位數以固定種子的 Monte Carlo 抽樣（效果在區間內均勻分布、彼此獨立），
  同輸入同種子結果一致（G7-1）；
- 敏感度（龍捲風圖）：一次只讓一項措施在區間兩端移動，其他措施固定在中點；
- 成本只分級（低／中／高／未知），不換算成效益。

數值都由這裡計算；S10 scenario-simulator 只解釋假設與限制。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

ENGINE_VERSION = "1.0.0"
MAX_MEASURES = 10
MAX_SEED = 2**31 - 1


class ScenarioInputError(ValueError):
    """輸入不合法；`field` 指出出錯的欄位。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class SimulationStatus(StrEnum):
    """estimated：至少一項措施有效果假設；not_estimable：效果全部未知。"""

    ESTIMATED = "estimated"
    NOT_ESTIMABLE = "not_estimable"


class CostTier(StrEnum):
    """成本分級；未填成本為 unknown。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EngineParams:
    """引擎參數（純計算參數，不放設定檔）。

    `cost_tiers`（新台幣）：低於第一個值為 low，低於第二個值為 medium，其餘為 high。
    """

    samples: int = 2000
    cost_tiers: tuple[int, int] = (50_000, 500_000)


@dataclass(frozen=True)
class EffectRange:
    """措施的假設效果：件數降低比例的區間（0 ≤ low ≤ high ≤ 1）。"""

    low: float
    high: float

    @property
    def mid(self) -> float:
        """區間中點。"""
        return (self.low + self.high) / 2


@dataclass(frozen=True)
class Measure:
    """一項措施；`effect` 為 None 代表效果未知，`cost` 為 None 代表成本未知（新台幣）。"""

    measure_id: str
    name: str
    effect: EffectRange | None
    cost: int | None = None


@dataclass(frozen=True)
class ScenarioInput:
    """一次模擬的輸入。`location`、`event_type` 只是情境描述，不影響計算。"""

    baseline: float
    window_days: int
    measures: tuple[Measure, ...]
    seed: int
    location: str | None = None
    event_type: str | None = None


@dataclass(frozen=True)
class Estimate:
    """模擬後的件數：點估計、上下界、抽樣分位數，以及點估計的降低比例。"""

    point: float
    low: float
    high: float
    p05: float
    p50: float
    p95: float
    reduction: float


@dataclass(frozen=True)
class MeasureResult:
    """一項措施的結果；未納入計算（效果未知）時敏感度欄位為 None。"""

    measure_id: str
    included: bool
    cost_tier: CostTier
    at_low: float | None
    at_high: float | None
    swing: float | None
    rank: int | None


@dataclass(frozen=True)
class SimulationResult:
    """模擬結果；`estimate` 只在 estimated 時有值。"""

    engine_version: str
    seed: int
    samples: int
    status: SimulationStatus
    baseline: float
    estimate: Estimate | None
    measures: tuple[MeasureResult, ...]
    excluded_measure_ids: tuple[str, ...]
    total_cost: int | None
    total_cost_tier: CostTier

    def sensitivity(self) -> list[MeasureResult]:
        """納入計算的措施，依敏感度（swing）由大到小。"""
        ranked = [m for m in self.measures if m.rank is not None]
        return sorted(ranked, key=lambda m: m.rank or 0)

    def to_dict(self) -> dict[str, Any]:
        """可直接存成 JSON 的結果。"""
        est = self.estimate
        return {
            "engine_version": self.engine_version,
            "seed": self.seed,
            "samples": self.samples,
            "status": self.status.value,
            "baseline": self.baseline,
            "estimate": None
            if est is None
            else {
                "point": est.point,
                "low": est.low,
                "high": est.high,
                "p05": est.p05,
                "p50": est.p50,
                "p95": est.p95,
                "reduction": est.reduction,
            },
            "measures": [
                {
                    "measure_id": m.measure_id,
                    "included": m.included,
                    "cost_tier": m.cost_tier.value,
                    "at_low": m.at_low,
                    "at_high": m.at_high,
                    "swing": m.swing,
                    "rank": m.rank,
                }
                for m in self.measures
            ],
            "excluded_measure_ids": list(self.excluded_measure_ids),
            "total_cost": self.total_cost,
            "total_cost_tier": self.total_cost_tier.value,
        }


def _validate(scenario: ScenarioInput, params: EngineParams) -> None:
    if not math.isfinite(scenario.baseline) or scenario.baseline < 0:
        raise ScenarioInputError("baseline", "基準件數必須是 0 以上的有限數字")
    if scenario.window_days < 1:
        raise ScenarioInputError("window_days", "時間窗至少 1 天")
    if not 0 <= scenario.seed <= MAX_SEED:
        raise ScenarioInputError("seed", f"隨機種子必須介於 0 與 {MAX_SEED}")
    if not 1 <= len(scenario.measures) <= MAX_MEASURES:
        raise ScenarioInputError("measures", f"措施數量必須介於 1 與 {MAX_MEASURES}")
    ids = [m.measure_id for m in scenario.measures]
    if len(set(ids)) != len(ids):
        raise ScenarioInputError("measures", "措施代號不可重複")
    for i, m in enumerate(scenario.measures):
        e = m.effect
        if e is not None and not (0 <= e.low <= e.high <= 1):
            raise ScenarioInputError(f"measures.{i}.effect", "效果區間必須滿足 0 ≤ 下限 ≤ 上限 ≤ 1")
        if m.cost is not None and m.cost < 0:
            raise ScenarioInputError(f"measures.{i}.cost", "成本不可為負")
    if params.samples < 1:
        raise ScenarioInputError("samples", "抽樣次數至少 1")


def _cost_tier(cost: int | None, params: EngineParams) -> CostTier:
    if cost is None:
        return CostTier.UNKNOWN
    low, high = params.cost_tiers
    if cost < low:
        return CostTier.LOW
    return CostTier.MEDIUM if cost < high else CostTier.HIGH


def _apply(baseline: float, effects: Sequence[float]) -> float:
    value = baseline
    for e in effects:
        value *= 1 - e
    return value


def _quantile(ordered: Sequence[float], q: float) -> float:
    """線性內插的分位數；`ordered` 須已排序。"""
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _r(value: float) -> float:
    return round(value, 2)


def simulate(scenario: ScenarioInput, params: EngineParams | None = None) -> SimulationResult:
    """執行一次模擬；輸入不合法時拋出 `ScenarioInputError`。"""
    params = params or EngineParams()
    _validate(scenario, params)
    baseline = scenario.baseline
    known = [m for m in scenario.measures if m.effect is not None]
    excluded = tuple(m.measure_id for m in scenario.measures if m.effect is None)

    costs = [m.cost for m in scenario.measures]
    total_cost = None if any(c is None for c in costs) else sum(c or 0 for c in costs)
    tiers = {m.measure_id: _cost_tier(m.cost, params) for m in scenario.measures}

    estimate: Estimate | None = None
    swings: dict[str, tuple[float, float, float]] = {}
    if known:
        ranges = [m.effect for m in known if m.effect is not None]
        mids = [e.mid for e in ranges]
        rng = random.Random(scenario.seed)  # noqa: S311 — 模擬抽樣要可重現，不是密碼用途
        draws = sorted(
            _apply(baseline, [rng.uniform(e.low, e.high) for e in ranges])
            for _ in range(params.samples)
        )
        estimate = Estimate(
            point=_r(_apply(baseline, mids)),
            low=_r(_apply(baseline, [e.high for e in ranges])),
            high=_r(_apply(baseline, [e.low for e in ranges])),
            p05=_r(_quantile(draws, 0.05)),
            p50=_r(_quantile(draws, 0.5)),
            p95=_r(_quantile(draws, 0.95)),
            reduction=round(1 - _apply(1.0, mids), 4),
        )
        for i, m in enumerate(known):
            e = ranges[i]
            at_low = _apply(baseline, [*mids[:i], e.low, *mids[i + 1 :]])
            at_high = _apply(baseline, [*mids[:i], e.high, *mids[i + 1 :]])
            swings[m.measure_id] = (_r(at_low), _r(at_high), _r(at_low - at_high))

    order = sorted(swings, key=lambda mid: (-swings[mid][2], mid))
    ranks = {mid: i + 1 for i, mid in enumerate(order)}
    measures = tuple(
        MeasureResult(
            measure_id=m.measure_id,
            included=m.measure_id in swings,
            cost_tier=tiers[m.measure_id],
            at_low=swings[m.measure_id][0] if m.measure_id in swings else None,
            at_high=swings[m.measure_id][1] if m.measure_id in swings else None,
            swing=swings[m.measure_id][2] if m.measure_id in swings else None,
            rank=ranks.get(m.measure_id),
        )
        for m in scenario.measures
    )
    return SimulationResult(
        engine_version=ENGINE_VERSION,
        seed=scenario.seed,
        samples=params.samples,
        status=SimulationStatus.ESTIMATED if known else SimulationStatus.NOT_ESTIMABLE,
        baseline=baseline,
        estimate=estimate,
        measures=measures,
        excluded_measure_ids=excluded,
        total_cost=total_cost,
        total_cost_tier=_cost_tier(total_cost, params),
    )
