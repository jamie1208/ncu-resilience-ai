# CLAUDE.md

本專案的完整 AI 開發規範在 AGENTS.md，所有規則以它為準：

@AGENTS.md

## Claude Code 補充

- 執行任務用 `/task <任務ID>`，結案用 `/finish <任務ID>`，審視進度用 `/review`。
- 進度追蹤使用 `.claude/skills/ncu-resilience-tracker`；開始與結束任務前都要用它確認狀態。
- 任務定義讀 `docs/plan/execution-plan.md`，不要憑記憶推測檔案清單。
- 回覆使用繁體中文。
