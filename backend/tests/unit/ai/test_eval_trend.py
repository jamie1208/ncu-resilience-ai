"""S09 trend-forecaster 的衍生欄位與評測（P7-T01）。

- `explained_signals`：有解釋的統計訊號，依 signal_id 排序成「s1;s3」；abstain 為 None。
  異常由程式（P6-T03 Poisson 變化偵測）判定，模型只解釋被標為異常的訊號，不自行新增。
- `caveats_complete`：一定標註聲量≠事件數（volume_not_event_count），
  資料覆蓋率不是 full 時也標註 limited_coverage；abstain 時同樣檢查。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.ai.eval import runner
from app.ai.eval.derived import trend_forecast_fields
from app.ai.eval.runner import evaluate_skill, load_thresholds
from app.ai.llm.config import load_llm_config
from app.ai.runner.loader import load_skill

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "trend-forecaster"


def _explanation(signal_id: str) -> dict[str, Any]:
    return {"signal_id": signal_id, "summary": "…", "driver": "incident_cluster"}


def _forecast(
    *signal_ids: str,
    status: str = "ok",
    coverage_level: str = "full",
    caveats: tuple[str, ...] = ("volume_not_event_count",),
) -> dict[str, Any]:
    return {
        "status": status,
        "explanations": [_explanation(s) for s in signal_ids],
        "coverage_level": coverage_level,
        "caveats": list(caveats),
    }


def test_explained_signals_sorted_and_deduplicated() -> None:
    fields = trend_forecast_fields(_forecast("s3", "s1", "s3"))
    assert fields["explained_signals"] == "s1;s3"


def test_abstain_has_no_explained_signals() -> None:
    fields = trend_forecast_fields(_forecast(status="abstain"))
    assert fields["explained_signals"] is None


def test_volume_caveat_is_required() -> None:
    assert trend_forecast_fields(_forecast("s1"))["caveats_complete"] is True
    assert trend_forecast_fields(_forecast("s1", caveats=()))["caveats_complete"] is False


@pytest.mark.parametrize("level", ["partial", "low"])
def test_limited_coverage_must_be_flagged(level: str) -> None:
    without = _forecast("s1", coverage_level=level)
    with_flag = _forecast(
        "s1", coverage_level=level, caveats=("volume_not_event_count", "limited_coverage")
    )
    assert trend_forecast_fields(without)["caveats_complete"] is False
    assert trend_forecast_fields(with_flag)["caveats_complete"] is True


def test_abstain_still_checks_caveats() -> None:
    output = _forecast(status="abstain", coverage_level="low")
    assert trend_forecast_fields(output)["caveats_complete"] is False


def test_registered_and_declared() -> None:
    assert runner.DERIVERS["trend-forecaster"] is trend_forecast_fields
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1])["metadata"]
    declared = {f.strip() for f in meta["derived_fields"].split(",")}
    assert declared == {"explained_signals", "caveats_complete"}


def test_thresholds_cover_both_fields() -> None:
    specs = load_thresholds().skills["trend-forecaster"].metrics
    assert {s.field for s in specs} == {"explained_signals", "caveats_complete"}
    caveats = next(s for s in specs if s.field == "caveats_complete")
    assert caveats.threshold == 1.0


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
