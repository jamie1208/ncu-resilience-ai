"""跑產品 Skill 的 golden cases 並產生評測報告（P2-T05）。

在 backend/ 執行：
    uv run python ../scripts/eval/run_eval.py --skill template --split test           # 錄製回應
    uv run python ../scripts/eval/run_eval.py --skill event-classifier --split test --live
    uv run python ../scripts/eval/run_eval.py --all --split test --live \
        --summary ../eval/reports/P2-summary.json
邏輯在 backend/app/ai/eval/runner.py；報告寫到 eval/reports/，完整輸出寫到 eval/runs/（不進 git）。
"""

import sys
from pathlib import Path


def main() -> int:
    """把 backend/ 加入匯入路徑後執行。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.ai.eval.runner import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
