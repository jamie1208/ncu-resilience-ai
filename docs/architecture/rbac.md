# RBAC 與稽核規格

依據規格 §2（使用者與權限）、§10（API 與權限）、§9（audit_logs append-only）、AGENTS.md、ADR-0008（個人版：單一使用者）與 ADR-0015（公開網站：公開唯讀 API，管理端不對外）。

- 角色與權限、密碼與 token 工具：`backend/app/core/security.py`
- 帳號與 session：`backend/app/services/auth.py`；資料表 `infra/migrations/versions/0007_users_roles.py`
- 請求驗證（cookie、CSRF、權限依賴）：`backend/app/api/deps.py`；登入 API：`backend/app/api/v1/auth.py`
- 建立／重設使用者：`scripts/admin/create_admin.py`
- 稽核：`backend/app/services/audit.py`（`record_audit()`）；`infra/migrations/versions/0002_audit_append_only.py`（trigger）
- 測試：`backend/tests/unit/test_security.py`、`backend/tests/integration/test_auth.py`、`backend/tests/integration/test_audit_append_only.py`

## 角色

| 角色 | 對應規格 §2 | 身分（`Principal.kind`） | 說明 |
| --- | --- | --- | --- |
| （無） | — | anonymous | 未登入者：沒有任何角色與權限 |
| `admin` | 管理者（個人版即使用者本人） | admin | 系統唯一的帳號；敏感存取需稽核 |
| `service_ingestion` | 系統服務帳號 | service | 來源擷取排程 |
| `service_ai` | 系統服務帳號 | service | AI 分析（Skill Runner） |
| `service_notifier` | 系統服務帳號 | service | 依發布規則發布與外送警示 |

系統服務帳號依規格「最小權限」拆成三個：擷取程式不能改事件，AI 不能發布，通知服務不能查證。

個人版（ADR-0008）移除了規格 §2 的訪客（公開閱讀）與匿名投稿者角色，以及 `public:read`、`reports:create`、`reports:followup` 權限（P3-T01）。ADR-0015 恢復公開閱讀，但**不恢復角色或權限**：公開唯讀 API（P5-T11）不經 `Principal`，任何人都以 anonymous 身分讀取，能讀到的範圍完全由公開規則（P5-T10）寫入的公開狀態決定，見下方「公開唯讀 API」。

`Principal` 建立時會檢查身分種類與角色是否一致（`KIND_ROLES`）：anonymous 不能有任何角色、admin 只能是 admin、service 只能是三種服務角色，且 admin 與 service 至少要有一個角色。不一致時直接拋出 ValueError，避免出現「anonymous 卻有管理權限」這種無法追溯的身分。

## 權限矩陣

| 權限 | admin | service_ingestion | service_ai | service_notifier |
| --- | :-: | :-: | :-: | :-: |
| `reports:read_internal` ⚠ | ✓ | | | |
| `events:read_all` | ✓ | | ✓ | |
| `events:write` | | | ✓ | |
| `events:verify` | ✓ | | | |
| `events:analyze` | ✓ | | | |
| `alerts:read_all` | ✓ | | | ✓ |
| `alerts:publish` | | | | ✓ |
| `alerts:retract` | ✓ | | | |
| `sources:read` | ✓ | ✓ | | |
| `sources:manage` | ✓ | | | |
| `ingestion:write` | | ✓ | | |
| `ingestion_runs:read` | ✓ | | | |
| `ai:run` | | | ✓ | |
| `scenarios:create` | ✓ | | | |
| `audit_logs:read` ⚠ | ✓ | | | |

⚠ = 敏感權限（`SENSITIVE_PERMISSIONS`）：連「讀取」也必須寫入稽核紀錄。

重點規則：

- **只有 `service_notifier` 能發布警示**（個人版：通知使用者本人），而且只能依 `app/alerts/policy.py` 的確定性規則（AGENTS.md）。使用者可以撤回，但不能直接發布。
- 管理者不能寫入擷取資料或直接改事件內容；事件狀態只能經 `events:verify`，並由 `app/domain/states.py` 檢查轉移。

## 路由權限（規格 §10）

| 方法 | 路由 | 權限 | 稽核 |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | **免登入** | |
| POST | `/api/v1/auth/login` | **免登入**（連續失敗鎖定） | `auth.login`／`auth.login_failed`／`auth.login_locked` |
| POST | `/api/v1/auth/logout` | 已登入＋CSRF | `auth.logout` |
| GET | `/api/v1/auth/me` | 已登入 | |
| GET | `/api/v1/admin/events` | `events:read_all` | |
| GET | `/api/v1/admin/events/{id}` | `events:read_all` | |
| POST | `/api/v1/admin/events/{id}/verify` | `events:verify` | `event.verify` |
| POST | `/api/v1/admin/events/{id}/analyze` | `events:analyze` | `event.analyze` |
| GET | `/api/v1/admin/alerts` | `alerts:read_all` | |
| POST | `/api/v1/admin/alerts/{id}/retract` | `alerts:retract` | `alert.retract`（含原因） |
| GET | `/api/v1/admin/dashboard` | `events:read_all` | |
| POST | `/api/v1/admin/scenarios` | `scenarios:create` | `scenario.create` |
| GET | `/api/v1/admin/ingestion-runs` | `ingestion_runs:read` | |
| GET | `/api/v1/admin/ingestion-runs/health` | `ingestion_runs:read` | |
| GET | `/api/v1/admin/audit-logs` | `audit_logs:read` | `audit_logs.read`（敏感讀取，經 `authorize()`） |
| GET | `/api/v1/public/feed` | **免登入**（限速） | |
| GET | `/api/v1/public/notices` | **免登入**（限速） | |
| GET | `/api/v1/public/categories` | **免登入**（限速） | |
| GET | `/api/v1/public/events` | **免登入**（限速） | |
| GET | `/api/v1/public/events/{id}` | **免登入**（限速） | |
| GET | `/api/v1/public/progress` | **免登入**（限速） | |

