"""Skills 可攜性驗證（P7-T04）：在 Claude Code 跑產品 Skill 的 golden cases，與 Runner 比較。

在 backend/ 執行：
    uv run python ../scripts/eval/portability.py --all --split test --model sonnet --jobs 4

流程：
1. 把 `skills/` 下的產品 Skill（不含 tests/）複製到隔離的工作目錄 `.claude/skills/`，
   讓 Claude Code 以 Agent Skills 的方式自行探索與載入；
2. 每個 case 執行一次 `claude -p`，使用者訊息與 Runner 的 user 訊息相同（`<untrusted_source>`），
   但沒有 Runner 的系統前言與 structured outputs；只開放 Skill、Read、Glob 工具；
3. 把輸出當成錄製回應，交給同一套評測（`app/ai/eval/runner.py` 的錄製模式）做 schema、
   業務驗證與指標計算，再和 `eval/reports/<skill>/<version>.json`（Runner live 報告）逐筆比較。

完整輸出寫到 `eval/runs/portability/`（不進 git）；已有的 case 輸出會重複使用，可中斷後續跑。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.ai.eval.runner import (  # noqa: E402
    DEFAULT_REPORTS,
    DEFAULT_RUNS,
    evaluate_skill,
    load_thresholds,
    resolve_skill,
)
from app.ai.llm.config import load_llm_config  # noqa: E402
from app.ai.runner.loader import Skill, discover_skills  # noqa: E402
from app.ai.runner.prompt import build_messages  # noqa: E402

PROMPT = (
    "請使用 {name} skill 處理以下輸入，只輸出一個符合該 skill 的 "
    "schemas/output.schema.json 的 JSON 物件，不要任何其他文字。\n\n{user}"
)
TOOLS = "Skill,Read,Glob"
TIMEOUT_SECONDS = 600
_FENCE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.S)


def extract_json(text: str) -> tuple[dict[str, Any] | None, str]:
    """從回覆取出 JSON 物件；回傳 (物件, 方式)，方式為 strict／fenced／extracted／failed。"""
    candidates = [("strict", text.strip())]
    if m := _FENCE.match(text.strip()):
        candidates.append(("fenced", m.group(1)))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(("extracted", text[start : end + 1]))
    for how, raw in candidates:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value, how
    return None, "failed"


def _load_cases(skill: Skill, split: str) -> list[dict[str, Any]]:
    lines = (skill.root / "tests" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    return [c for c in cases if split == "all" or c.get("split") == split]


def prepare_workspace(ws: Path, skills: list[Skill]) -> None:
    """建立隔離工作目錄：只放 SKILL.md、schemas、references、scripts，不放 golden cases。"""
    target = ws / ".claude" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    for skill in skills:
        dest = target / skill.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(
            skill.root, dest, ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc")
        )


def parse_stream(lines: list[str], skill_name: str) -> dict[str, Any]:
    """整理 stream-json：實際模型、是否啟用 Skill、使用的工具、回合數、費用與最後回覆。"""
    info: dict[str, Any] = {
        "model": None,
        "skill_invoked": False,
        "tools": [],
        "num_turns": None,
        "cost_usd": None,
        "duration_ms": None,
        "result_text": None,
        "is_error": None,
    }
    for line in lines:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "init":
            info["model"] = msg.get("model")
        elif kind == "assistant":
            for block in msg.get("message", {}).get("content", []):
                if block.get("type") != "tool_use":
                    continue
                info["tools"].append(block.get("name"))
                if block.get("name") == "Skill" and block.get("input", {}).get("skill") == (
                    skill_name
                ):
                    info["skill_invoked"] = True
        elif kind == "result":
            info["num_turns"] = msg.get("num_turns")
            info["cost_usd"] = msg.get("total_cost_usd")
            info["duration_ms"] = msg.get("duration_ms")
            info["result_text"] = msg.get("result")
            info["is_error"] = msg.get("is_error")
    return info


def run_case(ws: Path, skill: Skill, case: dict[str, Any], model: str, out: Path) -> None:
    """以 `claude -p` 跑一個 case，結果寫到 `out`。"""
    user = build_messages(skill, case["input"])[1].content
    cmd = [
        "claude",
        "-p",
        PROMPT.format(name=skill.name, user=user),
        "--model",
        model,
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--tools",
        TOOLS,
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    started = time.monotonic()
    try:
        # 指令固定為 claude；case 內容只以單一參數傳入，不經 shell。
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=ws,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        info = parse_stream(proc.stdout.splitlines(), skill.name)
        info["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired:
        info = parse_stream([], skill.name)
        info["exit_code"] = "timeout"
    info["wall_ms"] = round((time.monotonic() - started) * 1000)
    output, how = extract_json(info["result_text"] or "")
    info.update(case_id=case["id"], skill_version=skill.version, output=output, parse=how)
    out.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stage_recordings(skill: Skill, case_dir: Path, score_root: Path) -> Skill:
    """把 Claude Code 的輸出放成錄製回應（無法解析時放原文，交給 Runner 判定失敗）。"""
    dest = score_root / skill.root.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(skill.root, dest, ignore=shutil.ignore_patterns("recorded", "__pycache__"))
    rec_dir = dest / "tests" / "recorded"
    rec_dir.mkdir(parents=True)
    for path in case_dir.glob("*.json"):
        info = json.loads(path.read_text(encoding="utf-8"))
        output = info["output"] if info["output"] is not None else (info["result_text"] or "")
        record = {
            "case_id": info["case_id"],
            "skill_version": info["skill_version"],
            "model": f"claude-code/{info['model']}",
            "output": output,
        }
        (rec_dir / path.name).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return resolve_skill(skill.root.name.lstrip("_"), root=score_root)


def compare(skill: Skill, report: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    """與 Runner live 報告逐筆比較，並彙整 Claude Code 端的執行情形。"""
    runner_path = DEFAULT_REPORTS / skill.name / f"{skill.version}.json"
    runner = json.loads(runner_path.read_text(encoding="utf-8")) if runner_path.is_file() else None
    runner_pass = {c["id"]: c["passed"] for c in runner["cases"]} if runner else {}
    infos = {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(case_dir.glob("*.json"))
    }
    rows = report["cases"]
    both = [r for r in rows if r["id"] in runner_pass]
    s = report["summary"]
    durations = [i["duration_ms"] for i in infos.values() if i["duration_ms"]]
    return {
        "skill": skill.name,
        "version": skill.version,
        "cases": s["cases"],
        "cc_passed": s["passed"],
        "cc_schema_pass_rate": s["schema_pass_rate"],
        "cc_report_passed": report["passed"],
        "cc_metrics": {m["name"]: m["value"] for m in report["metrics"]},
        "runner_model": runner["model"] if runner else None,
        "runner_split": runner["split"] if runner else None,
        "runner_compared": len(both),
        "runner_passed": sum(1 for r in both if runner_pass[r["id"]]),
        "agree_pass": sum(1 for r in both if r["passed"] and runner_pass[r["id"]]),
        "runner_only": [r["id"] for r in both if runner_pass[r["id"]] and not r["passed"]],
        "cc_only": [r["id"] for r in both if r["passed"] and not runner_pass[r["id"]]],
        "failures": {r["id"]: r["failures"] for r in rows if not r["passed"]},
        "skill_invoked": sum(1 for i in infos.values() if i["skill_invoked"]),
        "parse": {
            k: sum(1 for i in infos.values() if i["parse"] == k)
            for k in ("strict", "fenced", "extracted", "failed")
        },
        "models": sorted({str(i["model"]) for i in infos.values()}),
        "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
        "avg_turns": round(
            sum(i["num_turns"] or 0 for i in infos.values()) / max(len(infos), 1), 2
        ),
        "cost_usd": round(sum(i["cost_usd"] or 0 for i in infos.values()), 4),
    }


def _pct(n: int, d: int) -> str:
    return f"{n}/{d}（{n / d:.0%}）" if d else "—"


def print_table(rows: list[dict[str, Any]]) -> None:
    """印出 Markdown 比較表。"""
    print("| Skill | 版本 | Runner 通過 | Claude Code 通過 | 兩者皆過 | 只 Runner 過 | 只 CC 過 |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        print(
            f"| {r['skill']} | {r['version']} | {_pct(r['runner_passed'], r['runner_compared'])}"
            f" | {_pct(r['cc_passed'], r['cases'])} | {r['agree_pass']}"
            f" | {len(r['runner_only'])} | {len(r['cc_only'])} |"
        )


async def _main(args: argparse.Namespace) -> int:
    skills = (
        [s for s in discover_skills().values()]
        if args.all
        else [resolve_skill(n) for n in args.skill]
    )
    base = args.runs_dir / f"claude-code-{args.model}-{args.split}"
    ws = base / "workspace"
    # 工作目錄放全部產品 Skill，讓客戶端必須自行選對 Skill。
    prepare_workspace(ws, list(discover_skills().values()))
    jobs: list[tuple[Skill, dict[str, Any], Path]] = []
    for skill in skills:
        case_dir = base / "cases" / skill.name
        case_dir.mkdir(parents=True, exist_ok=True)
        for case in _load_cases(skill, args.split):
            out = case_dir / f"{case['id']}.json"
            if not out.is_file() or args.rerun:
                jobs.append((skill, case, out))
    print(f"要執行 {len(jobs)} 個 case（已完成的會重複使用）")
    done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_case, ws, s, c, args.model, o) for s, c, o in jobs]
        for f in futures:
            f.result()
            done += 1
            if done % 10 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)}", flush=True)

    config = load_llm_config()
    thresholds = load_thresholds()
    rows = []
    for skill in skills:
        case_dir = base / "cases" / skill.name
        staged = stage_recordings(skill, case_dir, base / "score")
        result = await evaluate_skill(
            staged,
            split=args.split,
            mode="recorded",
            config=config,
            thresholds=thresholds,
            out_dir=base / "reports",
            runs_dir=base / "scored",
        )
        rows.append(compare(skill, result.report, case_dir))
    (base / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print_table(rows)
    print(f"摘要 → {base / 'summary.json'}")
    return 0


def main() -> int:
    """命令列入口。"""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--skill", action="append", help="Skill 名稱，可重複")
    target.add_argument("--all", action="store_true", help="所有產品 Skill（不含模板）")
    p.add_argument("--split", choices=["dev", "test", "all"], default="test")
    p.add_argument("--model", default="sonnet", help="claude -p 的 --model")
    p.add_argument("--jobs", type=int, default=4, help="同時執行的 claude 行程數")
    p.add_argument("--rerun", action="store_true", help="忽略已有輸出，全部重跑")
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS / "portability")
    return asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
