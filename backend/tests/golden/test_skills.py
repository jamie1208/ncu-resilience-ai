"""產品 Skills 的 golden 測試（錄製模式，P2-T05）。

每個 Skill（含模板）用 `tests/recorded/` 的錄製回應跑完全部 golden cases：
每筆都要通過 expected／must_not，schema 合格率、可追溯率與 eval/thresholds.yaml 的門檻都要達標。
CI 只跑這個模式，不呼叫真實 API。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ai.eval.runner import evaluate_skill, load_thresholds
from app.ai.llm.config import load_llm_config
from app.ai.runner.loader import load_skill

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
SKILL_DIRS = sorted(p for p in (REPO / "skills").iterdir() if (p / "SKILL.md").is_file())


def test_there_is_at_least_the_template() -> None:
    assert any(p.name == "_template" for p in SKILL_DIRS)


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
async def test_golden_cases_pass_in_recorded_mode(skill_dir: Path) -> None:
    result = await evaluate_skill(
        load_skill(skill_dir),
        split="all",
        mode="recorded",
        config=load_llm_config(REPO / "config" / "llm.yaml"),
        thresholds=load_thresholds(REPO / "eval" / "thresholds.yaml"),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    report = result.report
    failed = [(c["id"], c["status"], c["failures"]) for c in report["cases"] if not c["passed"]]
    assert failed == []
    assert report["passed"] is True, report["gates"] | {"metrics": report["metrics"]}
