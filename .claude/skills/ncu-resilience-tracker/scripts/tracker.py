#!/usr/bin/env python3
"""NCU Resilience AI 進度追蹤工具。

子指令：
  init            建立 docs/progress/ 與 state.yaml
  sync            phases.yaml 新增任務／閘門後，補進 state.yaml
  status          階段進度、目前階段、下一個可做任務
  check           比對 repo 與 state.yaml 的一致性；--run-gates 執行閘門指令
  skills          盤點 skills/ 下產品 Skills 的成熟度
  review          status + check + skills，寫入 docs/progress/reviews/
  set ID STATUS   更新任務（pending|in_progress|done|blocked|skipped）
                  或閘門（pending|pass|fail）狀態
  record ID       產生任務或階段（P0…P5）的驗收紀錄草稿

只依賴 Python 3.9+ 與 PyYAML（pip install pyyaml）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("需要 PyYAML：pip install pyyaml（或 uv pip install pyyaml）")

SKILL_DIR = Path(__file__).resolve().parent.parent
TASK_STATUSES = ["pending", "in_progress", "done", "blocked", "skipped"]
GATE_STATUSES = ["pending", "pass", "fail"]
DONE = {"done", "skipped"}
ICON = {"pending": "⬜", "in_progress": "🔄", "done": "✅", "blocked": "⛔",
        "skipped": "➖", "pass": "✅", "fail": "❌"}


# ---------------------------------------------------------------- 基礎工具
def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    cur = Path.cwd().resolve()
    for p in [cur, *cur.parents]:
        if (p / "docs" / "progress" / "state.yaml").exists() or (p / ".git").exists():
            return p
    return cur


def load_plan(root: Path) -> dict:
    """repo 內 docs/progress/phases.yaml 優先，否則用 skill 內建定義。"""
    override = root / "docs" / "progress" / "phases.yaml"
    path = override if override.exists() else SKILL_DIR / "references" / "phases.yaml"
    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    plan["_path"] = str(path)
    return plan


def state_path(root: Path) -> Path:
    return root / "docs" / "progress" / "state.yaml"


def load_state(root: Path) -> dict:
    p = state_path(root)
    if not p.exists():
        sys.exit(f"找不到 {p}。請先執行：tracker.py init")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def save_state(root: Path, state: dict) -> None:
    state["updated_at"] = now()
    state_path(root).write_text(
        yaml.safe_dump(state, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8")


def all_tasks(plan: dict):
    for ph in plan["phases"]:
        for t in ph.get("tasks", []):
            yield ph, t


def all_gates(plan: dict):
    for ph in plan["phases"]:
        for g in ph.get("gates", []):
            yield ph, g


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, timeout=10).stdout.strip() or "N/A"
    except Exception:
        return "N/A"


# ---------------------------------------------------------------- 狀態計算
def task_status(state: dict, tid: str) -> str:
    return state.get("tasks", {}).get(tid, {}).get("status", "pending")


def gate_status(state: dict, gid: str) -> str:
    return state.get("gates", {}).get(gid, {}).get("status", "pending")


def phase_summary(plan: dict, state: dict) -> list[dict]:
    out = []
    for ph in plan["phases"]:
        tasks = ph.get("tasks", [])
        gates = ph.get("gates", [])
        done = sum(task_status(state, t["id"]) in DONE for t in tasks)
        passed = sum(gate_status(state, g["id"]) == "pass" for g in gates)
        out.append({"id": ph["id"], "name": ph["name"], "tasks_done": done,
                    "tasks_total": len(tasks), "gates_passed": passed,
                    "gates_total": len(gates),
                    "complete": done == len(tasks) and passed == len(gates)})
    return out


def current_phase(summary: list[dict]) -> str:
    for s in summary:
        if not s["complete"]:
            return s["id"]
    return "DONE"


def next_tasks(plan: dict, state: dict, limit: int = 5) -> list[dict]:
    cands = []
    for ph, t in all_tasks(plan):
        st = task_status(state, t["id"])
        if st in DONE or st == "blocked":
            continue
        unmet = [d for d in t.get("deps", []) if task_status(state, d) not in DONE]
        if not unmet:
            cands.append({"id": t["id"], "title": t["title"], "phase": ph["id"],
                          "status": st, "human": bool(t.get("human"))})
    # 進行中的優先，其次依計畫順序
    cands.sort(key=lambda c: (c["status"] != "in_progress",))
    return cands[:limit]


def blocked_by_gate(plan: dict, state: dict) -> list[str]:
    """任務已在後面階段開工，但前一階段閘門未通過 → 警告。"""
    warns, prev_ok = [], True
    for ph in plan["phases"]:
        started = [t["id"] for t in ph.get("tasks", [])
                   if task_status(state, t["id"]) in {"in_progress", "done"}]
        if started and not prev_ok:
            warns.append(f"{ph['id']} 已有任務開工（{', '.join(started[:3])}…），但前一階段閘門尚未全部通過")
        gates = ph.get("gates", [])
        prev_ok = prev_ok and all(gate_status(state, g["id"]) == "pass" for g in gates)
    return warns


def consistency(plan: dict, state: dict, root: Path, phase: str | None) -> dict:
    marked_missing, unmarked_present, partial = [], [], []
    for ph, t in all_tasks(plan):
        if phase and ph["id"] != phase:
            continue
        paths = t.get("paths", [])
        if not paths:
            continue
        exist = [p for p in paths if (root / p).exists()]
        missing = [p for p in paths if p not in exist]
        st = task_status(state, t["id"])
        if st == "done" and missing:
            marked_missing.append({"id": t["id"], "missing": missing})
        elif st == "pending" and len(exist) == len(paths):
            unmarked_present.append({"id": t["id"]})
        elif st in {"pending", "in_progress"} and exist and missing:
            partial.append({"id": t["id"], "exist": len(exist), "total": len(paths)})
    return {"done_but_missing": marked_missing,
            "present_but_pending": unmarked_present, "partial": partial}


def run_gates(plan: dict, state: dict, root: Path, phase: str | None,
              timeout: int) -> list[dict]:
    results = []
    for ph, g in all_gates(plan):
        if phase and ph["id"] != phase:
            continue
        rec = state.setdefault("gates", {}).setdefault(g["id"], {"status": "pending"})
        if g.get("manual") or not g.get("command"):
            results.append({"id": g["id"], "title": g["title"], "result": "manual",
                            "status": rec.get("status", "pending")})
            continue
        try:
            proc = subprocess.run(g["command"], shell=True, cwd=root, text=True,
                                  capture_output=True, timeout=timeout)
            ok = proc.returncode == 0
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
        except subprocess.TimeoutExpired:
            ok, tail = False, [f"timeout after {timeout}s"]
        rec.update({"status": "pass" if ok else "fail", "last_run": now(),
                    "last_output": "\n".join(tail)})
        results.append({"id": g["id"], "title": g["title"],
                        "result": "pass" if ok else "fail", "output": tail})
    return results


# ---------------------------------------------------------------- 產品 Skills
FRONT = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def scan_skills(plan: dict, root: Path) -> list[dict]:
    expected = {}
    for ph, t in all_tasks(plan):
        for name in str(t.get("skill", "")).split(","):
            if name.strip():
                expected[name.strip()] = t["id"]
    rows = []
    for name, tid in expected.items():
        d = root / "skills" / name
        row = {"skill": name, "task": tid, "exists": d.exists(), "version": None,
               "frontmatter_ok": False, "cases": 0, "adversarial": 0,
               "output_schema": False, "eval_reports": 0, "issues": []}
        if d.exists():
            md = d / "SKILL.md"
            if md.exists():
                m = FRONT.match(md.read_text(encoding="utf-8"))
                if m:
                    try:
                        fm = yaml.safe_load(m.group(1)) or {}
                    except yaml.YAMLError:
                        fm = {}
                    row["frontmatter_ok"] = fm.get("name") == name and bool(fm.get("description"))
                    row["version"] = (fm.get("metadata") or {}).get("version")
                    if fm.get("name") != name:
                        row["issues"].append("frontmatter name 與資料夾名稱不一致")
                else:
                    row["issues"].append("SKILL.md 缺 YAML frontmatter")
            else:
                row["issues"].append("缺 SKILL.md")
            row["output_schema"] = (d / "schemas" / "output.schema.json").exists()
            cases = d / "tests" / "cases.jsonl"
            if cases.exists():
                lines = [ln for ln in cases.read_text(encoding="utf-8").splitlines() if ln.strip()]
                row["cases"] = len(lines)
                row["adversarial"] = sum('"adversarial"' in ln for ln in lines)
            rep = root / "eval" / "reports" / name
            row["eval_reports"] = len(list(rep.glob("*.json"))) if rep.exists() else 0
            if row["cases"] < 20:
                row["issues"].append(f"golden cases {row['cases']} < 20")
            if row["cases"] and row["adversarial"] == 0:
                row["issues"].append("沒有標記 adversarial 的案例")
            if not row["output_schema"]:
                row["issues"].append("缺 schemas/output.schema.json")
            if row["eval_reports"] == 0:
                row["issues"].append("尚無評測報告")
        rows.append(row)
    return rows


def skill_maturity(r: dict) -> str:
    if not r["exists"]:
        return "未建立"
    if r["issues"]:
        return "開發中"
    return "已評測" if r["version"] else "待凍結版本"


# ---------------------------------------------------------------- 輸出
def render_status(plan, state, root) -> str:
    summ = phase_summary(plan, state)
    cur = current_phase(summ)
    total = sum(s["tasks_total"] for s in summ)
    done = sum(s["tasks_done"] for s in summ)
    lines = [f"# NCU Resilience AI 進度（{dt.date.today()}）", "",
             f"目前階段：**{cur}**　整體任務：{done}/{total}　"
             f"閘門：{sum(s['gates_passed'] for s in summ)}/{sum(s['gates_total'] for s in summ)}",
             "", "| 階段 | 名稱 | 任務 | 閘門 | 狀態 |", "| --- | --- | --- | --- | --- |"]
    for s in summ:
        mark = "✅ 完成" if s["complete"] else ("▶ 進行中" if s["id"] == cur else "")
        lines.append(f"| {s['id']} | {s['name']} | {s['tasks_done']}/{s['tasks_total']} | "
                     f"{s['gates_passed']}/{s['gates_total']} | {mark} |")
    ph = next((p for p in plan["phases"] if p["id"] == cur), None)
    if ph:
        lines += ["", f"## {cur} {ph['name']} 明細", f"目標：{ph.get('goal', '')}", ""]
        for t in ph.get("tasks", []):
            st = task_status(state, t["id"])
            note = state.get("tasks", {}).get(t["id"], {}).get("note") or ""
            human = "（需你決定）" if t.get("human") else ""
            lines.append(f"- {ICON[st]} {t['id']} {t['title']}{human}"
                         + (f" — {note}" if note else ""))
        lines.append("")
        for g in ph.get("gates", []):
            st = gate_status(state, g["id"])
            kind = "人工" if g.get("manual") else "自動"
            lines.append(f"- {ICON.get(st, '⬜')} {g['id']} [{kind}] {g['title']}")
    nxt = next_tasks(plan, state)
    lines += ["", "## 下一個可做的任務"]
    if nxt:
        for n in nxt:
            flag = "（需你決定）" if n["human"] else ""
            far = "（超前目前階段）" if n["phase"] != cur and cur != "DONE" else ""
            lines.append(f"- {ICON[n['status']]} {n['id']} {n['title']}{flag}{far}")
    else:
        lines.append("- 無：所有剩餘任務都被前置任務或 blocked 擋住")
    warns = blocked_by_gate(plan, state)
    if warns:
        lines += ["", "## 警告"] + [f"- {w}" for w in warns]
    return "\n".join(lines)


def render_check(con: dict, gate_results: list[dict] | None) -> str:
    lines = ["## 一致性檢查"]
    if con["done_but_missing"]:
        lines.append("- 標記完成但缺檔案：")
        lines += [f"    - {x['id']}：{', '.join(x['missing'])}" for x in con["done_but_missing"]]
    if con["present_but_pending"]:
        lines.append("- 檔案已齊但仍標記 pending（確認後用 set 更新）：" +
                     ", ".join(x["id"] for x in con["present_but_pending"]))
    if con["partial"]:
        lines.append("- 部分完成：" + ", ".join(f"{x['id']}({x['exist']}/{x['total']})"
                                           for x in con["partial"]))
    if len(lines) == 1:
        lines.append("- 無不一致")
    if gate_results is not None:
        lines += ["", "## 閘門執行結果"]
        for r in gate_results:
            if r["result"] == "manual":
                lines.append(f"- 👤 {r['id']} {r['title']}（人工確認，目前：{r['status']}）")
            else:
                lines.append(f"- {ICON[r['result']]} {r['id']} {r['title']}")
                if r["result"] == "fail" and r.get("output"):
                    lines += [f"    > {ln}" for ln in r["output"]]
    return "\n".join(lines)


def render_skills(rows: list[dict]) -> str:
    lines = ["## 產品 Skills 成熟度", "",
             "| Skill | 任務 | 成熟度 | 版本 | cases（對抗） | schema | 評測報告 | 問題 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['skill']} | {r['task']} | {skill_maturity(r)} | {r['version'] or '-'} | "
                     f"{r['cases']}（{r['adversarial']}） | {'✓' if r['output_schema'] else '✗'} | "
                     f"{r['eval_reports']} | {'；'.join(r['issues']) or '-'} |")
    return "\n".join(lines)


# ---------------------------------------------------------------- 子指令
def cmd_init(args, root):
    prog = root / "docs" / "progress"
    for sub in ["records", "reviews"]:
        (prog / sub).mkdir(parents=True, exist_ok=True)
    if state_path(root).exists():
        print(f"{state_path(root)} 已存在，改執行 sync。")
        return cmd_sync(args, root)
    plan = load_plan(root)
    try:
        plan_ref = str(Path(plan["_path"]).resolve().relative_to(root))
    except ValueError:
        plan_ref = "skill:references/phases.yaml"
    state = {"project": "NCU Resilience AI", "plan": plan_ref, "created_at": now(),
             "tasks": {t["id"]: {"status": "pending"} for _, t in all_tasks(plan)},
             "gates": {g["id"]: {"status": "pending"} for _, g in all_gates(plan)},
             "history": []}
    save_state(root, state)
    print(f"已建立 {state_path(root)}（{len(state['tasks'])} 任務、{len(state['gates'])} 閘門）")


def cmd_sync(args, root):
    plan, state = load_plan(root), load_state(root)
    added = []
    for _, t in all_tasks(plan):
        if t["id"] not in state.setdefault("tasks", {}):
            state["tasks"][t["id"]] = {"status": "pending"}
            added.append(t["id"])
    for _, g in all_gates(plan):
        if g["id"] not in state.setdefault("gates", {}):
            state["gates"][g["id"]] = {"status": "pending"}
            added.append(g["id"])
    known = {t["id"] for _, t in all_tasks(plan)} | {g["id"] for _, g in all_gates(plan)}
    orphans = [k for k in [*state["tasks"], *state["gates"]] if k not in known]
    save_state(root, state)
    print(f"新增：{', '.join(added) or '無'}")
    if orphans:
        print(f"注意：state.yaml 有計畫中已不存在的 ID（保留未刪）：{', '.join(orphans)}")


def cmd_status(args, root):
    plan, state = load_plan(root), load_state(root)
    if args.json:
        summ = phase_summary(plan, state)
        print(json.dumps({"current_phase": current_phase(summ), "phases": summ,
                          "next": next_tasks(plan, state),
                          "warnings": blocked_by_gate(plan, state)},
                         ensure_ascii=False, indent=2))
    else:
        print(render_status(plan, state, root))


def cmd_check(args, root):
    plan, state = load_plan(root), load_state(root)
    con = consistency(plan, state, root, args.phase)
    gates = None
    if args.run_gates:
        phase = args.phase or current_phase(phase_summary(plan, state))
        gates = run_gates(plan, state, root, None if phase == "DONE" else phase, args.timeout)
        save_state(root, state)
    if args.json:
        print(json.dumps({"consistency": con, "gates": gates}, ensure_ascii=False, indent=2))
    else:
        print(render_check(con, gates))


def cmd_skills(args, root):
    rows = scan_skills(load_plan(root), root)
    print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json else render_skills(rows))


def cmd_review(args, root):
    plan, state = load_plan(root), load_state(root)
    gates = None
    if args.run_gates:
        phase = current_phase(phase_summary(plan, state))
        gates = run_gates(plan, state, root, None if phase == "DONE" else phase, args.timeout)
        save_state(root, state)
    body = "\n\n".join([
        render_status(plan, state, root),
        render_check(consistency(plan, state, root, None), gates),
        render_skills(scan_skills(plan, root)),
        "## Git\n" + f"- branch：{git(root, 'rev-parse', '--abbrev-ref', 'HEAD')}\n"
        f"- commit：{git(root, 'rev-parse', '--short', 'HEAD')}\n"
        f"- 未提交變更：{len([l for l in git(root, 'status', '--porcelain').splitlines() if l and l != 'N/A'])} 個檔案",
        "## 風險與偏離規格\n（由 AI 依 references/review-checklist.md 填寫）",
        "## 下一步\n（由 AI 填寫：建議任務與理由、需要你決定的事項）",
    ])
    out = root / "docs" / "progress" / "reviews" / f"review-{dt.datetime.now():%Y%m%d-%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body + "\n", encoding="utf-8")
    print(body)
    print(f"\n→ 已寫入 {out.relative_to(root)}")


def cmd_set(args, root):
    plan, state = load_plan(root), load_state(root)
    tids = {t["id"] for _, t in all_tasks(plan)}
    gids = {g["id"] for _, g in all_gates(plan)}
    key = "tasks" if args.id in tids else "gates" if args.id in gids else None
    if not key:
        sys.exit(f"未知 ID：{args.id}")
    allowed = TASK_STATUSES if key == "tasks" else GATE_STATUSES
    if args.status not in allowed:
        sys.exit(f"{args.id} 的狀態只能是：{', '.join(allowed)}")
    if key == "tasks" and args.status == "done" and not args.force:
        t = next(t for _, t in all_tasks(plan) if t["id"] == args.id)
        unmet = [d for d in t.get("deps", []) if task_status(state, d) not in DONE]
        missing = [p for p in t.get("paths", []) if not (root / p).exists()]
        if unmet or missing:
            sys.exit("拒絕標記 done：" + (f"前置未完成 {unmet} " if unmet else "")
                     + (f"缺檔案 {missing}" if missing else "") + "（確定要標記請加 --force）")
    if key == "tasks":
        item = next(t for _, t in all_tasks(plan) if t["id"] == args.id)
        needs_user = bool(item.get("human")) and args.status == "done"
        kind = "需你決定的任務"
    else:
        item = next(g for _, g in all_gates(plan) if g["id"] == args.id)
        needs_user = bool(item.get("manual")) and args.status == "pass"
        kind = "人工閘門"
    if needs_user and not args.confirmed_by_user:
        sys.exit(f"拒絕標記 {args.status}：{args.id} 是{kind}，必須由使用者本人確認。"
                 "取得使用者明確同意後加 --confirmed-by-user（--force 不適用）")
    rec = state.setdefault(key, {}).setdefault(args.id, {})
    old = rec.get("status", "pending")
    rec.update({"status": args.status, "updated_at": now()})
    if args.note is not None:
        rec["note"] = args.note
    if args.evidence:
        rec["evidence"] = args.evidence
    entry = {"at": now(), "id": args.id, "from": old, "to": args.status, "note": args.note or ""}
    if needs_user:
        entry["confirmed_by_user"] = True
    state.setdefault("history", []).append(entry)
    save_state(root, state)
    print(f"{args.id}: {old} → {args.status}")


def cmd_record(args, root):
    plan, state = load_plan(root), load_state(root)
    tpl = (SKILL_DIR / "templates" / "acceptance-record.md").read_text(encoding="utf-8")
    phase = next((p for p in plan["phases"] if p["id"] == args.id), None)
    if phase:
        name = phase["name"]
        items = "\n".join(f"- {ICON[task_status(state, t['id'])]} {t['id']} {t['title']}"
                          for t in phase.get("tasks", []))
        tests = "\n".join(f"| {g['id']} {g.get('command') or '（人工）'} | "
                          f"{gate_status(state, g['id'])} | {g['title']} |"
                          for g in phase.get("gates", []))
        ph_id = phase["id"]
    else:
        found = next(((p, t) for p, t in all_tasks(plan) if t["id"] == args.id), None)
        if not found:
            sys.exit(f"未知 ID：{args.id}")
        p, t = found
        name, ph_id = t["title"], p["id"]
        items = "\n".join(f"- {'✓' if (root / x).exists() else '✗'} `{x}`" for x in t.get("paths", []))
        tests = "| （填入本任務執行的測試指令） |  |  |"
    text = (tpl.replace("{ID}", args.id).replace("{NAME}", name).replace("{DATE}", str(dt.date.today()))
            .replace("{PHASE}", ph_id).replace("{COMMIT}", git(root, "rev-parse", "--short", "HEAD"))
            .replace("{BRANCH}", git(root, "rev-parse", "--abbrev-ref", "HEAD"))
            .replace("{ITEMS}", items).replace("{TESTS}", tests))
    out = root / "docs" / "progress" / "records" / f"{args.id}-{dt.date.today():%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not args.overwrite:
        sys.exit(f"{out.relative_to(root)} 已存在（加 --overwrite 覆寫）")
    out.write_text(text, encoding="utf-8")
    print(f"已產生 {out.relative_to(root)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="NCU Resilience AI 進度追蹤")
    ap.add_argument("--root", help="repo 根目錄（預設自動偵測）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("sync")
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("--phase"); c.add_argument("--run-gates", action="store_true")
    c.add_argument("--timeout", type=int, default=900); c.add_argument("--json", action="store_true")
    k = sub.add_parser("skills"); k.add_argument("--json", action="store_true")
    r = sub.add_parser("review")
    r.add_argument("--run-gates", action="store_true"); r.add_argument("--timeout", type=int, default=900)
    st = sub.add_parser("set")
    st.add_argument("id"); st.add_argument("status")
    st.add_argument("--note"); st.add_argument("--evidence")
    st.add_argument("--force", action="store_true")
    st.add_argument("--confirmed-by-user", action="store_true",
                    help="人工閘門標記 pass、human 任務標記 done 時必填，僅在使用者明確同意後使用")
    rc = sub.add_parser("record"); rc.add_argument("id"); rc.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    root = find_root(args.root)
    {"init": cmd_init, "sync": cmd_sync, "status": cmd_status, "check": cmd_check,
     "skills": cmd_skills, "review": cmd_review, "set": cmd_set, "record": cmd_record}[args.cmd](args, root)


if __name__ == "__main__":
    main()
