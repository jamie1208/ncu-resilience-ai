"""S10 scenario-simulator 的衍生欄位與評測（P7-T02）。

- `explained_measures`：有解釋的措施，依 measure_id 排序成「m1;m2」；abstain 為 None。
  只解釋納入引擎計算的措施（效果未知的不猜）。
- `limitation_set`：限制標註依代碼排序成「hypothetical_not_prediction;independence_assumed」；
  abstain 時同樣比對。
- `hypothetical_flagged`：一定標明假設情境、不是成效預測（hypothetical_not_prediction）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.ai.eval import runner
from app.ai.eval.derived import scenario_fields
from app.ai.eval.runner import evaluate_skill, load_thresholds
from app.ai.llm.config import load_llm_config
from app.ai.runner.loader import load_skill

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "scenario-simulator"


def _output(
    *measure_ids: str,
    status: str = "ok",
    limitations: tuple[str, ...] = ("hypothetical_not_prediction",),
) -> dict[str, Any]:
    return {
        "status": status,
        "assumptions": [
            {"measure_id": m, "statement": "…", "evidence_ids": [m]} for m in measure_ids
        ],
        "limitations": list(limitations),
    }


def test_explained_measures_sorted_and_deduplicated() -> None:
    assert scenario_fields(_output("m2", "m1", "m2"))["explained_measures"] == "m1;m2"


def test_abstain_has_no_explained_measures() -> None:
    assert scenario_fields(_output(status="abstain"))["explained_measures"] is None


def test_limitation_set_sorted() -> None:
    output = _output("m1", limitations=("independence_assumed", "hypothetical_not_prediction"))
    assert scenario_fields(output)["limitation_set"] == (
        "hypothetical_not_prediction;independence_assumed"
    )


def test_hypothetical_flag_required_even_when_abstaining() -> None:
    assert scenario_fields(_output("m1"))["hypothetical_flagged"] is True
    missing = _output(status="abstain", limitations=("unknown_effects",))
    assert scenario_fields(missing)["hypothetical_flagged"] is False


def test_registered_and_declared() -> None:
    assert runner.DERIVERS["scenario-simulator"] is scenario_fields
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1])["metadata"]
    declared = {f.strip() for f in meta["derived_fields"].split(",")}
    assert declared == {"explained_measures", "limitation_set", "hypothetical_flagged"}


def test_thresholds() -> None:
    specs = {s.field: s for s in load_thresholds().skills["scenario-simulator"].metrics}
    assert set(specs) == {
        "explained_measures",
        "key_driver",
        "limitation_set",
        "hypothetical_flagged",
    }
    assert specs["hypothetical_flagged"].threshold == 1.0


@pytest.mark.anyio
async def test_recorded_eval_passes() -> None:
    result = await evaluate_skill(
        load_skill(SKILL),
        split="all",
        mode="recorded",
        config=load_llm_config(),
        thresholds=load_thresholds(),
        clock=lambda: datetime(2026, 11, 4, tzinfo=UTC),
    )
    report = result.report
    assert report["summary"]["passed"] == report["summary"]["cases"]
    assert report["passed"] is True
