"""P2-T11：風險引擎（app/risk/engine.py）與 config/risk-policy.yaml。

使用者 2026-10-21 決定（照建議）：
1. 權重用規格 §6.1：人身安全 0.30、營運 0.20、財產 0.15、聲譽 0.15、影響範圍 0.20；
2. 分級：低 0–29、中 30–59、高 60–79、極高 80–100；
3. critical_override：人身安全 ≥ 80 → 極高；保底：任一面向 ≥ 80 → 至少高；
4. 不看查證狀態（風險是「若事件成立」的影響，規格 §6.2）；
5. 評測的「高風險」＝分級為高或極高。
模型只給五維分數與不確定性，加權、保底與分級由程式計算；同一輸入的結果必須完全相同。
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.risk.engine import (
    DIMENSIONS,
    RiskPolicy,
    assessment_record,
    calculate_risk,
    derived_fields,
    load_policy,
)

REPO = Path(__file__).resolve().parents[4]
POLICY_PATH = REPO / "config" / "risk-policy.yaml"
SCRIPT = REPO / "skills" / "risk-assessor" / "scripts" / "calculate_risk.py"


@pytest.fixture(scope="module")
def policy() -> RiskPolicy:
    return load_policy(POLICY_PATH)


def scores(**kw: float) -> dict[str, float]:
    return {d: kw.get(d, 0) for d in DIMENSIONS}


# --- 設定 ----------------------------------------------------------------------------------


def test_repo_policy_matches_user_decision(policy: RiskPolicy) -> None:
    assert policy.weights == {
        "personal_safety": 0.30,
        "operations": 0.20,
        "property": 0.15,
        "reputation": 0.15,
        "scope": 0.20,
    }
    assert [(lv.name, lv.min) for lv in policy.levels] == [
        ("low", 0),
        ("medium", 30),
        ("high", 60),
        ("critical", 80),
    ]
    assert policy.critical_override.dimension == "personal_safety"
    assert policy.critical_override.min == 80
    assert policy.critical_override.level == "critical"
    assert policy.floor.min == 80 and policy.floor.level == "high"
    assert policy.high_risk_levels == ["high", "critical"]
    assert policy.policy_version


def _raw() -> dict[str, Any]:
    data = load_policy(POLICY_PATH).model_dump()
    return data


def test_weights_must_cover_all_dimensions_and_sum_to_one() -> None:
    data = _raw()
    data["weights"]["scope"] = 0.25
    with pytest.raises(ValidationError, match="總和"):
        RiskPolicy.model_validate(data)
    data = _raw()
    del data["weights"]["scope"]
    with pytest.raises(ValidationError, match="面向"):
        RiskPolicy.model_validate(data)


def test_levels_must_start_at_zero_and_increase() -> None:
    data = _raw()
    data["levels"][0]["min"] = 10
    with pytest.raises(ValidationError):
        RiskPolicy.model_validate(data)
    data = _raw()
    data["levels"][2]["min"] = 20
    with pytest.raises(ValidationError):
        RiskPolicy.model_validate(data)


def test_override_levels_must_exist() -> None:
    data = _raw()
    data["floor"]["level"] = "severe"
    with pytest.raises(ValidationError):
        RiskPolicy.model_validate(data)
    data = _raw()
    data["high_risk_levels"] = ["urgent"]
    with pytest.raises(ValidationError):
        RiskPolicy.model_validate(data)


def test_policy_rejects_unknown_field() -> None:
    data = _raw()
    data["temperature"] = 0.2
    with pytest.raises(ValidationError):
        RiskPolicy.model_validate(data)


# --- 計算 ----------------------------------------------------------------------------------


def test_weighted_score_and_level(policy: RiskPolicy) -> None:
    r = calculate_risk(
        scores(personal_safety=50, operations=50, property=50, reputation=50, scope=50), policy
    )
    assert r.score == Decimal("50.00")
    assert r.weighted_level == r.level == "medium"
    assert r.rules == ()
    assert r.high_risk is False
    assert r.policy_version == policy.policy_version


@pytest.mark.parametrize(
    ("value", "level"),
    [
        (0, "low"),
        (29.99, "low"),
        (30, "medium"),
        (59.99, "medium"),
        (60, "high"),
        (79.99, "high"),
        (80, "critical"),
        (100, "critical"),
    ],
)
def test_level_boundaries(policy: RiskPolicy, value: float, level: str) -> None:
    # 五個面向同分 → 加權分數等於該分數；79.99 以下的單一面向不觸發保底
    r = calculate_risk(scores(**dict.fromkeys(DIMENSIONS, value)), policy)
    assert r.score == Decimal(str(value)).quantize(Decimal("0.01"))
    assert r.weighted_level == level


def test_score_is_rounded_half_up(policy: RiskPolicy) -> None:
    # 0.30 × 1 + 0.15 × 1 = 0.45；0.15 × 0.1 = 0.015 → 0.02
    r = calculate_risk(scores(property=0.1), policy)
    assert r.score == Decimal("0.02")


def test_critical_override_for_personal_safety(policy: RiskPolicy) -> None:
    # 火警有人受傷：加權 56.5（中），人身安全 90 → 極高
    r = calculate_risk(
        scores(personal_safety=90, operations=40, property=50, reputation=40, scope=40), policy
    )
    assert r.score == Decimal("56.50")
    assert r.weighted_level == "medium"
    assert r.level == "critical"
    assert r.rules == ("critical_override",)
    assert r.high_risk is True


def test_floor_for_any_dimension(policy: RiskPolicy) -> None:
    # 整棟停電半天：加權 36.5（中），營運 90 → 至少高
    r = calculate_risk(scores(personal_safety=10, operations=90, reputation=10, scope=70), policy)
    assert r.score == Decimal("36.50")
    assert r.weighted_level == "medium"
    assert r.level == "high"
    assert r.rules == ("floor",)
    assert r.high_risk is True


def test_overrides_never_lower_the_level(policy: RiskPolicy) -> None:
    # 規則只在提高分級時記錄；本來就是極高時不記錄
    r = calculate_risk(scores(**dict.fromkeys(DIMENSIONS, 95)), policy)
    assert r.weighted_level == r.level == "critical"
    assert r.rules == ()


def test_low_event_stays_low(policy: RiskPolicy) -> None:
    r = calculate_risk(scores(property=40, reputation=10, scope=10), policy)
    assert r.level == "low" and r.high_risk is False


@pytest.mark.parametrize(
    "bad",
    [
        {d: 10 for d in DIMENSIONS[:-1]},  # 缺面向
        {**dict.fromkeys(DIMENSIONS, 10), "extra": 5},  # 多面向
        {**dict.fromkeys(DIMENSIONS, 10), "scope": 101},
        {**dict.fromkeys(DIMENSIONS, 10), "scope": -1},
        {**dict.fromkeys(DIMENSIONS, 10), "scope": float("nan")},
    ],
)
def test_invalid_scores_are_rejected(policy: RiskPolicy, bad: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        calculate_risk(bad, policy)


def test_same_input_same_result(policy: RiskPolicy) -> None:
    s = scores(personal_safety=33, operations=71, property=12, reputation=48, scope=66)
    results = {calculate_risk(dict(s), policy) for _ in range(20)}
    assert len(results) == 1
    # 面向的順序不影響結果
    assert calculate_risk(dict(reversed(list(s.items()))), policy) == calculate_risk(s, policy)


# --- 模型輸出 → 衍生欄位與資料列 --------------------------------------------------------------


def output(**kw: float) -> dict[str, Any]:
    return {
        "status": "ok",
        "dimensions": {
            d: {"score": kw.get(d, 0), "uncertainty": 0.2, "reason": "測試"} for d in DIMENSIONS
        },
        "evidence_ids": ["e1"],
    }


def test_derived_fields(policy: RiskPolicy) -> None:
    fields = derived_fields(output(operations=90, scope=70), policy)
    assert fields == {"risk_score": 32.0, "risk_level": "high", "high_risk": True}


def test_derived_fields_for_abstain(policy: RiskPolicy) -> None:
    abstain = {"status": "abstain", "dimensions": None}
    assert derived_fields(abstain, policy) == {
        "risk_score": None,
        "risk_level": None,
        "high_risk": None,
    }


def test_assessment_record(policy: RiskPolicy) -> None:
    event_id, run_id = uuid.uuid4(), uuid.uuid4()
    out = output(personal_safety=90, operations=40, property=50, reputation=40, scope=40)
    rec = assessment_record(event_id, out, policy, ai_run_id=run_id)
    assert rec.event_id == event_id and rec.ai_run_id == run_id
    assert rec.score == Decimal("56.50")
    assert rec.level == "critical"
    assert rec.policy_version == policy.policy_version
    assert rec.dimensions["personal_safety"] == {"score": 90, "uncertainty": 0.2}
    assert rec.dimensions["_rules"] == ["critical_override"]
    assert "reason" not in json.dumps(rec.dimensions)  # 理由是模型文字，不寫進資料列


def test_assessment_record_rejects_abstain(policy: RiskPolicy) -> None:
    with pytest.raises(ValueError):
        assessment_record(uuid.uuid4(), {"status": "abstain", "dimensions": None}, policy)


# --- Skill 的 scripts/calculate_risk.py 與引擎共用同一個函式 ----------------------------------


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("calculate_risk_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_script_uses_engine_function() -> None:
    module = _script()
    assert module.calculate_risk is calculate_risk
    assert module.derived_fields is derived_fields


def test_skill_script_cli_matches_engine(
    tmp_path: Path, policy: RiskPolicy, capsys: pytest.CaptureFixture[str]
) -> None:
    out = output(personal_safety=85, operations=30, scope=50)
    path = tmp_path / "out.json"
    path.write_text(json.dumps(out), encoding="utf-8")
    assert _script().main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == derived_fields(out, policy) | {
        "policy_version": policy.policy_version
    }
