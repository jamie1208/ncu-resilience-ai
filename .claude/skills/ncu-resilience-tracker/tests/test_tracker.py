"""tracker.py 的行為測試：以暫存 repo 執行 CLI，不觸碰真實的 docs/progress/。

執行：python3 -m pytest .claude/skills/ncu-resilience-tracker/tests -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

TRACKER = Path(__file__).resolve().parent.parent / "scripts" / "tracker.py"

# 取自 skill 內建 references/phases.yaml
MANUAL_GATE = "G0-3"
AUTO_GATE = "G0-1"
HUMAN_TASK = "P0-T09"  # human: true，無前置；paths 為 docs/governance/source-inventory.md


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(TRACKER), "--root", str(root), *args],
                          capture_output=True, text=True, timeout=30)


def state_file(root: Path) -> Path:
    return root / "docs" / "progress" / "state.yaml"


def load_state(root: Path) -> dict:
    return yaml.safe_load(state_file(root).read_text(encoding="utf-8"))


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    res = run(tmp_path, "init")
    assert res.returncode == 0, res.stderr
    return tmp_path


def test_manual_gate_pass_without_confirmation_is_rejected(repo: Path) -> None:
    before = state_file(repo).read_bytes()
    res = run(repo, "set", MANUAL_GATE, "pass")
    assert res.returncode != 0
    assert "--confirmed-by-user" in res.stderr
    assert state_file(repo).read_bytes() == before


def test_force_does_not_bypass_manual_gate_guard(repo: Path) -> None:
    before = state_file(repo).read_bytes()
    res = run(repo, "set", MANUAL_GATE, "pass", "--force")
    assert res.returncode != 0
    assert state_file(repo).read_bytes() == before


def test_manual_gate_pass_with_confirmation_is_recorded(repo: Path) -> None:
    res = run(repo, "set", MANUAL_GATE, "pass", "--confirmed-by-user", "--note", "使用者確認 CI 全綠")
    assert res.returncode == 0, res.stderr
    state = load_state(repo)
    assert state["gates"][MANUAL_GATE]["status"] == "pass"
    last = state["history"][-1]
    assert last["id"] == MANUAL_GATE
    assert last["to"] == "pass"
    assert last["confirmed_by_user"] is True


@pytest.mark.parametrize("status", ["fail", "pending"])
def test_manual_gate_non_pass_statuses_need_no_confirmation(repo: Path, status: str) -> None:
    res = run(repo, "set", MANUAL_GATE, status)
    assert res.returncode == 0, res.stderr
    assert load_state(repo)["gates"][MANUAL_GATE]["status"] == status


def test_auto_gate_pass_needs_no_confirmation(repo: Path) -> None:
    res = run(repo, "set", AUTO_GATE, "pass")
    assert res.returncode == 0, res.stderr
    state = load_state(repo)
    assert state["gates"][AUTO_GATE]["status"] == "pass"
    assert "confirmed_by_user" not in state["history"][-1]


def test_task_done_with_unmet_deps_is_still_rejected(repo: Path) -> None:
    before = state_file(repo).read_bytes()
    res = run(repo, "set", "P0-T05", "done")
    assert res.returncode != 0
    assert "前置未完成" in res.stderr
    assert state_file(repo).read_bytes() == before


@pytest.fixture()
def human_task_ready(repo: Path) -> Path:
    """建立 human 任務的應存在檔案，讓測試只檢驗人工確認這一關。"""
    inv = repo / "docs" / "governance" / "source-inventory.md"
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text("# 來源盤點\n", encoding="utf-8")
    return repo


def test_human_task_done_without_confirmation_is_rejected(human_task_ready: Path) -> None:
    before = state_file(human_task_ready).read_bytes()
    res = run(human_task_ready, "set", HUMAN_TASK, "done")
    assert res.returncode != 0
    assert "--confirmed-by-user" in res.stderr
    assert state_file(human_task_ready).read_bytes() == before


def test_force_does_not_bypass_human_task_guard(human_task_ready: Path) -> None:
    before = state_file(human_task_ready).read_bytes()
    res = run(human_task_ready, "set", HUMAN_TASK, "done", "--force")
    assert res.returncode != 0
    assert state_file(human_task_ready).read_bytes() == before


def test_human_task_done_with_confirmation_is_recorded(human_task_ready: Path) -> None:
    res = run(human_task_ready, "set", HUMAN_TASK, "done", "--confirmed-by-user")
    assert res.returncode == 0, res.stderr
    state = load_state(human_task_ready)
    assert state["tasks"][HUMAN_TASK]["status"] == "done"
    assert state["history"][-1]["confirmed_by_user"] is True


def test_human_task_confirmation_does_not_skip_file_check(repo: Path) -> None:
    before = state_file(repo).read_bytes()
    res = run(repo, "set", HUMAN_TASK, "done", "--confirmed-by-user")
    assert res.returncode != 0
    assert "缺檔案" in res.stderr
    assert state_file(repo).read_bytes() == before


def test_human_task_in_progress_needs_no_confirmation(repo: Path) -> None:
    res = run(repo, "set", HUMAN_TASK, "in_progress")
    assert res.returncode == 0, res.stderr
    assert load_state(repo)["tasks"][HUMAN_TASK]["status"] == "in_progress"
