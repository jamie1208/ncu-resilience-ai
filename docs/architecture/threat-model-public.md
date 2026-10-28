# 公開端威脅模型（P5-T11）

依據 ADR-0015（公開網站重新規劃）、AGENTS.md「AI 與產品 Skills 規範」、P5-T10 公開規則引擎。
本文件列出每個公開端點會給出的資料、可能的濫用方式與防護。**上線前（P5-T15）需由使用者本人審閱並同意（閘門 G5-8）。**

- 程式：`backend/app/api/v1/public/`（端點、rate limit、快取標頭）、`backend/app/publishing/sync.py`（寫入公開狀態）、`backend/app/publishing/policy.py`（公開規則與硬性邊界）
- 測試：`backend/tests/security/test_public_exposure.py`（G5-5）、`backend/tests/integration/test_public_api.py`、`backend/tests/unit/publishing/test_publish_policy.py`

## 資料流與信任邊界

```
來源文章、匿名投稿（不可信）
  → 擷取、S01–S08 分析、使用者查證（管理端，不對外）
  → sync.py：組出輸入 → policy.evaluate()（確定性規則，不呼叫模型）→ publications／publication_versions（已遮蔽 PII 的快照）
  → /api/v1/public/*：只讀 state = published 的目前版本，以白名單挑欄位回傳
  → 通道（P5-T15，只轉送公開網站與 /api/v1/public/*）→ 任何人
```

- 公開 API **不讀** events、event_evidence、source_items、event_reports、response_actions、audit_logs 等原始資料表；能被讀到的只有 `content.py` 的 `_published()` 這一個查詢。
- 公開內容只在 sync 時由公開規則產生一次，存成版本快照；之後規則或資料變動會產生新版本或撤下，不會在讀取時臨時組內容。
- 模型輸出不直接到公開端：S02 摘要、S08 措施都必須先經公開規則（查證門檻、指控比對、PII 遮蔽）才進快照。

## 公開端點

全部免登入、只有 GET（`tests/integration/test_auth.py` 列舉所有路由確認 `/api/v1/public/` 下沒有其他方法）。回應統一 `{data, meta, error}`；`id` 一律是 publication ID，不是事件 ID。

| 端點 | 回傳的資料 | 不回傳 |
| --- | --- | --- |
| `GET /api/v1/public/feed` | 公開的事件內容與安全提醒（新到舊）：類別、標題、摘要、地點、日期、風險分級；提醒只有類別與中性提醒文字 | 小報告、來源、改善進度（只在詳細頁） |
| `GET /api/v1/public/notices` | 安全提醒：類別、中性提醒文字（P3-T07 一般安全提醒模板：類別、地點、日期、防護建議） | 事件標題、摘要、來源 |
| `GET /api/v1/public/categories` | 每個類別的名稱與公開中的事件數、提醒數 | 未公開事件的數量 |
| `GET /api/v1/public/events` | 已查證、公開內容的事件（同 feed 的內容欄位），可依類別篩選 | 只有提醒的事件 |
| `GET /api/v1/public/events/{id}` | 公開版小報告：影響（面向、分數、不確定性、理由）、建議措施（不含依據推測原因者）、官方來源名稱與連結、改善進度 | S07 原因、推測原因的措施、非官方來源、證據摘錄 |
| `GET /api/v1/public/progress` | 有改善措施的公開事件：措施名稱、狀態、預計完成日、更新時間、各狀態數量 | 負責人、佐證、依據推測原因的措施 |

列表參數：`type`（只接受已知類別，其他回 422）、`limit`（1–50，預設 20）、`cursor`（不透明字串，格式不對回 422）。

## 硬性邊界（寫在程式裡，設定無法關閉）

| 邊界 | 落點 | 測試 |
| --- | --- | --- |
| 未查證只公開中性提醒；只有官方來源證實或使用者查證為 true 才公開內容 | `policy.evaluate()`；查證依據由 `sync._verification()` 從 `event.verify` 稽核與 S05 結果推得 | `test_publish_policy`、`test_public_api`（user／official／first_hand 三種依據） |
| 疑似指控永不自動公開內容（比對標題、摘要、影響理由、措施、改善措施名稱），只公開不寫地點的中性提醒 | `policy.evaluate()` | `test_public_exposure`（A02、A05、藏在措施裡的指控） |
| S07 原因不公開；依據推測原因的措施與改善措施不公開 | `policy._content()`、`ActionInput.cause_basis` | `test_public_exposure` |
| Facebook、匿名投稿只透過 AI 摘要呈現；原文、摘錄、連結永不公開 | 來源只列 `is_official_source()`；快照不含證據摘錄 | `test_public_exposure`（FB 原文與連結、匿名投稿原文） |
| 公開內容一律經 PII 遮蔽 | `mask_pii()` | `test_public_exposure`（`find_pii` 為空） |
| 事件不實、封存或不再符合規則時撤下 | `sync_publication()`（beat 每分鐘） | `test_public_api` |
| 已下架（P5-T14）不被同步覆蓋 | `sync_publication()` 遇 `taken_down` 直接略過 | `test_public_api` |

