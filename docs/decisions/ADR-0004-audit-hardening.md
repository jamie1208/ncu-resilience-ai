# ADR-0004：稽核與權限強化（新增 P0-T12）

- 狀態：已採納
- 日期：2026-10-17

## 背景

P0 全部任務完成、四道閘門通過後（G0-3、G0-4 由使用者確認），使用者執行 `/code-review`，對 P0-T07（RBAC 與稽核）提出 9 項發現。其中數項是實際的缺陷或規範違反：

1. 稽核 metadata 只檢查欄位名稱、不檢查值；`audit_logs` 為 append-only，個資一旦寫入就無法刪除。
2. metadata 含 UUID、datetime 時，大小檢查（`json.dumps(default=str)`）會通過，但寫入 JSONB 時失敗，連帶業務交易失敗。
3. 欄位名稱以子字串比對，會誤擋 `prompt_tokens`、`content_hash` 等正常欄位。
4. `Principal` 的 kind 與 roles 可以不一致（例如 anonymous 卻有 ADMIN 角色），稽核紀錄的行為者就不可信。
5. 沒有請求上下文時（背景工作），request_id 為空，違反 AGENTS.md「寫入操作產生 request_id」。
6. 「敏感讀取必須稽核」只靠每個路由記得呼叫，沒有集中強制。
7. 測試資料庫的 audit_logs 無法清空，會持續累積。
8. 新增了 `type: ignore`，違反 AGENTS.md「mypy 不可新增忽略」。除了審查指出的測試，`app/domain/states.py`（P0-T06）與 P0-T05／T06 的測試也有。
9. `Principal.permissions` 每次呼叫都重新計算。

目前沒有任何程式寫入稽核紀錄，所以沒有實際損害；但 P1 開始寫入資料後，第 1、2、5 項會直接影響正確性，第 8 項是規範違反。

## 決策

1. 新增任務 **P0-T12「稽核與權限強化（code review 修正）」**，依賴 P0-T07，列在 P0。進入 P1 前完成（使用者決定）。
2. 計畫變更：`docs/progress/phases.yaml` 新增任務並執行 `sync`；執行計畫加入定義；任務總數 60 → 61。
3. 任務範圍為上述 9 項，驗證為：
   - `cd backend && uv run pytest -q && uv run mypy app`；
   - `grep -rn "type: ignore" backend/app backend/tests` 沒有結果。
4. P0 的階段驗收紀錄（`record P0`）延到 P0-T12 結案後產生。已通過的 G0-1～G0-4 不需要重跑，但 P0-T12 結案時仍須經過 CI。

## 考慮過的替代方案

- **併入 P1 的第一個任務**：範圍混雜，而且 P1 第一個任務就會開始寫入資料，第 1、2、5 項的修正必須搶在它之前。
- **只修高嚴重性的項目**：第 4、6、7、9 項的修正都很小，留下來只會在 P3 前又要重新處理一次。

## 影響

- P0 重新變為「進行中」（11/12），tracker 在 P0-T12 完成前不會推進到 P1。
- `record_audit` 會拒絕看起來像個資的值（email、手機、身分證字號樣式）與過長的字串。呼叫端必須只放必要的識別碼與摘要，不放原文。
