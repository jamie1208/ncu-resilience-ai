"""評測流程（P2-T05）：以錄製回應或真實模型跑 golden cases，產生報告。

使用者 2026-10-20 決定：
- 指標與門檻集中在 `eval/thresholds.yaml`；
- 評測不寫資料庫：費用記在報告，`max_cost_usd`（預設等於每日上限）限制一次執行的總費用；
- 報告（`eval/reports/<skill>/<version>[.<recorded或模型>].json`）進 git，只放統計與每筆的判定；
  完整輸出放 `eval/runs/<skill>/`（不進 git）。

命令列：`scripts/eval/run_eval.py`
（在 backend/ 執行 `uv run python ../scripts/eval/run_eval.py --help`）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    ValidationError,
    model_validator,
)

from app.ai.eval.derived import root_cause_fields, solution_plan_fields
from app.ai.eval.metrics import (
    accuracy,
    case_outcome,
    macro_f1,
    precision_recall_f1,
    unsupported_count,
)
from app.ai.llm.base import LLMProvider
from app.ai.llm.config import LLMConfig, LLMConfigError, create_provider, load_llm_config
from app.ai.llm.fake_provider import FakeProvider
from app.ai.runner.loader import SKILLS_DIR, Skill, discover_skills, load_skill
from app.ai.runner.run import RunRecord, RunStatus, run_skill
from app.core.config import get_settings
from app.risk.engine import derived_fields

REPO = Path(__file__).resolve().parents[4]
DEFAULT_THRESHOLDS = REPO / "eval" / "thresholds.yaml"
DEFAULT_REPORTS = REPO / "eval" / "reports"
DEFAULT_RUNS = REPO / "eval" / "runs"

Mode = Literal["recorded", "live"]
Split = Literal["dev", "test", "all"]
Ratio = Annotated[float, Field(ge=0, le=1)]

NO_RECORDING = "no_recording"
SKIPPED_BUDGET = "skipped_budget"


# --- 門檻設定 ------------------------------------------------------------------------------


class ThresholdsError(ValueError):
    """門檻設定檔不存在或格式錯誤。"""


class GlobalGates(BaseModel):
    """所有 Skill 共用的門檻。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_pass_rate: Ratio
    traceability: Ratio


class MetricSpec(BaseModel):
    """一個指標：類型、欄位、正類與門檻。

    比例型指標以 `threshold`（≥）判定，`field` 是 expected 中的 gold 欄位；
    計次指標（`unsupported_count`，P2-T10）以 `max`（≤）判定，`field` 是輸出欄位。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    type: Literal["macro_f1", "f1", "precision", "recall", "accuracy", "unsupported_count"]
    field: str
    positive: Any = True
    threshold: Ratio | None = None
    max: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _one_limit(self) -> Self:
        if self.type == "unsupported_count":
            if self.max is None or self.threshold is not None:
                raise ValueError("unsupported_count 只能設定 max")
        elif self.threshold is None or self.max is not None:
            raise ValueError(f"{self.type} 只能設定 threshold")
        return self

    def passed(self, value: Any) -> bool:
        """數值是否達標；無法計算（None）一律未達標。"""
        if value is None:
            return False
        if self.max is not None:
            return bool(value <= self.max)
        return self.threshold is not None and bool(value >= self.threshold)


class SkillThresholds(BaseModel):
    """一個 Skill 的指標。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metrics: list[MetricSpec] = []


class Thresholds(BaseModel):
    """`eval/thresholds.yaml` 的內容。"""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    version: int
    global_: GlobalGates = Field(alias="global")
    skills: dict[str, SkillThresholds] = {}


def load_thresholds(path: Path = DEFAULT_THRESHOLDS) -> Thresholds:
    """讀取並驗證門檻設定。"""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Thresholds.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ThresholdsError(f"{path} 無法使用：{exc}") from exc


# --- 執行 ----------------------------------------------------------------------------------


@dataclass
class _MemoryStore:
    """評測不寫資料庫：紀錄留在記憶體，已花費作為 `max_cost_usd` 的累計。"""

    spent: float = 0.0
    records: list[RunRecord] = field(default_factory=list)

    async def spent_today_usd(self, now: datetime) -> float:
        return self.spent

    async def save(self, record: RunRecord) -> uuid.UUID:
        self.records.append(record)
        self.spent += record.cost_usd
        return uuid.uuid4()


@dataclass(frozen=True)
class EvalResult:
    """評測結果：報告內容與寫入的路徑（沒有指定輸出資料夾時為 None）。"""

    report: dict[str, Any]
    path: Path | None


