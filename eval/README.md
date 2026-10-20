# eval

評測框架（P2-T05）：`scripts/eval/run_eval.py`，邏輯在 `backend/app/ai/eval/`。

- `thresholds.yaml`：各 Skill 的指標與門檻、全部共用的 schema 合格率與可追溯率門檻。修改門檻要由使用者決定並寫 ADR。
- `reports/<skill>/<version>.json`：以 `config/llm.yaml` 的預設模型實測（`--live`）的報告；`<version>.<model>.json` 是用 `--model` 換模型的比較；`<version>.recorded.json` 是錄製回應的報告。報告進 git，只放統計與每筆的判定（expected 欄位與預測值），不放完整輸出。
- `reports/P2-summary.json`：`--all --summary` 產生的摘要（閘門 G2-1）。
- `runs/`：每次執行的完整輸出（不進 git）。

評測不寫資料庫；`--max-cost` 限制一次執行的總費用，預設等於每日上限。CI 只跑錄製模式（`backend/tests/golden/test_skills.py`）。
