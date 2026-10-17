# RBAC 與稽核規格

依據規格 §2（使用者與權限）、§10（API 與權限）、§9（audit_logs append-only）與 AGENTS.md。

- 實作：`backend/app/core/security.py`（角色、權限、`Principal`、`require()`、`Authenticator` 介面）
- 稽核：`backend/app/services/audit.py`（`record_audit()`）；`infra/migrations/versions/0002_audit_append_only.py`（trigger）
- 測試：`backend/tests/unit/test_security.py`、`backend/tests/integration/test_audit_append_only.py`
- 驗證實作（密碼、session、CSRF）屬 P3-T01；本文件與 `security.py` 先定義「誰能做什麼」。

## 角色

| 角色 | 對應規格 §2 | 身分（`Principal.kind`） | 說明 |
| --- | --- | --- | --- |
| `visitor` | 訪客 | anonymous | 未登入的所有人 |
| `reporter` | 匿名投稿者 | reporter | 持有效收據碼補件時才是 reporter；不是帳號，不得識別個人 |
| `admin` | 開發團隊管理者 | admin | 個人帳號；敏感存取需稽核 |
| `service_ingestion` | 系統服務帳號 | service | 來源擷取排程 |
| `service_ai` | 系統服務帳號 | service | AI 分析（Skill Runner） |
| `service_notifier` | 系統服務帳號 | service | 依發布規則發布與外送警示 |

系統服務帳號依規格「最小權限」拆成三個：擷取程式不能改事件，AI 不能發布，通知服務不能查證。

`Principal` 建立時會檢查身分種類與角色是否一致（`KIND_ROLES`）：anonymous 只能是 visitor、reporter 只能是 reporter、admin 只能是 admin、service 只能是三種服務角色，且至少要有一個角色。不一致時直接拋出 ValueError，避免出現「anonymous 卻有管理權限」這種無法追溯的身分。

## 權限矩陣

| 權限 | visitor | reporter | admin | service_ingestion | service_ai | service_notifier |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| `public:read` | ✓ | ✓ | ✓ | | | |
| `reports:create` | ✓ | ✓ | | | | |
| `reports:followup` | | ✓ | | | | |
| `reports:read_internal` ⚠ | | | ✓ | | | |
| `events:read_all` | | | ✓ | | ✓ | |
| `events:write` | | | | | ✓ | |
| `events:verify` | | | ✓ | | | |
| `events:analyze` | | | ✓ | | | |
| `alerts:read_all` | | | ✓ | | | ✓ |
| `alerts:publish` | | | | | | ✓ |
| `alerts:retract` | | | ✓ | | | |
| `sources:read` | | | ✓ | ✓ | | |
| `sources:manage` | | | ✓ | | | |
| `ingestion:write` | | | | ✓ | | |
| `ingestion_runs:read` | | | ✓ | | | |
| `ai:run` | | | | | ✓ | |
| `scenarios:create` | | | ✓ | | | |
| `audit_logs:read` ⚠ | | | ✓ | | | |

⚠ = 敏感權限（`SENSITIVE_PERMISSIONS`）：連「讀取」也必須寫入稽核紀錄。

重點規則：

- **只有 `service_notifier` 能發布警示**，而且只能依 `app/alerts/policy.py` 的確定性規則發布（AGENTS.md）。管理者可以撤回，但不能直接發布。
- 投稿者無權建立已核實事件，也不能觸發公開（規格 §2）。
- 管理者不能寫入擷取資料或直接改事件內容；事件狀態只能經 `events:verify`，並由 `app/domain/states.py` 檢查轉移。

## 路由權限（規格 §10）

| 方法 | 路由 | 權限 | 稽核 |
| --- | --- | --- | --- |
| GET | `/api/v1/events` | `public:read`（只回公開事件與欄位） | |
| GET | `/api/v1/events/{id}` | `public:read` | |
| GET | `/api/v1/alerts` | `public:read`（只回有效警示） | |
| POST | `/api/v1/reports` | `reports:create`（限流） | `report.create`（不記原文） |
| POST | `/api/v1/reports/{token}/followups` | `reports:followup`（有效收據碼） | `report.followup`（不記收據碼） |
| GET | `/api/v1/dashboard/summary` | `public:read` | |
| GET | `/api/v1/admin/events` | `events:read_all` | |
| POST | `/api/v1/admin/events/{id}/verify` | `events:verify` | `event.verify` |
| POST | `/api/v1/admin/events/{id}/analyze` | `events:analyze` | `event.analyze` |
| POST | `/api/v1/admin/alerts/{id}/retract` | `alerts:retract` | `alert.retract`（含原因） |
| POST | `/api/v1/admin/scenarios` | `scenarios:create` | `scenario.create` |
| GET | `/api/v1/admin/ingestion-runs` | `ingestion_runs:read` | |
| GET | `/api/v1/admin/audit-logs` | `audit_logs:read` | `audit_log.read`（敏感讀取） |

