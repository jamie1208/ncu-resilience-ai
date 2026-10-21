"""P2-T10：評測的計次指標 `unsupported_count`。

S05 的門檻：無 evidence_ids 被標 verified 的次數 = 0。

計次指標以上限（`max`）判定，數值 ≤ max 才通過；比例型指標仍以 `threshold`（≥）判定。
「有支持的證據」的定義：輸出的 `assessments` 中有 stance 為 supports、且 evidence_id 也列在
`evidence_ids` 中的項目。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.ai.eval.metrics import unsupported_count
from app.ai.eval.runner import MetricSpec, Thresholds, evaluate_skill, load_thresholds
from app.ai.llm.config import load_llm_config
from app.ai.runner.loader import load_skill

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[4]


def out(status: str | None, ids: list[str], assessments: list[dict[str, Any]]) -> dict[str, Any]:
    return {"suggested_status": status, "evidence_ids": ids, "assessments": assessments}


SUPPORT = {"evidence_id": "e1", "stance": "supports"}


def test_unsupported_count() -> None:
    outputs: list[dict[str, Any] | None] = [
        out("verified", ["e1"], [SUPPORT]),  # 有支持證據
        out("verified", [], [SUPPORT]),  # 沒有引用 → 違規
        out("verified", ["e1"], [{"evidence_id": "e1", "stance": "contradicts"}]),  # 違規
        out("verified", ["e2"], [SUPPORT]),  # 支持的證據沒有被引用 → 違規
        out("verified", ["e1"], []),  # 違規
        out("unverified", [], []),  # 不是 verified，不計
        {"suggested_status": "verified", "evidence_ids": ["e1"]},  # 沒有 assessments → 違規
        None,  # 沒有通過驗證的輸出不計
    ]
    assert unsupported_count(outputs, field="suggested_status", positive="verified") == 5


def test_unsupported_count_empty() -> None:
    assert unsupported_count([], field="suggested_status", positive="verified") == 0


def test_metric_spec_requires_max_for_count_and_threshold_for_ratio() -> None:
    MetricSpec(name="x", type="unsupported_count", field="s", positive="verified", max=0)
    MetricSpec(name="y", type="accuracy", field="s", threshold=0.9)
    with pytest.raises(ValidationError):
        MetricSpec(name="x", type="unsupported_count", field="s", threshold=0.9)
    with pytest.raises(ValidationError):
        MetricSpec(name="y", type="accuracy", field="s", max=0)
    with pytest.raises(ValidationError):
        MetricSpec(name="y", type="accuracy", field="s")


def test_repo_thresholds_have_s05_zero_limit() -> None:
    specs = load_thresholds(REPO / "eval" / "thresholds.yaml").skills["evidence-verifier"].metrics
    [spec] = [s for s in specs if s.type == "unsupported_count"]
    assert spec.max == 0
    assert spec.field == "suggested_status"
    assert spec.positive == "verified"


def _thresholds(positive: str) -> Thresholds:
    base = load_thresholds(REPO / "eval" / "thresholds.yaml")
    spec = MetricSpec(
        name="unsupported",
        type="unsupported_count",
        field="announcement_type",
        positive=positive,
        max=0,
    )
    skills = {
        **base.skills,
        "template": base.skills["template"].model_copy(update={"metrics": [spec]}),
    }
    return base.model_copy(update={"skills": skills})


@pytest.mark.parametrize(("positive", "passed"), [("no-such-type", True), ("maintenance", False)])
async def test_runner_gates_count_metric_by_max(positive: str, passed: bool) -> None:
    # 模板輸出沒有 assessments：預測為 positive 的輸出都算違規
    result = await evaluate_skill(
        load_skill(REPO / "skills" / "_template"),
        split="test",
        mode="recorded",
        config=load_llm_config(REPO / "config" / "llm.yaml"),
        thresholds=_thresholds(positive),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    [metric] = result.report["metrics"]
    assert metric["max"] == 0
    assert metric["threshold"] is None
    assert metric["passed"] is passed
    assert (metric["value"] == 0) is passed
    assert result.report["passed"] is passed
