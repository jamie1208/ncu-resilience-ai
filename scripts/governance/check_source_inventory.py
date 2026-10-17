#!/usr/bin/env python3
"""檢查來源授權盤點的核准數（P0-T09 驗證、閘門 G0-4）。

讀取盤點文件中第一個含「核准狀態」欄的 Markdown 表格，計算該欄值「恰為」
「核准」的列數（「不核准」不算，也不看「AI 建議」欄）。

用法：python3 scripts/governance/check_source_inventory.py [檔案] [--min N]
結束碼：0 = 核准數 >= N；1 = 不足；2 = 找不到表格或欄位。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[2] / "docs" / "governance" / "source-inventory.md"
STATUS_COL = "核准狀態"
APPROVED = "核准"


def cells(line: str) -> list[str]:
    """把一行 Markdown 表格拆成去除空白的儲存格。"""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def approved_sources(text: str) -> tuple[int, list[str]]:
    """回傳（來源總數, 核准的來源代碼）；找不到欄位時拋出 ValueError。"""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines)
                     if ln.lstrip().startswith("|") and STATUS_COL in cells(ln))
    except StopIteration:
        raise ValueError(f"找不到含「{STATUS_COL}」欄的表格") from None
    cols = cells(lines[start])
    idx = cols.index(STATUS_COL)
    total, approved = 0, []
    for ln in lines[start + 2:]:  # 跳過表頭與分隔線
        if not ln.lstrip().startswith("|"):
            break
        row = cells(ln)
        if len(row) != len(cols):
            break
        total += 1
        if row[idx] == APPROVED:
            approved.append(row[0])
    return total, approved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default=str(DEFAULT))
    ap.add_argument("--min", type=int, default=3, help="最少核准數（預設 3）")
    args = ap.parse_args(argv)
    try:
        total, approved = approved_sources(Path(args.path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    print(f"來源 {total} 個，核准 {len(approved)} 個：{', '.join(approved) or '無'}")
    return 0 if len(approved) >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
