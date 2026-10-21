"""P2-T11：評測的衍生欄位。

有些結果不是模型輸出，而是程式由輸出計算出來的，例如 S06 的風險分級（app/risk/engine.py）。
評測時先把衍生欄位併進輸出，再比對 expected 與計算指標；錄製回應只存模型的原始輸出。
衍生欄位不得覆蓋模型輸出的欄位。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.ai.eval import runner
from app.ai.eval.runner import evaluate_skill, load_thresholds, with_derived
from app.ai.llm.config import load_llm_config
from app.ai.llm.fake_provider import FakeProvider
from app.ai.runner.loader import load_skill
from app.risk.engine import derived_fields

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[4]
TEMPLATE = REPO / "skills" / "_template"


def test_risk_assessor_uses_engine() -> None:
    assert runner.DERIVERS["risk-assessor"] is derived_fields


def test_with_derived_merges_without_overriding() -> None:
    def deriver(output: dict[str, Any]) -> dict[str, Any]:
        return {"extra": 1, "status": "overridden"}

    merged = with_derived({"status": "ok"}, deriver)
    assert merged == {"status": "ok", "extra": 1}
    assert with_derived(None, deriver) is None
    assert with_derived({"status": "ok"}, None) == {"status": "ok"}


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "_template"
    shutil.copytree(TEMPLATE, dst)
    path = dst / "tests" / "cases.jsonl"
    cases = [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]
    for c in cases:
        c["expected"]["is_maintenance"] = c["expected"].get("announcement_type") == "maintenance"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), "utf-8")
    return dst


def _derive(output: dict[str, Any]) -> dict[str, Any]:
    return {"is_maintenance": output.get("announcement_type") == "maintenance"}


async def _evaluate(skill_dir: Path, **kw: Any) -> dict[str, Any]:
    result = await evaluate_skill(
        load_skill(skill_dir),
        split="all",
        config=load_llm_config(REPO / "config" / "llm.yaml"),
        thresholds=load_thresholds(REPO / "eval" / "thresholds.yaml"),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        **{"mode": "recorded", **kw},
    )
    report: dict[str, Any] = result.report
    return report


async def test_expected_can_check_derived_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = _copy(tmp_path)
    without = await _evaluate(skill_dir)
    assert without["summary"]["passed"] < without["summary"]["cases"]
    monkeypatch.setitem(runner.DERIVERS, "template", _derive)
    report = await _evaluate(skill_dir)
    assert report["summary"]["passed"] == report["summary"]["cases"]


async def test_recordings_store_only_model_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = _copy(tmp_path)
    monkeypatch.setitem(runner.DERIVERS, "template", _derive)
    first = sorted((skill_dir / "tests" / "recorded").glob("*.json"))[0]
    output = json.loads(first.read_text("utf-8"))["output"]
    provider = FakeProvider([output] * 30)
    await _evaluate(skill_dir, mode="live", provider=provider, record=True)
    saved = json.loads(first.read_text("utf-8"))["output"]
    assert "is_maintenance" not in saved
