# ADR-0002：tracker 人工閘門保護（新增 P0-T11）

- 狀態：已採納
- 日期：2026-10-17

## 背景

P0-T10 驗證 tracker 時發現：`tracker.py set` 只在任務標記 done 時檢查前置與檔案，`manual: true` 的人工閘門（如 G0-3 CI 全綠、G0-4 來源核准）可直接 `set G?-? pass`，沒有任何程式保護。驗證過程中 AI 實際誤標 G0-3 一次（已以備份還原）。AGENTS.md 規定人工閘門只能由使用者本人決定，目前只靠 SKILL.md 文字規則約束，不足以防止誤操作。

## 決策

1. 新增任務 P0-T11「tracker 人工閘門保護」，依賴 P0-T10，列於 P0。
2. 計畫變更依 tracker 規則進行：將 `references/phases.yaml` 複製為 `docs/progress/phases.yaml` 後修改並 `sync`；執行計畫文件同步新增任務定義，任務總數 59 → 60。
3. 行為：對 `manual: true` 閘門執行 `set <ID> pass` 時，必須加 `--confirmed-by-user`，否則拒絕（非零結束碼）且不改動 `state.yaml`；`human: true` 任務標記 done 時同樣須加此旗標（且仍先通過前置與檔案檢查）。確認後的 history 紀錄標記 `confirmed_by_user: true`。自動閘門、一般任務與其他狀態轉移的行為不變。
4. 測試以系統 Python + pytest 執行，放在 skill 目錄內，不依賴 `backend/` 的 uv 環境（使用者決定）。
5. P0-T08 的 CI 流水線納入 tracker 測試（使用者決定）。

## 考慮過的替代方案

- **完全禁止 tracker 設定人工閘門，改由使用者手改 state.yaml**：失去 history 紀錄，也與「狀態只經 tracker 修改」的原則衝突。
- **互動式確認提示**：AI agent 可直接回答提示，保護效果與旗標相同但不利於自動化測試。

## 影響

- 旗標本身無法驗證「真的是使用者本人」；它的作用是讓誤操作失敗，並讓 AI 必須明確引用使用者的確認。SKILL.md 規定只有在使用者明確同意後才可加此旗標。
- `docs/progress/phases.yaml` 自此成為 tracker 的計畫來源；之後計畫變更改這個檔案。