## 濫用方式與防護

| 威脅 | 濫用方式 | 防護 | 剩餘風險 |
| --- | --- | --- | --- |
| 指控外流 | 在社群或匿名投稿中誣指特定人，希望經系統放大 | 未查證事件只公開中性提醒；指控詞命中時連地點都不寫並標記 needs_review；公開版不放原因 | 關鍵字規則有漏判（例如不在清單中的稱謂）；不辨識人名，標題中的人名只能靠指控規則與 S02「不含人名」的要求擋下 |
| 推測寫成事實 | S07 推測原因、依推測原因產生的措施，被讀者當成已確認 | 原因一律不公開；推測依據的措施與改善措施不公開 | 手動建立的改善措施沒有 cause_basis，名稱由使用者自行負責 |
| 原文外流 | 透過公開端取得 FB 貼文或投稿原文、作者、連結 | 快照不含原文與摘錄；來源只列官方來源 | S02 摘要本身若轉述過多細節，仍會公開（摘要已有 PII 檢查與 S02 規則） |
| 個資外流 | 投稿中夾帶電話、Email、學號、身分證字號 | 所有公開文字經 `mask_pii()`；測試以 `find_pii` 檢查所有回應 | 不辨識人名與地址 |
| 枚舉內部資料 | 以事件 ID、遞增 ID 或撤下的 publication 探測未公開事件 | 公開 ID 是 publication 的隨機 UUID；事件 ID 一律 404；只有提醒、撤下、下架都回相同的 404 | 可從 404 與 200 得知某個 publication ID 曾存在，但 ID 無法猜測 |
| 透過篩選推測未公開資訊 | 以類別、cursor、limit 組合推測 | 篩選只作用在已公開快照；類別數量只算公開中的 | 無 |
| 管理端被觸及 | 從公開端存取 `/api/v1/admin/*`、登入 | 公開 router 不帶 session；管理端仍需登入；P5-T15 的通道只轉送公開路徑 | 本機開發時同一個 API 行程同時提供兩者，由通道設定隔離 |
| 洪水請求／爬蟲 | 大量請求拖垮本機服務或資料庫 | 每用戶端每 60 秒 60 次，超過回 429 與 `Retry-After`；`limit` 上限 50；回應帶 `Cache-Control: public, max-age=30` 與 ETag，可由瀏覽器或通道快取；`If-None-Match` 相符回 304 | 限速為行程內記憶體計數，多個 API 行程各自計算；經通道時用戶端位址可能都是通道本身（見下方待辦） |
| 限速追蹤使用者 | 限速資料成為訪客紀錄 | 只存「隨機鹽 + 位址」的雜湊，鹽在行程啟動時產生，計數每個時間窗清空，不落地、不寫日誌 | 無 |
| 快取殘留 | 撤下或下架後，舊內容仍留在瀏覽器或通道快取 | max-age 只有 30 秒；錯誤回應 `no-store`；API 每次請求都查目前狀態 | 最多 30 秒的瀏覽器快取；P5-T14 需確認通道快取的清除方式 |
| 注入 | 以 `type`、`cursor` 注入 SQL | `type` 只接受已知類別；cursor 解碼失敗回 422；查詢全部參數化 | 無 |
| 錯誤訊息洩漏 | 從錯誤訊息取得內部資訊 | 統一錯誤格式，不帶例外細節；未預期錯誤只記例外類型 | 無 |
| API 文件 | 從 `/docs`、`/openapi.json` 取得端點清單 | 預設關閉（`NCU_API_DOCS_ENABLED`） | 公開端點清單本身就在本文件，不視為秘密 |

## 不在本任務範圍

- 匿名回報入口 `POST /api/v1/public/reports`（P5-T13）：加入時需更新本文件與 `test_auth.py` 的公開路由規則（目前只允許 GET）。
- 一鍵下架與快取失效（P5-T14）。
- 通道設定、網域、用戶端位址來源（P5-T15）：經通道時需改用通道提供的可信任標頭（例如通道服務的 client IP 標頭）作為限速識別，且只信任來自通道的請求。
- 可觀測性中的公開 API 錯誤率與限速次數（P5-T04）。

## 上線前確認清單（G5-8）

- [ ] 使用者本人審閱上方端點清單與每個端點回傳的欄位
- [ ] 使用者本人審閱剩餘風險，同意上線
- [ ] P5-T15 通道只轉送公開網站與 `/api/v1/public/*`
