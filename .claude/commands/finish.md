---
description: 結案一個已通過驗證的任務，例如 /finish P0-T02
argument-hint: <任務ID>
---

結案任務 $ARGUMENTS：

1. 重新執行 docs/plan/execution-plan.md 中 $ARGUMENTS 的驗證指令，確認仍全部通過；失敗就停止並回報。
2. 確認 docs/progress/records/ 中 $ARGUMENTS 的驗收紀錄各段落都已填寫，判定欄為 PASS。
3. 若 repo 已有 git：commit 本任務的變更（訊息格式 `feat($ARGUMENTS): <摘要>`），不要 push，除非我要求。
4. 用 ncu-resilience-tracker 執行 `set $ARGUMENTS done --evidence <驗收紀錄路徑>`。若被拒絕，回報原因，不要加 --force。
5. 執行 `status`。若本階段任務已全部 done，提醒我執行階段閘門檢查（`check --run-gates`），並列出需要我人工確認的閘門。
6. 告訴我下一個建議任務與理由。
