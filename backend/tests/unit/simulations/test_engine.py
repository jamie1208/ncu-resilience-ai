"""P7-T02：What-if 引擎（規格 §8.3、F09、A09）。

- 透明規則：baseline × Π(1 − effect_i)；效果是使用者給的假設區間，不是實測成效；
- 同輸入同種子結果一致（G7-1），不同種子只影響抽樣分位數，不影響點估計與上下界；
- 效果值全部未知時回傳「無法估計」，不自造百分比；部分未知時只用已知的措施並列出排除者；
- 敏感度（龍捲風圖）：一次只讓一項措施在區間兩端移動，其他固定在中點；
- 成本只分級（低／中／高／未知），不換算成效益。
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from app.simulations.engine import (
    ENGINE_VERSION,
    CostTier,
    EffectRange,
    EngineParams,
    Measure,
    ScenarioInput,
    ScenarioInputError,
    SimulationStatus,
    simulate,
)


def _input(*measures: Measure, baseline: float = 20.0, seed: int = 42) -> ScenarioInput:
    return ScenarioInput(baseline=baseline, window_days=180, measures=measures, seed=seed)


def _m(
    measure_id: str, low: float | None, high: float | None, cost: int | None = 10_000
) -> Measure:
    effect = None if low is None or high is None else EffectRange(low, high)
    return Measure(measure_id=measure_id, name=f"措施 {measure_id}", effect=effect, cost=cost)


TWO = (_m("m1", 0.1, 0.3), _m("m2", 0.2, 0.6, cost=800_000))


def test_same_input_and_seed_is_reproducible() -> None:
    first = simulate(_input(*TWO))
    second = simulate(_input(*TWO))
    assert first == second
    assert first.to_dict() == second.to_dict()
    assert first.engine_version == ENGINE_VERSION
    assert first.seed == 42


def test_seed_only_changes_sampled_quantiles() -> None:
    a = simulate(_input(*TWO, seed=1))
    b = simulate(_input(*TWO, seed=2))
    assert a.estimate is not None and b.estimate is not None
    assert a.estimate.point == b.estimate.point
    assert (a.estimate.low, a.estimate.high) == (b.estimate.low, b.estimate.high)
    assert a.estimate.p50 != b.estimate.p50


def test_transparent_rule() -> None:
    result = simulate(_input(*TWO))
    est = result.estimate
    assert result.status is SimulationStatus.ESTIMATED
    assert est is not None
    assert est.point == pytest.approx(20 * (1 - 0.2) * (1 - 0.4), abs=0.01)
    # 最樂觀：效果都取上限；最保守：都取下限
    assert est.low == pytest.approx(20 * 0.7 * 0.4, abs=0.01)
    assert est.high == pytest.approx(20 * 0.9 * 0.8, abs=0.01)
    assert est.low <= est.p05 <= est.p50 <= est.p95 <= est.high
    assert est.reduction == pytest.approx(1 - 0.8 * 0.6, abs=0.001)


def test_all_effects_unknown_is_not_estimable() -> None:
    result = simulate(_input(_m("m1", None, None), _m("m2", None, None)))
    assert result.status is SimulationStatus.NOT_ESTIMABLE
    assert result.estimate is None
    assert result.excluded_measure_ids == ("m1", "m2")
    assert all(m.swing is None for m in result.measures)
    data = result.to_dict()
    assert data["estimate"] is None
    assert data["status"] == "not_estimable"


def test_unknown_effect_is_excluded_not_guessed() -> None:
    result = simulate(_input(_m("m1", 0.2, 0.2), _m("m2", None, None)))
    assert result.status is SimulationStatus.ESTIMATED
    assert result.excluded_measure_ids == ("m2",)
    assert result.estimate is not None
    assert result.estimate.point == pytest.approx(16.0)
    m2 = next(m for m in result.measures if m.measure_id == "m2")
    assert m2.included is False and m2.swing is None and m2.rank is None


def test_sensitivity_ranks_by_swing() -> None:
    result = simulate(_input(*TWO))
    by_id = {m.measure_id: m for m in result.measures}
    m1, m2 = by_id["m1"], by_id["m2"]
    # m1 在 0.1–0.3 之間移動，m2 固定在中點 0.4
    assert m1.at_low == pytest.approx(20 * 0.9 * 0.6, abs=0.01)
    assert m1.at_high == pytest.approx(20 * 0.7 * 0.6, abs=0.01)
    assert m1.swing == pytest.approx(m1.at_low - m1.at_high, abs=0.01)
    assert m2.swing is not None and m1.swing is not None
    assert m2.swing > m1.swing
    assert (m2.rank, m1.rank) == (1, 2)
    assert [m.measure_id for m in result.sensitivity()] == ["m2", "m1"]


def test_point_effect_has_no_swing() -> None:
    result = simulate(_input(_m("m1", 0.25, 0.25)))
    assert result.measures[0].swing == 0
    assert result.estimate is not None
    assert result.estimate.low == result.estimate.high == result.estimate.point == 15.0


def test_cost_tiers() -> None:
    result = simulate(
        _input(
            _m("m1", 0.1, 0.2, cost=0),
            _m("m2", 0.1, 0.2, cost=50_000),
            _m("m3", 0.1, 0.2, cost=500_000),
            _m("m4", 0.1, 0.2, cost=None),
        )
    )
    tiers = {m.measure_id: m.cost_tier for m in result.measures}
    assert tiers == {
        "m1": CostTier.LOW,
        "m2": CostTier.MEDIUM,
        "m3": CostTier.HIGH,
        "m4": CostTier.UNKNOWN,
    }
    assert result.total_cost is None
    assert result.total_cost_tier is CostTier.UNKNOWN

    known = simulate(_input(_m("m1", 0.1, 0.2, cost=30_000), _m("m2", 0.1, 0.2, cost=30_000)))
    assert known.total_cost == 60_000
    assert known.total_cost_tier is CostTier.MEDIUM


def test_zero_baseline_stays_zero() -> None:
    result = simulate(_input(*TWO, baseline=0))
    assert result.estimate is not None
    assert result.estimate.point == result.estimate.high == 0
    assert result.estimate.reduction == pytest.approx(1 - 0.8 * 0.6, abs=0.001)


def test_to_dict_is_json_ready() -> None:
    data = simulate(_input(*TWO)).to_dict()
    assert data["engine_version"] == ENGINE_VERSION
    assert data["seed"] == 42
    assert data["samples"] == EngineParams().samples
    assert data["status"] == "estimated"
    assert data["total_cost_tier"] == "high"
    assert [m["measure_id"] for m in data["measures"]] == ["m1", "m2"]
    assert data["measures"][1]["cost_tier"] == "high"
    assert data["measures"][1]["rank"] == 1


@pytest.mark.parametrize(
    "build",
    [
        lambda: _input(_m("m1", 0.1, 0.2), baseline=-1),
        lambda: _input(_m("m1", 0.1, 0.2), baseline=math.nan),
        lambda: _input(_m("m1", 0.1, 0.2), baseline=math.inf),
        lambda: _input(_m("m1", 0.3, 0.2)),
        lambda: _input(_m("m1", -0.1, 0.2)),
        lambda: _input(_m("m1", 0.1, 1.2)),
        lambda: _input(_m("m1", 0.1, 0.2), _m("m1", 0.1, 0.2)),
        lambda: _input(),
        lambda: _input(_m("m1", 0.1, 0.2, cost=-5)),
        lambda: _input(_m("m1", 0.1, 0.2), seed=-1),
        lambda: _input(*(_m(f"m{i}", 0.1, 0.2) for i in range(1, 12))),
        lambda: ScenarioInput(baseline=1, window_days=0, measures=(_m("m1", 0.1, 0.2),), seed=1),
    ],
)
def test_invalid_input(build: Callable[[], ScenarioInput]) -> None:
    with pytest.raises(ScenarioInputError):
        simulate(build())


def test_custom_params() -> None:
    params = EngineParams(samples=50, cost_tiers=(100, 1_000))
    result = simulate(_input(_m("m1", 0.1, 0.2, cost=500)), params)
    assert result.samples == 50
    assert result.measures[0].cost_tier is CostTier.MEDIUM