未登入存取需要權限的路由回 `UNAUTHORIZED`；已登入但權限不足回 `FORBIDDEN`（規格 §10.1）。

## 稽核紀錄（audit_logs）

### 什麼要記

- 所有寫入操作（規格 §10.1：所有寫入建立 request_id），包括服務帳號的寫入。
- 敏感讀取：`audit_logs:read`、`reports:read_internal`。
- 權限遭拒（`FORBIDDEN`）的管理端請求：P3-T01 加入（`authorize()` 目前遭拒時不寫紀錄）。

### 欄位

| 欄位 | 內容 |
| --- | --- |
| `actor` | `Principal.actor`：`admin:<帳號ID>`、`service:<服務名稱>`、`anonymous`、`reporter`（投稿者不含收據碼或任何識別資訊） |
| `action` | `<資源>.<動作>`，小寫，例如 `event.verify`、`alert.retract`；格式不符會被拒絕 |
| `target` | 被操作的對象，例如 `events/<id>` |
| `request_id` | 參數 > 目前請求的 `X-Request-ID` > 新產生的 UUID（背景工作沒有請求上下文時也一定有值） |
| `metadata` | 補充資訊（JSON，≤ 4 KB），規則見下方「metadata 規則」 |
| `created_at` | 資料庫 `now()`，aware UTC（規格欄位 `timestamp`） |

### metadata 規則（`prepare_metadata()`）

audit_logs 寫入後無法刪除，所以 metadata 寫入前會先檢查與正規化，不合格時拋出 `AuditMetadataError`：

- **欄位名稱**：拆成小寫分段（camelCase、底線、連字號都會拆開）後比對。
  - 任何一段是 password、passwd、pwd、secret、token、apikey、authorization、cookie、credential(s)、email、phone → 拒絕；
  - 出現連續的 api+key、private+key、national+id、session+id、session+key → 拒絕；
  - **最後一段**是 text、content、body → 拒絕（例如 `report_text`、`raw_content`）；`content_hash`、`text_length`、`prompt_tokens`、`session_count` 則允許。
- **字串值**：超過 200 字元（可能是原文）、或含 email、手機號碼（09xx-xxx-xxx）、身分證字號樣式 → 拒絕。
- **型別**：UUID → 字串；aware datetime → UTC ISO 字串；Enum → 值；沒有時區的 datetime、bytes、set 等不支援的型別 → 拒絕。
- 正規化後超過 4 KB → 拒絕。

這些檢查是防止明顯的錯誤，不能取代呼叫端的判斷：只放必要的識別碼與摘要，不放原文。

### 寫入方式

- 敏感權限一律用 `authorize(session, principal, permission, target=...)` 檢查：通過時自動寫入 `<資源>.<動作>`（例如 `audit_logs.read`）的稽核紀錄；不足時拋出 PermissionDeniedError，不寫入任何紀錄。一般權限也可以用它，只是不會寫紀錄。
- 其他寫入透過 `record_audit(session, principal, action, ...)`。它只 flush、不 commit，與業務寫入在同一個交易：業務寫入失敗，稽核紀錄一併回滾；稽核寫不進去，業務寫入也不成立。
- 日誌與稽核都不得包含個資、投稿原文或金鑰（AGENTS.md）。

### Append-only（資料庫層強制）

migration `0002_audit_append_only` 建立：

- `audit_logs_no_update_delete`：`BEFORE UPDATE OR DELETE ... FOR EACH ROW`
- `audit_logs_no_truncate`：`BEFORE TRUNCATE ... FOR EACH STATEMENT`（TRUNCATE 不會觸發列層級 trigger）

任何修改都會以 `audit_logs is append-only: <操作> is not allowed`（SQLSTATE `42501`）失敗，ORM 與原生 SQL 皆同。

**限制**：資料表擁有者或 superuser 仍可停用 trigger 或刪除整張表。正式環境應讓應用程式使用「非擁有者」的資料庫角色（只有 INSERT、SELECT 權限），migration 才用擁有者角色；這屬於部署任務（P5-T06），並應定期備份稽核紀錄（P5-T05）。

## 之後的任務

- P3-T01：實作 `Authenticator`（argon2 密碼、HttpOnly Secure Cookie session、CSRF），API 依賴注入 `require()`，並把權限遭拒寫入稽核。
- P3-T02：公開 API 欄位白名單。
- P5-T06：正式環境的資料庫角色分離。
