---
description: 結案一個已通過驗證的任務，例如 /finish P0-T02
argument-hint: <任務ID>
---

結案任務 $ARGUMENTS：

1. 重新執行 docs/plan/execution-plan.md 中 $ARGUMENTS 的驗證指令，確認仍全部通過；失敗就停止並回報。
2. 確認 docs/progress/records/ 中 $ARGUMENTS 的驗收紀錄各段落都已填寫，判定欄為 PASS。
3. 用 ncu-resilience-tracker 執行 `set $ARGUMENTS done --evidence <驗收紀錄路徑>`。若被拒絕，回報原因，不要加 --force。
   - 若 $ARGUMENTS 是「需你決定」（`human: true`）的任務，tracker 會要求 `--confirmed-by-user`：先停下來，列出驗收紀錄中使用者的各項決定並請我明確確認可以結案；取得同意後才加此旗標重跑。不得因為我執行了 /finish 就視為已同意。
4. 若 repo 已有 git：先確認 `git status` 中沒有 `.env`、金鑰檔或 `data/raw/`、`data/labeled/` 的內容，再 commit 本任務的變更（含 `docs/progress/state.yaml` 與驗收紀錄）（訊息格式 `feat($ARGUMENTS): <摘要>`）。
5. Push 任務分支：執行 `git push -u origin <目前任務分支>`。
6. 合併進 main 並推送：
   - `git switch main && git pull --ff-only origin main`
   - `git merge --no-ff <任務分支> -m "merge($ARGUMENTS): <摘要>"`
   - `git push origin main`
   - 完成後留在 main，保留任務分支不刪除。
   - 任何一步失敗（沒有 origin、認證失敗、main 無法 fast-forward、合併衝突、push 被拒絕）都要停止並回報；合併衝突時執行 `git merge --abort` 還原。不得改寫歷史、不得使用 --force、不開 PR。
7. 執行 `status`。若本階段任務已全部 done，提醒我執行階段閘門檢查（`check --run-gates`），並列出需要我人工確認的閘門。
8. 告訴我下一個建議任務與理由。