除 health、登入與 `/api/v1/public/*` 外，所有端點都需要登入；`/api/v1/admin/*` 在 router 層就先擋未登入，新端點即使忘了加權限依賴也不會變成免登入。`/docs`、`/redoc`、`/openapi.json` 預設關閉（`NCU_API_DOCS_ENABLED=true` 才開，僅供本機開發）。`tests/integration/test_auth.py` 會列舉所有路由驗證這點，並確認 `/api/v1/public/` 底下只有 GET。

未登入存取需要權限的路由回 `UNAUTHORIZED`（401）；已登入但權限不足或 CSRF 驗證失敗回 `FORBIDDEN`（403）；登入鎖定回 `RATE_LIMITED`（429）（規格 §10.1）。

## 公開唯讀 API（P5-T11，ADR-0015）

- 程式：`backend/app/api/v1/public/`；威脅模型與每個端點的資料、濫用方式與防護：`docs/architecture/threat-model-public.md`。
- 只讀取 `publications` 中 state 為 published 的目前版本（公開規則產生、已遮蔽 PII 的快照），不讀任何原始資料表；回傳欄位以白名單挑選，`id` 是 publication ID。
- 不讀取 session cookie、不需要也不使用 CSRF；沒有寫入，所以不寫稽核。
- 每用戶端每 60 秒 60 次，超過回 `RATE_LIMITED`（429）與 `Retry-After`；用戶端只以加鹽雜湊識別，不保存位址。
- 公開狀態由 `backend/app/publishing/sync.py` 以 `service_notifier` 身分寫入（Celery beat 每分鐘），每次公開、更新、撤下寫入稽核 `publication.publish`／`publication.update`／`publication.withdraw`（metadata 只有事件 ID、版本、公開程度與規則代碼）。
- 管理端（`/admin`、`/api/v1/admin/*`、登入）不經通道對外（P5-T15）。

## 驗證（P3-T01）

| 項目 | 做法 |
| --- | --- |
| 帳號 | `users` 只允許一列（常數唯一索引 `uq_users_single_user`），角色固定 `admin`；以 `scripts/admin/create_admin.py` 建立或重設密碼 |
| 密碼 | argon2id（argon2-cffi 預設參數），不限長度（不可為空）；參數過時時於登入成功後重新雜湊；帳號不存在時也跑一次雜湊，避免以回應時間探測帳號 |
| 鎖定 | 連續失敗 5 次鎖定 15 分鐘，鎖定期間正確密碼也回 429；成功登入或重設密碼時歸零 |
| Session | 隨機 32 bytes token，cookie `ncu_session`：HttpOnly、Secure、SameSite=Strict、Path=/api；資料庫 `user_sessions` 只存 SHA-256；絕對有效期 `NCU_SESSION_TTL_HOURS`（預設 12 小時）；登出、重設密碼、停用帳號即失效 |
| CSRF | 每個 session 一組 CSRF token（同樣只存雜湊），登入回應與 cookie `ncu_csrf`（非 HttpOnly、Secure、SameSite=Strict、Path=/）提供；POST／PUT／PATCH／DELETE 須帶 `X-CSRF-Token` 標頭且與該 session 相符 |
| 錯誤訊息 | 帳號不存在、密碼錯誤、帳號停用一律回「帳號或密碼錯誤」 |

Secure cookie 只經 HTTPS 送出；瀏覽器把 `http://localhost` 視為安全來源（Chrome、Firefox），本機開發可用，但 Safari 可能不送出。

## 稽核紀錄（audit_logs）

### 什麼要記

- 所有寫入操作（規格 §10.1：所有寫入建立 request_id），包括服務帳號的寫入。
- 敏感讀取：`audit_logs:read`、`reports:read_internal`。
- 登入、登出、登入失敗與鎖定（`auth.*`）；建立使用者與重設密碼（`user.create`、`user.password_reset`）。密碼與 token 永不寫入。
- 已登入者（或服務帳號）權限遭拒（`FORBIDDEN`）：`require_permission()` 以獨立交易寫入 `auth.forbidden`（metadata 為所需權限）。未登入的 401 不寫，避免被灌爆。

### 欄位

| 欄位 | 內容 |
| --- | --- |
| `actor` | `Principal.actor`：`admin:<帳號ID>`、`service:<服務名稱>`、`anonymous` |
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

- P5-T06：正式環境的資料庫角色分離。
