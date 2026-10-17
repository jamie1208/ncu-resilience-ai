"""check_source_inventory.py 的測試。

執行：python3 -m pytest scripts/governance/tests -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "check_source_inventory.py"
REPO_INVENTORY = Path(__file__).resolve().parents[3] / "docs" / "governance" / "source-inventory.md"

HEADER = "| 代碼 | 來源 | AI 建議 | 核准狀態 |\n| --- | --- | --- | --- |\n"


def write(tmp_path: Path, rows: list[tuple[str, str]], tail: str = "") -> Path:
    body = "".join(f"| {code} | 名稱 | 核准 | {status} |\n" for code, status in rows)
    p = tmp_path / "inventory.md"
    p.write_text("# 盤點\n\n" + HEADER + body + tail, encoding="utf-8")
    return p


def run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(path), *args],
                          capture_output=True, text=True, timeout=30)


def test_three_approved_passes(tmp_path: Path) -> None:
    res = run(write(tmp_path, [("a", "核准"), ("b", "核准"), ("c", "核准"), ("d", "待決定")]))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "核准 3" in res.stdout


def test_fewer_than_three_fails(tmp_path: Path) -> None:
    res = run(write(tmp_path, [("a", "核准"), ("b", "核准"), ("c", "暫緩")]))
    assert res.returncode == 1
    assert "核准 2" in res.stdout


def test_rejected_is_not_counted_as_approved(tmp_path: Path) -> None:
    res = run(write(tmp_path, [("a", "不核准"), ("b", "不核准"), ("c", "核准"), ("d", "核准")]))
    assert res.returncode == 1
    assert "核准 2" in res.stdout


def test_ai_suggestion_column_is_ignored(tmp_path: Path) -> None:
    # 每列的「AI 建議」欄都是「核准」，但核准狀態全是待決定
    res = run(write(tmp_path, [("a", "待決定"), ("b", "待決定"), ("c", "待決定")]))
    assert res.returncode == 1
    assert "核准 0" in res.stdout


def test_later_tables_are_ignored(tmp_path: Path) -> None:
    tail = "\n## 核准紀錄\n\n| 日期 | 代碼 | 決定 |\n| --- | --- | --- |\n| 2026-10-17 | x | 核准 |\n"
    res = run(write(tmp_path, [("a", "核准")], tail))
    assert res.returncode == 1
    assert "來源 1" in res.stdout


def test_min_option(tmp_path: Path) -> None:
    res = run(write(tmp_path, [("a", "核准")]), "--min", "1")
    assert res.returncode == 0


def test_missing_status_column_is_an_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.md"
    p.write_text("| 代碼 | 來源 |\n| --- | --- |\n| a | b |\n", encoding="utf-8")
    res = run(p)
    assert res.returncode == 2


def test_repo_inventory_is_parseable() -> None:
    res = run(REPO_INVENTORY, "--min", "0")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "來源 12" in res.stdout
