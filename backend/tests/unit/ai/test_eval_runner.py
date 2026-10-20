"""P2-T05：評測流程（錄製模式、實測模式、費用上限、報告與摘要）。

使用者 2026-10-20 決定：
- 指標與門檻集中在 `eval/thresholds.yaml`；
- 評測不寫資料庫：費用記在報告，`--max-cost` 限制每次執行的總費用（預設等於每日上限）；
- 報告進 git，只放統計與每筆的判定；完整輸出放 `eval/runs/`（不進 git）。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic_settings import SettingsConfigDict

from app.ai.eval.runner import (
    Thresholds,
    ThresholdsError,
    evaluate_skill,
    load_thresholds,
    main,
    resolve_skill,
    write_summary,
)
from app.ai.llm.base import LLMUnavailableError, Usage
from app.ai.llm.config import LLMConfig, load_llm_config
from app.ai.llm.fake_provider import FakeProvider
from app.ai.runner.loader import Skill, load_skill
from app.core.config import Settings

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[4]
TEMPLATE = REPO / "skills" / "_template"
NOW = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def skill() -> Skill:
    return load_skill(TEMPLATE)


@pytest.fixture(scope="module")
def config() -> LLMConfig:
    return load_llm_config(REPO / "config" / "llm.yaml")


@pytest.fixture(scope="module")
def thresholds() -> Thresholds:
    return load_thresholds(REPO / "eval" / "thresholds.yaml")


def cases(split: str | None = None) -> list[dict[str, Any]]:
    lines = (TEMPLATE / "tests" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    all_cases = [json.loads(ln) for ln in lines if ln.strip()]
    return [c for c in all_cases if split is None or c["split"] == split]


def recorded_outputs(split: str) -> list[dict[str, Any]]:
    return [
        json.loads((TEMPLATE / "tests" / "recorded" / f"{c['id']}.json").read_text("utf-8"))[
            "output"
        ]
        for c in cases(split)
    ]


async def no_sleep(_: float) -> None:
    return None


async def evaluate(skill: Skill, config: LLMConfig, thresholds: Thresholds, **kw: Any) -> Any:
    kw.setdefault("split", "test")
    kw.setdefault("mode", "recorded")
    kw.setdefault("sleep", no_sleep)
    return await evaluate_skill(
        skill, config=config, thresholds=thresholds, clock=lambda: NOW, **kw
    )


# --- 門檻設定 ------------------------------------------------------------------------------


def test_repo_thresholds(thresholds: Thresholds) -> None:
    assert thresholds.global_.schema_pass_rate == 0.99
    assert thresholds.global_.traceability == 1.0
    [metric] = thresholds.skills["template"].metrics
    assert metric.type == "macro_f1" and metric.field == "announcement_type"


def test_thresholds_reject_unknown_metric(tmp_path: Path) -> None:
    path = tmp_path / "t.yaml"
    path.write_text(
        "version: 1\nglobal: {schema_pass_rate: 0.99, traceability: 1.0}\n"
        "skills:\n  x:\n    metrics: [{name: m, type: bleu, field: a, threshold: 0.5}]\n",
        encoding="utf-8",
    )
    with pytest.raises(ThresholdsError):
        load_thresholds(path)


# --- 錄製模式 ------------------------------------------------------------------------------


async def test_recorded_template_passes(
    skill: Skill, config: LLMConfig, thresholds: Thresholds
) -> None:
    result = await evaluate(skill, config, thresholds)
    r = result.report
    assert r["skill"] == "template" and r["version"] == "0.1.0" and r["schema_version"] == "1"
    assert r["mode"] == "recorded" and r["split"] == "test"
    assert r["provider"] == "fake" and r["model"] == "hand-written"
    s = r["summary"]
    assert s["cases"] == 10 and s["passed"] == 10 and s["pass_rate"] == 1.0
    assert s["schema_pass_rate"] == 1.0
    assert s["traceability"] == 1.0
    assert s["cost_usd"] == 0
    assert s["by_type"]["adversarial"] == {"cases": 3, "passed": 3}
    [m] = r["metrics"]
    assert m["name"] == "macro_f1" and m["value"] == 1.0 and m["passed"] is True
    assert r["gates"]["schema_pass_rate"]["passed"] is True
    assert r["passed"] is True
    assert r["incomplete"] is False


async def test_split_all(skill: Skill, config: LLMConfig, thresholds: Thresholds) -> None:
    result = await evaluate(skill, config, thresholds, split="all")
    assert result.report["summary"]["cases"] == 20


async def test_case_rows_hold_labels_only(
    skill: Skill, config: LLMConfig, thresholds: Thresholds
) -> None:
    result = await evaluate(skill, config, thresholds, split="all")
    row = next(c for c in result.report["cases"] if c["id"] == "adversarial-003")
    assert row == {
        "id": "adversarial-003",
        "type": "adversarial",
        "split": "dev",
        "status": "succeeded",
        "passed": True,
        "failures": [],
        "expected": {"status": "ok", "announcement_type": "maintenance"},
        "predicted": {"status": "ok", "announcement_type": "maintenance"},
    }
    dumped = json.dumps(result.report, ensure_ascii=False)
    assert "0912-345-678" not in dumped  # 案例輸入中的虛構電話
    assert "管理學院 11/3 停電檢修" not in dumped  # 來源內文


async def test_reports_and_runs_are_written(
    skill: Skill, config: LLMConfig, thresholds: Thresholds, tmp_path: Path
) -> None:
    result = await evaluate(
        skill, config, thresholds, out_dir=tmp_path / "reports", runs_dir=tmp_path / "runs"
    )
    assert result.path == tmp_path / "reports" / "template" / "0.1.0.recorded.json"
    assert json.loads(result.path.read_text("utf-8")) == result.report
    [raw] = list((tmp_path / "runs" / "template").glob("*.jsonl"))
    rows = [json.loads(ln) for ln in raw.read_text("utf-8").splitlines()]
    assert len(rows) == 10
    assert rows[0]["output"]["status"] in ("ok", "abstain")


def broken_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "_template"
    shutil.copytree(TEMPLATE, dst)
    rec = dst / "tests" / "recorded" / "normal-002.json"  # test split，答案是 maintenance
    data = json.loads(rec.read_text("utf-8"))
    data["output"]["announcement_type"] = "event"
    rec.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (dst / "tests" / "recorded" / "normal-004.json").unlink()
    return dst


async def test_wrong_and_missing_recordings_fail(
    config: LLMConfig, thresholds: Thresholds, tmp_path: Path
) -> None:
    result = await evaluate(load_skill(broken_copy(tmp_path)), config, thresholds)
    r = result.report
    rows = {c["id"]: c for c in r["cases"]}
    assert rows["normal-002"]["passed"] is False
    assert rows["normal-002"]["failures"] == ["expected:announcement_type"]
    assert rows["normal-004"]["status"] == "no_recording"
    assert rows["normal-004"]["passed"] is False
    assert r["summary"]["passed"] == 8
    assert r["metrics"][0]["value"] < 1.0
    assert r["passed"] is False


# --- 實測模式（以 FakeProvider 代替 OpenAI） ---------------------------------------------------


async def test_live_mode_uses_provider_and_counts_cost(
    skill: Skill, config: LLMConfig, thresholds: Thresholds, tmp_path: Path
) -> None:
    provider = FakeProvider(
        recorded_outputs("test"), usage=Usage(input_tokens=1000, output_tokens=200)
    )
    result = await evaluate(
        skill, config, thresholds, mode="live", provider=provider, out_dir=tmp_path
    )
    r = result.report
    assert r["mode"] == "live" and r["model"] == config.default_model
    assert r["summary"]["cost_usd"] == pytest.approx(10 * 0.00065)
    assert r["summary"]["input_tokens"] == 10_000
    assert result.path == tmp_path / "template" / "0.1.0.json"
    assert len(provider.calls) == 10


async def test_live_model_override_changes_report_name(
    skill: Skill, config: LLMConfig, thresholds: Thresholds, tmp_path: Path
) -> None:
    other = config.model_copy(
        update={"pricing": {**config.pricing, "other-model": config.pricing[config.default_model]}}
    )
    provider = FakeProvider(recorded_outputs("test"))
    result = await evaluate(
        skill,
        other,
        thresholds,
        mode="live",
        provider=provider,
        model="other-model",
        out_dir=tmp_path,
    )
    assert result.report["model"] == "other-model"
    assert result.path == tmp_path / "template" / "0.1.0.other-model.json"
    assert provider.calls[0].model == "other-model"


async def test_max_cost_stops_the_run(
    skill: Skill, config: LLMConfig, thresholds: Thresholds
) -> None:
    # 每筆實際 US$0.00825；上限 US$0.02 → 第 3 筆前的預估就會超過
    usage = Usage(input_tokens=1000, output_tokens=4000)
    provider = FakeProvider(recorded_outputs("test"), usage=usage)
    result = await evaluate(
        skill, config, thresholds, mode="live", provider=provider, max_cost_usd=0.02
    )
    r = result.report
    assert r["incomplete"] is True
    assert r["passed"] is False
    assert r["summary"]["cost_usd"] <= 0.02
    statuses = [c["status"] for c in r["cases"]]
    assert statuses.count("succeeded") + statuses.count("abstained") == len(provider.calls)
    assert "skipped_budget" in statuses


async def test_live_failures_count_against_traceability(
    skill: Skill, config: LLMConfig, thresholds: Thresholds
) -> None:
    outputs: list[Any] = recorded_outputs("test")
    outputs[0:1] = [LLMUnavailableError("503")] * 3
    provider = FakeProvider(outputs)
    result = await evaluate(skill, config, thresholds, mode="live", provider=provider)
    s = result.report["summary"]
    assert s["failed_runs"] == 1
    assert s["traceability"] == pytest.approx(0.9)
    assert result.report["gates"]["traceability"]["passed"] is False


async def test_record_writes_recordings(
    config: LLMConfig, thresholds: Thresholds, tmp_path: Path
) -> None:
    dst = tmp_path / "_template"
    shutil.copytree(TEMPLATE, dst)
    skill = load_skill(dst)
    outputs = recorded_outputs("test")
    provider = FakeProvider(outputs, model="model-x")
    await evaluate(skill, config, thresholds, mode="live", provider=provider, record=True)
    first = cases("test")[0]["id"]
    rec = json.loads((dst / "tests" / "recorded" / f"{first}.json").read_text("utf-8"))
    assert rec == {
        "case_id": first,
        "skill_version": "0.1.0",
        "model": "model-x",
        "output": outputs[0],
    }


async def test_record_requires_live(
    skill: Skill, config: LLMConfig, thresholds: Thresholds
) -> None:
    with pytest.raises(ValueError, match="--live"):
        await evaluate(skill, config, thresholds, record=True)


# --- 摘要與命令列 ----------------------------------------------------------------------------


async def test_summary(
    skill: Skill, config: LLMConfig, thresholds: Thresholds, tmp_path: Path
) -> None:
    result = await evaluate(skill, config, thresholds)
    path = tmp_path / "P2-summary.json"
    write_summary([result.report], path)
    data = json.loads(path.read_text("utf-8"))
    assert data["all_passed"] is True
    assert data["skills"] == [
        {
            "skill": "template",
            "version": "0.1.0",
            "mode": "recorded",
            "model": "hand-written",
            "split": "test",
            "passed": True,
            "pass_rate": 1.0,
            "schema_pass_rate": 1.0,
            "traceability": 1.0,
            "metrics": {"macro_f1": 1.0},
            "cost_usd": 0.0,
        }
    ]


def test_resolve_skill_finds_template() -> None:
    assert resolve_skill("template", REPO / "skills").name == "template"
    with pytest.raises(ValueError, match="找不到"):
        resolve_skill("no-such-skill", REPO / "skills")


class IsolatedSettings(Settings):
    model_config = SettingsConfigDict(**{**Settings.model_config, "env_file": None})


def test_cli_recorded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--skill",
            "template",
            "--split",
            "test",
            "--out-dir",
            str(tmp_path / "reports"),
            "--runs-dir",
            str(tmp_path / "runs"),
        ]
    )
    assert code == 0
    assert (tmp_path / "reports" / "template" / "0.1.0.recorded.json").exists()
    assert "template" in capsys.readouterr().out


def test_cli_live_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NCU_OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("app.ai.eval.runner.get_settings", IsolatedSettings)
    code = main(["--skill", "template", "--live", "--out-dir", str(tmp_path)])
    assert code == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().out


def test_cli_requires_skill_or_all() -> None:
    with pytest.raises(SystemExit):
        main([])