def _load_cases(skill: Skill, split: Split) -> list[dict[str, Any]]:
    lines = (skill.root / "tests" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [json.loads(ln) for ln in lines if ln.strip()]
    return [c for c in cases if split == "all" or c.get("split") == split]


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _report_name(skill: Skill, mode: Mode, model: str, default_model: str) -> str:
    if mode == "recorded":
        return f"{skill.version}.recorded.json"
    if model == default_model:
        return f"{skill.version}.json"
    return f"{skill.version}.{_safe(model)}.json"


Deriver = Callable[[dict[str, Any]], dict[str, Any]]

DERIVERS: dict[str, Deriver] = {
    "risk-assessor": derived_fields,
    "root-cause-analyst": root_cause_fields,
    "solution-planner": solution_plan_fields,
}
"""由輸出計算衍生欄位的 Skill（P2-T11；S07／S08 為 P2-T12）。

欄位名稱要在 SKILL.md 的 metadata.derived_fields 宣告。
"""


def with_derived(output: dict[str, Any] | None, deriver: Deriver | None) -> dict[str, Any] | None:
    """把衍生欄位併進輸出（評測比對與指標用）；衍生欄位不覆蓋模型輸出的欄位。"""
    if output is None or deriver is None:
        return output
    return {**deriver(output), **output}


def _metric(spec: MetricSpec, rows: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> Any:
    if spec.type == "unsupported_count":
        return unsupported_count([o for _, o in rows], field=spec.field, positive=spec.positive)
    pairs = [
        (case["expected"][spec.field], output.get(spec.field) if output else "<no_output>")
        for case, output in rows
        if spec.field in (case.get("expected") or {})
    ]
    gold = [g for g, _ in pairs]
    pred = [p for _, p in pairs]
    if spec.type == "macro_f1":
        return macro_f1(gold, pred)
    if spec.type == "accuracy":
        return accuracy(gold, pred)
    prf = precision_recall_f1(gold, pred, positive=spec.positive)
    return {"f1": prf.f1, "precision": prf.precision, "recall": prf.recall}[spec.type]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _gate(value: float | None, threshold: float) -> dict[str, Any]:
    return {
        "value": value,
        "threshold": threshold,
        "passed": value is not None and value >= threshold,
    }


async def evaluate_skill(
    skill: Skill,
    *,
    split: Split,
    mode: Mode,
    config: LLMConfig,
    thresholds: Thresholds,
    provider: LLMProvider | None = None,
    model: str | None = None,
    max_cost_usd: float | None = None,
    record: bool = False,
    out_dir: Path | None = None,
    runs_dir: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> EvalResult:
    """跑一個 Skill 的 golden cases，回傳報告；指定 `out_dir` 時寫入報告檔。"""
    if record and mode != "live":
        raise ValueError("--record 只能搭配 --live（錄製真實模型的回應）")
    if mode == "live" and provider is None:
        raise ValueError("live 模式需要 provider")
    model_id = model or config.default_model
    budget = config.limits.daily_usd if max_cost_usd is None else max_cost_usd
    run_config = config.model_copy(
        update={"limits": config.limits.model_copy(update={"daily_usd": budget})}
    )
    store = _MemoryStore()
    cases = _load_cases(skill, split)
    rows: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    case_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    recorded_models: set[str] = set()
    exhausted = False

    for case in cases:
        status: str
        output: dict[str, Any] | None = None
        rec: RunRecord | None = None
        if exhausted:
            status = SKIPPED_BUDGET
        else:
            case_provider: LLMProvider | None = provider
            if mode == "recorded":
                rec_path = skill.root / "tests" / "recorded" / f"{case['id']}.json"
                if rec_path.is_file():
                    case_provider = FakeProvider.from_recorded(rec_path)
                    recorded = json.loads(rec_path.read_text(encoding="utf-8"))
                    recorded_models.add(str(recorded["model"]))
                else:
                    case_provider = None
            if case_provider is None:
                status = NO_RECORDING
            else:
                result = await run_skill(
                    skill,
                    case["input"],
                    provider=case_provider,
                    config=run_config,
                    store=store,
                    model=model_id if mode == "live" else None,
                    trace_id=f"eval-{skill.name}-{case['id']}",
                    clock=clock,
                    sleep=sleep,
                )
                rec = store.records[-1]
                output = result.output
                status = result.status.value
                if result.error_type == "cost_limit":
                    status, exhausted = SKIPPED_BUDGET, True
                if record and output is not None:
                    _write_recording(skill, case["id"], rec.model_id, output)
        scored = with_derived(output, DERIVERS.get(skill.name))
        if status != SKIPPED_BUDGET:
            rows.append((case, scored))
        outcome = case_outcome(case, scored)
        failures = list(outcome.failures)
        if status == NO_RECORDING:
            failures = [NO_RECORDING]
        expected = case.get("expected") or {}
        case_rows.append(
            {
                "id": case["id"],
                "type": case.get("type"),
                "split": case.get("split"),
                "status": status,
                "passed": outcome.passed,
                "failures": failures,
                "expected": expected,
                "predicted": {k: output.get(k) for k in expected} if output else None,
            }
        )
        raw_rows.append(
            {
                "id": case["id"],
                "status": status,
                "output": output,
                "model": rec.model_id if rec else None,
                "validation_issues": list(rec.validation_issues) if rec else [],
                "cost_usd": rec.cost_usd if rec else 0.0,
            }
        )

    ran = [r for r in case_rows if r["status"] != SKIPPED_BUDGET]
    with_output = sum(1 for _, o in rows if o is not None)
    traceable = sum(
        1
        for r, (_, o) in zip(ran, rows, strict=True)
        if o is not None or r["status"] == RunStatus.ABSTAINED.value
    )
    by_type: dict[str, dict[str, int]] = {}
    for r in case_rows:
        bucket = by_type.setdefault(str(r["type"]), {"cases": 0, "passed": 0})
        bucket["cases"] += 1
        bucket["passed"] += int(r["passed"])
    statuses = Counter(r["status"] for r in case_rows)
    records = store.records
    schema_pass_rate = _ratio(with_output, len(ran))
    traceability = _ratio(traceable, len(ran))
    summary: dict[str, Any] = {
        "cases": len(case_rows),
        "passed": sum(1 for r in case_rows if r["passed"]),
        "pass_rate": _ratio(sum(1 for r in case_rows if r["passed"]), len(case_rows)),
        "schema_pass_rate": schema_pass_rate,
        "traceability": traceability,
        "abstain_rate": _ratio(statuses[RunStatus.ABSTAINED.value], len(ran)),
        "failed_runs": statuses[RunStatus.FAILED.value],
        "no_recording": statuses[NO_RECORDING],
        "skipped_budget": statuses[SKIPPED_BUDGET],
        "by_type": by_type,
        "attempts": sum(r.attempts for r in records),
        "input_tokens": sum(r.input_tokens for r in records),
        "output_tokens": sum(r.output_tokens for r in records),
        "cost_usd": round(sum(r.cost_usd for r in records), 6),
        "latency_ms": sum(r.latency_ms for r in records),
    }
    specs = thresholds.skills.get(skill.name, SkillThresholds()).metrics
    metrics = []
    for spec in specs:
        value = _metric(spec, rows)
        metrics.append(
            {
                "name": spec.name,
                "type": spec.type,
                "field": spec.field,
                "value": value,
                "threshold": spec.threshold,
                "max": spec.max,
                "passed": spec.passed(value),
            }
        )
    gates = {
        "schema_pass_rate": _gate(schema_pass_rate, thresholds.global_.schema_pass_rate),
        "traceability": _gate(traceability, thresholds.global_.traceability),
    }
    incomplete = statuses[SKIPPED_BUDGET] > 0
    if mode == "recorded":
        provider_name = "fake"
        report_model = ",".join(sorted(recorded_models)) or "none"
    else:
        provider_name = provider.name if provider else "none"
        report_model = model_id
    report = {
        "skill": skill.name,
        "version": skill.version,
        "schema_version": skill.schema_version,
        "mode": mode,
        "provider": provider_name,
        "model": report_model,
        "split": split,
        "generated_at": clock().isoformat(),
        "max_cost_usd": budget,
        "summary": summary,
        "metrics": metrics,
        "gates": gates,
        "incomplete": incomplete,
        "passed": bool(ran)
        and not incomplete
        and all(m["passed"] for m in metrics)
        and all(g["passed"] for g in gates.values()),
        "cases": case_rows,
    }

    path: Path | None = None
    stamp = clock().strftime("%Y%m%dT%H%M%SZ")
    if out_dir is not None:
        path = out_dir / skill.name / _report_name(skill, mode, model_id, config.default_model)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if runs_dir is not None:
        raw = runs_dir / skill.name / f"{stamp}-{mode}-{_safe(report_model)}.jsonl"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in raw_rows), encoding="utf-8"
        )
    return EvalResult(report, path)


def _write_recording(skill: Skill, case_id: str, model: str, output: dict[str, Any]) -> None:
    data = {"case_id": case_id, "skill_version": skill.version, "model": model, "output": output}
    path = skill.root / "tests" / "recorded" / f"{case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_summary(reports: Sequence[dict[str, Any]], path: Path) -> None:
    """把多份報告整理成摘要（例如 `eval/reports/P2-summary.json`，閘門 G2-1）。"""
    skills = [
        {
            "skill": r["skill"],
            "version": r["version"],
            "mode": r["mode"],
            "model": r["model"],
            "split": r["split"],
            "passed": r["passed"],
            "pass_rate": r["summary"]["pass_rate"],
            "schema_pass_rate": r["summary"]["schema_pass_rate"],
            "traceability": r["summary"]["traceability"],
            "metrics": {m["name"]: m["value"] for m in r["metrics"]},
            "cost_usd": r["summary"]["cost_usd"],
        }
        for r in reports
    ]
    data = {"all_passed": bool(skills) and all(s["passed"] for s in skills), "skills": skills}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_skill(name: str, root: Path = SKILLS_DIR) -> Skill:
    """以名稱找 Skill：先找 `<name>/`，再找模板 `_<name>/`。"""
    for candidate in (root / name, root / f"_{name}"):
        if (candidate / "SKILL.md").is_file():
            return load_skill(candidate)
    raise ValueError(f"找不到 Skill「{name}」（{root}）")


# --- 命令列 --------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """`run_eval.py` 的參數。"""
    p = argparse.ArgumentParser(description="跑產品 Skill 的 golden cases 並產生評測報告")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--skill", action="append", help="Skill 名稱，可重複（模板為 template）")
    target.add_argument("--all", action="store_true", help="所有產品 Skill（不含模板）")
    p.add_argument("--split", choices=["dev", "test", "all"], default="test")
    p.add_argument("--live", action="store_true", help="呼叫真實模型（預設用錄製回應）")
    p.add_argument("--model", help="覆寫 config/llm.yaml 的 default_model（須有價格）")
    p.add_argument("--max-cost", type=float, help="這次執行的費用上限（USD，預設為每日上限）")
    p.add_argument("--record", action="store_true", help="把真實回應寫回 tests/recorded/")
    p.add_argument(
        "--summary", type=Path, help="另外寫一份摘要（例如 eval/reports/P2-summary.json）"
    )
    p.add_argument("--out-dir", type=Path, default=DEFAULT_REPORTS)
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    return p


def _fmt(value: Any) -> str:
    return "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


async def _main(args: argparse.Namespace) -> int:
    config = load_llm_config()
    thresholds = load_thresholds()
    try:
        skills = (
            list(discover_skills().values()) if args.all else [resolve_skill(n) for n in args.skill]
        )
    except ValueError as exc:
        print(exc)
        return 2
    if not skills:
        print("沒有找到任何產品 Skill")
        return 2
    provider: LLMProvider | None = None
    if args.live:
        try:
            provider = create_provider(config, get_settings())
        except LLMConfigError as exc:
            print(exc)
            return 2
    reports = []
    for skill in skills:
        result = await evaluate_skill(
            skill,
            split=args.split,
            mode="live" if args.live else "recorded",
            config=config,
            thresholds=thresholds,
            provider=provider,
            model=args.model,
            max_cost_usd=args.max_cost,
            record=args.record,
            out_dir=args.out_dir,
            runs_dir=args.runs_dir,
        )
        r = result.report
        s = r["summary"]
        metrics = "、".join(f"{m['name']} {_fmt(m['value'])}" for m in r["metrics"]) or "—"
        print(
            f"{'✓' if r['passed'] else '✗'} {r['skill']} {r['version']}"
            f"（{r['mode']}，{r['model']}，"
            f"{r['split']}）：通過 {s['passed']}/{s['cases']}，{metrics}，"
            f"schema {_fmt(s['schema_pass_rate'])}，可追溯 {_fmt(s['traceability'])}，"
            f"US${s['cost_usd']:.4f} → {result.path}"
        )
        reports.append(r)
    if args.summary:
        write_summary(reports, args.summary)
        print(f"摘要 → {args.summary}")
    return 0 if all(r["passed"] for r in reports) else 1


def main(argv: Sequence[str] | None = None) -> int:
    """命令列入口；回傳結束碼（0 全部通過、1 有未通過、2 設定或參數錯誤）。"""
    args = build_parser().parse_args(argv)
    return asyncio.run(_main(args))
