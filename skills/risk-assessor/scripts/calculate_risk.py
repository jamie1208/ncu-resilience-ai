"""S06 risk-assessor 的確定性計算：把模型給的五維分數換算成加權分數與分級。

這支腳本只是入口，計算一律呼叫 `backend/app/risk/engine.py` 的同一個函式，
政策讀 `config/risk-policy.yaml`，所以和後端的結果一定相同（P2-T11）。

用法（在 repo 根目錄）：

    python skills/risk-assessor/scripts/calculate_risk.py output.json   # S06 的輸出 JSON
    cat output.json | python skills/risk-assessor/scripts/calculate_risk.py
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 加入 backend 的路徑之後才能載入引擎
_engine = importlib.import_module("app.risk.engine")
calculate_risk = _engine.calculate_risk
derived_fields = _engine.derived_fields
load_policy = _engine.load_policy

__all__ = ["calculate_risk", "derived_fields", "main"]


def main(argv: list[str] | None = None) -> int:
    """讀取 S06 輸出，印出 risk_score、risk_level、high_risk 與 policy_version。"""
    args = sys.argv[1:] if argv is None else argv
    text = Path(args[0]).read_text(encoding="utf-8") if args else sys.stdin.read()
    policy = load_policy()
    result = derived_fields(json.loads(text), policy) | {"policy_version": policy.policy_version}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
