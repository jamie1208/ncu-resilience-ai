"""標記資料集統計與 dev／test 切分（G1-5：--check-min 100）（P1-T09）。

在 backend/ 執行：uv run python ../scripts/labeling/dataset_stats.py --help
邏輯在 backend/app/labeling/cli.py。
"""

import sys
from pathlib import Path


def main() -> int:
    """把 backend/ 加入匯入路徑後執行。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.labeling.cli import stats_main

    return stats_main()


if __name__ == "__main__":
    raise SystemExit(main())
