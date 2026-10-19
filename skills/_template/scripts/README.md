# scripts（可選）

放 Skill 需要的確定性計算，例如 S06 的 `calculate_risk.py`（與 `app/risk/engine.py` 共用同一個函式）。

- 數值計算（加權、門檻、What-if）一律由程式完成，模型只提供分項判斷與解釋（AGENTS.md）。
- 腳本只做純計算：不連網、不讀寫資料庫、不呼叫模型；輸入與輸出都是 JSON。
- 腳本要有對應的單元測試，放在 `backend/tests/unit/`。

本模板的分類任務不需要計算，因此沒有腳本；不需要腳本的 Skill 可以刪除這個資料夾。
