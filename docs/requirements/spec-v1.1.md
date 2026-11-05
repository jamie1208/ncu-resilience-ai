# NCU Resilience AI 系統規劃書 v1.1

- 文件版本：1.1｜日期：2026-11-05｜任務：P7-T05（原 P5-T09）
- 基準：`NCU_Resilience_AI_System_Plan_v1.pdf`（v1.0，2026-10-16）
- 依據：ADR-0001～ADR-0017、`docs/plan/execution-plan.md` 各任務的範圍調整與實作調整、`docs/progress/records/` 驗收紀錄、repo 中的設定與程式（2026-11-05 `main` 1fcb5cc）

## 0. 如何閱讀本文件

1. v1.0 的 PDF 不修改。本文件依 v1.0 的章節編號（§0～§18、附錄 A、B）逐章寫出 v1.1 的有效內容；**沒有提到的內容沿用 v1.0**。
2. 第 19 章是 v1.0 → v1.1 的差異清單，每一項有編號（D01…）、對應章節、v1.0 原文、v1.1 內容與依據。第 20 章逐章對照，確認 v1.0 每一章都已檢查過。
3. 本文件記錄的是 2026-11-05 實際完成的系統，不是計畫中的預期。程式與設定檔是最終依據；細節（欄位、參數）以文中列出的檔案為準。
4. 「使用者」指專案作者本人，也是管理端唯一的帳號；「訪客」指公開網站的任何讀者。

### 0.1 定位變更摘要

| 時間 | 決策 | 依據 |
| --- | --- | --- |
| 2026-10-16 | v1.0：研究專題，公開介面給一般使用者，後台給開發團隊；AI 判為高風險即自動對外警示 | v1.0 §0 |
| 2026-10-18 | **改為個人使用**：只有使用者一人，刪除公開網站、公開 API、匿名通報、自動發布與負載測試；Facebook 社團納入來源 | ADR-0008 |
| 2026-10-27 | **改為對外公開**（部分取代 ADR-0008）：新增公開網站、公開唯讀 API、匿名回報入口；公開由確定性規則自動判斷，並有程式寫死的硬性邊界；管理端仍只有使用者一人；通知仍只送給使用者 | ADR-0015 |
| 2026-11-04 | 推測原因經使用者人工確認後，相關學校措施可以公開（原因本身仍不公開） | ADR-0017 |

結論：v1.1 的系統是「**個人營運、對外公開唯讀內容**」的專題網站。v1.0 的公開與通報章節大致重新適用，但多了 ADR-0015 的硬性邊界；v1.0 的多角色後台、對處室通知、正式部署則不適用。

---

## §0 文件說明與決策紀錄

| 需求項目 | v1.0 | v1.1 |
| --- | --- | --- |
| 開發目的 | 專題成果與未來正式導入並重 | 專題展示為主（公開期限 3 個月以上，以真實資料展示）；正式導入不在範圍內，只保留 §17 的移交條件 |
| 使用者 | 公開介面供一般使用者；後台僅開發團隊 | 公開網站供任何人唯讀；後台只有使用者本人一個帳號 |
| 資料來源 | 校內公告、社群、新聞、完全匿名的自主通報 | 校方公告與中央大學新聞網、氣象署開放資料、使用者所在的 Facebook 社團（每日一次讀取）、匿名回報；外部新聞媒體與 Dcard 不納入（§0.2） |
| 分析深度 | 分類、查證、跨事件關聯、趨勢預警、What-if | 不變；另加事件說明（摘要）、事件小報告、改善追蹤到底、再發生提醒 |
| 通知政策 | AI 判為高風險即自動對外警示 | 拆成兩條：**個人通知**只送給使用者本人（`app/alerts/policy.py`）；**公開內容**由公開規則引擎判斷（`app/publishing/policy.py`），未查證只公開中性提醒。系統不寄信給任何處室 |
| 模型 | 優先 OpenAI 雲端 API；保留替換能力 | 不變；預設 `gpt-5-mini`，模型名稱、費用上限只在 `config/llm.yaml` |
| 進度 | 無硬性期限，分階段 | 不變；8 個階段、品質閘門推進 |
| 交付 | 正式 PDF 系統規劃書 | v1.0 PDF 保留；v1.1 以本 Markdown 文件交付 |

「需要正視的設計衝突」的雙軌原則保留，並由 ADR-0015 落實成程式中的硬性邊界（§6.4）。

### 0.2 實際資料來源（2026-11-05）

完整盤點與核准紀錄見 `docs/governance/source-inventory.md`；上線清單見 `config/sources.yaml`。

| 代碼 | 來源 | 擷取方式 | 頻率 | 依據 |
| --- | --- | --- | --- | --- |
| ncu-announcements | 校園公告 | RPage HTML | 一天兩次 | P0-T09 核准 |
| ncu-sec | 秘書室公告 | RPage HTML | 一天兩次 | P0-T09 |
| ncu-oia | 國際事務處 | RPage HTML | 一天兩次 | P0-T09 |
| ncu-aa | 教務處（總公告、課務組） | RPage HTML | 一天兩次 | P0-T09 |
| ncu-cc | 電算中心（9 個分類） | RPage HTML | 一天兩次 | ADR-0010 |
| ncu-news | 中央大學新聞網（中大、焦點、學術新聞） | 專用 HTML Adapter | 一天兩次 | ADR-0007 |
| ncu-epsc | 環安中心 | 專用 HTML Adapter | 一天兩次 | ADR-0010 |
| ncu-lib | 圖書館 | 專用 HTML Adapter | 一天兩次 | ADR-0010 |
| ncu-osa-life | 生活輔導組 | 專用 HTML Adapter | 一天兩次 | ADR-0010 |
| cwa-alerts | 氣象署開放資料（天氣警特報 W-C0033-001、地震 E-A0015-001，桃園市） | JSON API（`CWA_API_KEY`） | 每 10 分鐘 | ADR-0005；政府資料開放授權條款第 1 版 |
| facebook | 使用者已加入的 NCU 社團 | 每天 08:00 由 Claude Code 經 Claude in Chrome 讀取 → 人工匯入格式 → `manual_import` | 每天一次 | ADR-0008、ADR-0011、ADR-0015 |
| anonymous-report | 公開網站匿名回報 | 收件匣 → Adapter | 每 10 分鐘 | ADR-0015 第 8 點 |

- 校方來源的頻率由使用者在 2026-11-04 從每 30～360 分鐘改為一天兩次（公告很少）。
- 暫緩：總務處 `ncu-oga-news`（改版為 Nuxt，只能經未公開 JSON API）、人事室、低關聯處室。待決定：學務處 `ncu-osa`、Dcard、台水、台電。不核准：`cwa-rss`（使用聲明禁止衍生產品）、中央社、Google News。

---

## §1 專案背景、目標與範圍

§1.1 核心問題、§1.2 系統目標不變。§1.2「提供根因假設、可操作的改善措施」由 S07／S08 與事件小報告落實（P4-T09），「持續追蹤處理進度與校園韌性指標」由改善措施追蹤（P4-T05）、再發生提醒（P4-T10）與韌性成績單（P6-T05）落實。

§1.3 範圍與非目標不變，另加三項：

- 不是國立中央大學官方系統；公開網站每一頁都標明「個人專題，非國立中央大學官方系統」，並列出校安中心、110、119（ADR-0015 第 7 點）。
- 不寄信或通知任何處室。事件小報告只顯示「建議轉給哪個單位」，由使用者自行決定是否聯絡（ADR-0015 第 4 點）。
- 不保證公開網站持續可用：服務在使用者的電腦上運行，關機即停止。

---

## §2 使用者、權限與產品情境

| 角色 | v1.1 可使用功能 | 限制 | 程式 |
| --- | --- | --- | --- |
| 訪客 | 公開網站與 `/api/v1/public/*`：最新動態、安全提醒、事件分類查詢、公開版小報告、改善進度、韌性成績單 | 只讀已公開快照；看不到原文、來源摘錄、非官方來源、推測原因、內部風險分數以外的內部紀錄 | `app/api/v1/public/` |
| 匿名回報者 | `POST /api/v1/public/reports` 送出內容與地點 | **沒有收據碼、不能補件、不能查詢**；投稿不會直接公開 | `app/api/v1/public/reports.py` |
| 管理者（使用者本人） | 全部管理功能 | 單一帳號、argon2、HttpOnly Cookie session、CSRF；管理端只綁 127.0.0.1，不經通道對外 | `app/core/security.py`、`docs/architecture/rbac.md` |
| 系統服務帳號 | 排程、AI、通知（例如 `service:notifier`） | 最小權限；動作同樣寫入稽核 | `rbac.md` |

P0-T07 建立的 RBAC 與權限名稱保留（例如 `events:read_all`、`events:verify`、`events:analyze`、`alerts:retract`、`scenarios:create`），即使目前只有一個管理者。

### §2.1 關鍵使用情境

v1.0 的四個情境保留，期望結果依公開規則調整：

- 社群提及疑似自行車失竊：保留未核實狀態；若風險為高以上，公開只出現**不寫地點**的中性防竊提醒（指控詞命中時），不公開事件內容。
- 宿舍停電公告：官方來源證實 → 公開事件內容、公開版小報告與改善進度。
- 匿名通報食安疑慮：投稿是非官方的不可信來源；疑似指控永不自動公開，只在後台標記「疑似指控，需人工判斷」。
- 同區域多次事件：以 related_event 關聯與再發生提醒呈現，不認定因果；推測原因只在後台。

---

## §3 系統整體架構

架構不變：React + TypeScript 前端、FastAPI 模組化單體、PostgreSQL 16 + pgvector、Redis + Celery、OpenAI API Adapter、規則引擎（ADR-0001）。差異：

| 層級 | v1.1 |
| --- | --- |
| 呈現層 | 同一個前端專案，公開網站在根路徑、管理端在 `/admin`；雙設計系統 Minimalist Modern／Playful Geometric，建置時以 `VITE_DESIGN` 選擇（ADR-0003、ADR-0014） |
| API 層 | `/api/v1/public/*`（免登入，只有 GET 與一條匿名回報 POST）、`/api/v1/admin/*`、`/api/v1/auth/*`（需登入）；公開端限流與快取標頭 |
| 收集層 | Source Adapters（RPage、專用 HTML、CWA JSON、RSS〔通用元件，目前沒有核准來源〕、人工匯入、匿名回報收件匣）＋ Celery beat |
| 資料層 | PostgreSQL + pgvector。**不使用物件儲存**：系統不接受附件 |
| AI 層 | 自建 Skill Runner 只載入 `skills/`（S01～S10）；LLM Adapter（OpenAI、fake provider 供 CI） |
| 決策層 | 風險引擎（`app/risk/`）、查證規則（`app/verification/rules.py`）、個人通知規則（`app/alerts/policy.py`）、公開規則引擎（`app/publishing/policy.py`）、What-if 引擎（`app/simulations/engine.py`）、CRI（`app/resilience/`） |
| 工作層 | Celery + Redis：擷取、未分析文章掃描（每分鐘）、分析、小報告、公開同步（每分鐘）、重試與死信 |
| 維運層 | Prometheus + Grafana（只綁 127.0.0.1）、JSON 日誌與 trace_id、`pg_dump` 備份與還原腳本、GitHub Actions CI |
| 對外通道 | **新增**：Cloudflare Quick Tunnel → 公開閘道 nginx（白名單，`infra/tunnel/nginx-public.conf`）→ 公開網站與 `/api/v1/public/*`；其他路徑一律 404 |

### §3.1 端到端資料流程

來源擷取 → 原始資料保存與版本 → 標準化 → 未分析文章掃描（近 14 天、每日費用上限內）→ S01 相關性 → S02 事件抽取與摘要 → S03 分類 → S04 同事件／關聯 → S05 查證（規則優先）→ S06 風險（程式加權）→ 個人通知規則 → S07／S08 事件小報告（風險 medium 以上）→ 公開規則引擎 → 公開快照 → 改善追蹤、再發生提醒 → 時序統計、S09 趨勢解讀、CRI。

所有 AI 結果寫入 `ai_analysis_runs`，附 skill_version、schema_version、model_id、證據 ID 與 trace_id（v1.0 不變）。

---

## §4 主要功能需求（F01～F10）

| 功能 | v1.1 實作 | 驗收狀態 |
| --- | --- | --- |
| F01 多來源採集 | 12 個來源上線（§0.2）；增量 cursor、可重試失敗與自動停用、死信、來源健康 API | G1-1、G1-3 通過；穩定性改為一晚（§14） |
| F02 標準化與追溯 | 時間 aware UTC、內容雜湊版本、來源標籤 `tags`；Facebook 以內文雜湊為 `external_id`，只能追溯到社團與發文時間（ADR-0011） | G1-4 通過（排除 facebook） |
| F03 AI 事件管理 | S02 抽取＋事件說明（摘要）、S03 九類分類、S04 同事件合併 | 手動拆分與合併**未實作**（D24） |
| F04 多維風險評估 | 五維＋保底規則＋critical_override，`policy_version` 保存每次評分 | 通過 |
| F05 自動預警中心 | 拆為個人通知（警示狀態機、通知中心）與公開規則引擎（中性提醒、事件內容、一鍵下架） | G3-2、G5-5、G5-7 通過 |
| F06 決策輔助 | 事件小報告：可能的影響、值得查查看（推測）與已確認原因、同學／學校可以怎麼做（短中長期）、建議轉給哪個單位；推測原因可人工確認（ADR-0017） | G4-5 通過 |
| F07 儀表板 | 後台儀表板（`GET /admin/dashboard`）；公開端以類別統計與韌性成績單取代公開聚合統計 | G3-3 通過 |
| F08 跨事件關聯 | 關聯圖（Cytoscape）、確認／駁回／回滾 | **G6-1 待審閱**（ADR-0016 補註） |
| F09 What-if | 透明規則引擎＋S10 解釋；「假設情境，非成效預測」標示 | G7-1 待執行 |
| F10 改善與韌性追蹤 | 狀態 discovered → handling → recovered → improved（另有 dropped）、佐證、期限；再發生提醒；CRI | G4-4、G4-6、G6-3 通過 |

新增功能（v1.0 沒有獨立編號）：

- **事件說明**（P4-T08）：S02 輸出 `summary {text, evidence_ids}`，官方來源或較新文章才覆寫，寫入前經 `mask_pii()`。
- **再發生偵測**（P4-T10）：同地點同類別 30 天內 ≥ 2 件時在通知中心提醒；改進後 365 天內再發生標示 after_improvement。
- **人工確認原因**（P7-T07，ADR-0017）。
- **匿名回報收件匣**（P5-T13）。

---

## §5 可攜式 AI Skills 設計

S01～S10 的 ID、名稱與職責不變。實際封裝（`docs/architecture/skill-standard.md`、ADR-0013）：

```text
skills/<skill-name>/
  SKILL.md                 # frontmatter：name、description、metadata.version、metadata.schema_version
  schemas/input.schema.json
  schemas/output.schema.json
  references/*.md
  scripts/                 # 可選，例如 risk-assessor/scripts/calculate_risk.py（與引擎共用同一函式）
  tests/cases.jsonl        # 至少 20 筆，含正常、邊界、對抗
  tests/recorded/          # CI 用錄製回應
```

- v1.0 §5.1 的範例 `campus-risk-assessor/`、`schemas/risk-output.schema.json`、`tests/cases.json` 改為上述結構（名稱 `risk-assessor`、input／output 兩份 schema、`cases.jsonl`）。
- `skills/_template/` 是可執行的示範模板（公告類型分類），Runner 不載入底線開頭的資料夾（ADR-0013）。
- S05 的輸出值統一為 `verified`（v1.0 寫 `confirmed`）。
- S09 只解釋程式算出的異常，不產生件數；S10 只解釋假設與限制，數值全由引擎計算。

2026-11-05 版本：S01 1.2.0、S02 1.1.0，其餘 1.0.0；schema_version 皆為 1。

### §5.1 跨 AI 共用（可攜性驗證結果）

P7-T04 以 Claude Code 執行 S01～S10 的 test split golden cases（`docs/portability-report.md`）：161 筆中 156 筆通過，10 個 Skill 中 6 個全部達門檻；S01、S02、S06 各有 1～2 筆未過（schema 或指標）。結論：Skill 格式可攜，但輸出格式約束（例如不加 code fence）與部分判斷在其他客戶端需要調整。G7-2 由使用者判定通過，報告中的調整建議不處理。

### §5.2 執行治理

v1.0 五點全部落實：外部內容一律以 `<untrusted_source>` 包裹並跳脫標籤；單次呼叫費用上限 US$0.05、每日 US$1.0（`config/llm.yaml`），超過時文章留在 pending；輸出經 JSON Schema 與業務驗證（列舉、範圍、evidence_id 存在、PII）；版本與 trace_id 保存；允許 abstain。另加：CI 只用 fake provider 與錄製回應，不呼叫真實 API。

---

## §6 風險模型、證據查證與發布政策

### §6.1 五維風險模型

權重、分級不變（`config/risk-policy.yaml`，`policy_version: 2026-10-21.1`）：

- Risk = 0.30 人身安全 + 0.20 營運 + 0.15 財產 + 0.15 聲譽 + 0.20 影響範圍。
- 分級：low 0–29、medium 30–59、high 60–79、critical 80–100。
- critical_override：人身安全 ≥ 80 → critical。
- **新增保底**：任一面向 ≥ 80 → 至少 high（避免全棟停電、全校斷網被加權平均稀釋成 medium）。
- 模型只給五維分數與不確定性，加權由程式計算。

### §6.2 獨立三種判斷

不變。查證狀態值統一為 `verified／unverified／disputed／false`。

### §6.3 生命週期

依 `docs/architecture/state-machines.md`：

- 事件：v1.0 的狀態不變；查證狀態之間可互相改判；monitoring 可改判 disputed／false（A06）；`resolved → monitoring` 可重新開啟；`archived` 為終態。
- 警示（個人通知）：v1.0 的狀態不變，另加終態 `blocked`（規則阻擋，不落地）；`published` 表示已通知使用者本人；idempotency_key ＝ hash(event_id, alert_version, audience)。
- **新增：公開內容**（`publications`）：level content／safety_notice／none；state published／withdrawn／taken_down；每次變動產生已遮蔽 PII 的版本快照。
- **新增：改善措施**：discovered／handling／recovered／improved／dropped，每次轉移寫入 `response_action_transitions`。
- **新增：關聯決策**：pending → confirmed／rejected，只能回滾到 pending，不能直接翻轉。

### §6.4 自動公開策略（取代 v1.0 的表格）

公開與否只由公開規則引擎的確定性規則判斷，不由模型決定，也不需要使用者逐筆核可。以下硬性邊界寫在程式裡，設定無法關閉（ADR-0015、ADR-0017、`docs/architecture/threat-model-public.md`）：

| 情境 | v1.1 公開處理 |
| --- | --- |
| 官方來源證實，或使用者在後台查證為 true | 公開事件內容（標題、摘要、地點、日期、風險分級）、公開版小報告、官方來源名稱與連結、改善進度 |
| 只有兩份第一手來源支持的 verified | 只公開中性安全提醒 |
| 未查證，風險 high／critical | 只公開中性安全提醒（類別、地點、日期、防護建議；沿用個人通知的 general_safety 模板） |
| 未查證，風險 low／medium，或沒有風險評估 | 不公開 |
| 疑似指控（標題、摘要、影響理由、措施、改善措施名稱命中指控規則） | 永不自動公開內容；只公開**不寫地點**的中性提醒，並標記 needs_review |
| 來源互相矛盾（disputed）或不實（false） | 不公開內容；不實或封存時撤下 |
| S07 原因 | 永不公開；依據推測原因的措施與改善措施不公開，除非該原因在最新有效小報告中經使用者人工確認（需填確認依據，重新產生小報告後失效） |
| Facebook、匿名投稿 | 只透過 AI 摘要呈現；原文、摘錄、連結、發文者永不公開；來源欄只列官方來源 |
| 所有公開文字 | 一律經 `mask_pii()` |
| 下架 | 使用者一鍵下架即時生效並寫入稽核；已下架不被同步覆蓋；重新上架交回公開規則判斷 |

v1.0「爭議處理聯絡管道、回收 SLA 與錯誤通知機制」：回收以一鍵下架（即時）與每分鐘的公開同步落實，瀏覽器快取最多殘留 30 秒、通道邊緣不快取。**沒有另設對外的申訴管道**，「關於本站」頁只說明有問題的內容可由站主下架（D21）。

---

## §7 匿名回報與平台防護

| v1.0 要求 | v1.1 |
| --- | --- |
| 不要求姓名或帳號，產生高熵 receipt_token 供查詢與補充 | 不要求姓名或帳號；**不產生收據碼、不提供查詢與補件**，回應固定為 202 `{"status": "received"}`，不回顯投稿 |
| IP 等短期防濫用控制須揭露 | 不保存 IP、User-Agent、轉送標頭；限速只存「行程啟動時產生的隨機鹽 + 位址」的雜湊，時間窗結束即清空、不落地；uvicorn 存取日誌關閉 |
| CAPTCHA、速率限制、附件白名單、惡意檔案檢測、同文大量投稿偵測 | 速率限制：每用戶端 10 分鐘 3 則、全站 10 分鐘 30 則；只收 JSON、本文 ≤ 8 KiB、內容 10–2000 字、地點 ≤ 100 字，拒絕控制字元。**沒有 CAPTCHA、不接受附件、沒有同文偵測** |
| 不顯示原始投稿全文；去除個資 | 投稿原文永不公開；投稿是非官方的不可信來源，經與其他來源相同的管線與公開規則 |
| 依法遵審查設定保留與刪除；附件與原文加密儲存 | 收件匣只有內容、地點、收到時間；**資料不刪除、未加密**，只存在使用者本機資料庫（D19） |
| 說明非 110／119 或校安替代品 | 每一頁都有；回報頁另提醒內容本身可能透露身分 |

---

## §8 事件關聯、趨勢與 What-if

### §8.1 關聯分類

類型不變：same_event、related_event、hypothesized_cause（P6-T01 加入資料表）。邊保存 evidence_ids、method（ai／rule／manual）、score、decision（pending／confirmed／rejected）、決策者與時間。禁止 self-loop；同一對事件同類型不論方向只能一條；hypothesized_cause 至少一個證據。

目前只有 S04 管線會寫入關聯（related_event 為主），沒有流程產生 hypothesized_cause 邊，也沒有手動新增關聯的端點。

### §8.2 趨勢預警

- 週／月件數以 materialized views（`analytics_event_counts_week`／`_month`）依類別與地點統計；排除 false 與類別 none。
- 只偵測突增：期望值為之前各期的 EWMA（不看當期與未來），Poisson 右尾 p < 0.001 且當期 ≥ 5 件、至少 4 期歷史才判定（`DetectionParams`）。
- 回測（P6-T04）只使用切點前已建立的事件，報告寫入 `eval/reports/trends/`。
- S09 只解釋程式判定的異常；資料覆蓋率以 `ingestion_runs` 計算（low：ratio < 0.5 或無來源；full：ratio ≥ 0.95、每個來源 ≥ 0.9 且期間內沒有新增來源；其餘 partial）；覆蓋率 low 或沒有異常時不呼叫模型；輸出一律標註「聲量≠事件數」。只手動觸發，不排程（使用者決定）。
- 學期與假期**沒有**納入模型，只在 S09 的解釋與限制中提示（D27）。

### §8.3 情境模擬

公式不變：baseline × Π(1 − effect_i)（`app/simulations/engine.py`）。

- effect 是件數降低比例的假設區間 [low, high]（0～1）；點估計取中點；範圍為全取上限／下限；p05／p50／p95 以固定種子 Monte Carlo（均勻、獨立、2000 次）。
- 敏感度一次只移動一項措施，其他固定在中點；介面以龍捲風圖呈現。
- 效果未知的措施排除並列出；全部未知 → `not_estimable`，顯示「無法估計」，不呼叫 S10（A09）。
- 成本分級：< 5 萬 low、< 50 萬 medium、其餘 high、未填 unknown。措施最多 10 項。
- 未給種子時以 `secrets` 產生並保存，結果可重現。
- 介面不選關聯事件，基準件數手動填寫；頁首與每次結果都標示「假設情境，非成效預測」。
- v1.0 的「時間窗、地點」輸入沒有實作（D29）。

---

## §9 資料庫與核心欄位

v1.0 的 16 張表中，15 張依規格建立；`reports` 改為 `anonymous_reports`。另新增 10 張資料表與 2 個 materialized views，共 26 張資料表。所有變更經 Alembic migration（0001～0018），皆可 downgrade。

| 表 | v1.1 | migration |
| --- | --- | --- |
| sources、ingestion_runs、source_items、source_item_versions、events、event_evidence、locations、audit_logs | 依 v1.0；audit_logs 以 trigger 禁止 UPDATE／DELETE | 0001、0002 |
| ingestion_dead_letters | 新增：擷取死信 | 0003 |
| source_items.tags | 新增欄位：來源分類標籤 | 0004 |
| ai_analysis_runs、risk_assessments、event_relations | 依 v1.0 | 0005 |
| events 向量與日期欄位 | 新增：occurrence_start_date、pgvector 嵌入（S04 候選召回） | 0006 |
| users、user_sessions | 新增：單一使用者登入 | 0007 |
| alerts、alert_deliveries | 依 v1.0 | 0008 |
| source_items 分析狀態 | 新增欄位：analysis_status、attempts、queued_at | 0009 |
| events 摘要欄位 | 新增：summary 與證據、來源、時間 | 0010 |
| event_reports | 新增：事件小報告（保留歷史，失效不刪除） | 0011 |
| response_actions、response_action_transitions | 依 v1.0（`proof` 為佐證），另加轉移歷史表 | 0012 |
| publications、publication_versions、publication_takedowns | 新增：公開狀態、已遮蔽快照、下架紀錄 | 0013 |
| anonymous_reports | **取代 v1.0 的 reports**：只有 id、content、location、submitted_at；沒有 receipt_hash、沒有加密 | 0014 |
| event_relations 決策欄位 | 新增：method、decision、decided_at、decided_by、decision_note；type 加 hypothesized_cause | 0015 |
| analytics_event_counts_week／_month | 新增：materialized views | 0016 |
| scenario_runs | 依 v1.0，另有 seed、ai_run_id、created_by | 0017 |
| report_cause_decisions | 新增：人工確認原因（append-only） | 0018 |

### §9.1 重要關係與一致性

不變。原始內容與公開內容以不同資料表隔離：公開 API 只讀 `publication_versions` 的快照，不讀 events、source_items、event_reports 等原始資料表。

### §9.2 事件抽取輸出 Schema

v1.0 的單一摘要 schema 不採用，改由各 Skill 的 `schemas/output.schema.json` 分別定義（S02 為 strict，所有欄位必填）。對照：event_type → S03 `category`；title、occurrence_time、location、summary → S02；verification_status → S05 建議＋查證規則；classification_confidence → S03；risk_dimensions → S06；possible_causes → S07；recommendations → S08；publication_eligibility → 公開規則引擎（不由模型輸出）；schema_version → 每個 Skill 的 `metadata.schema_version`。

---

## §10 API 與權限規格

| 方法 | v1.0 路由 | v1.1 路由 | 權限 |
| --- | --- | --- | --- |
| GET | `/api/v1/events` | `/api/v1/public/events`（`type`、`limit`、`cursor`） | 免登入 |
| GET | `/api/v1/events/{id}` | `/api/v1/public/events/{id}`（id 為 publication ID） | 免登入 |
| GET | `/api/v1/alerts` | `/api/v1/public/notices`、`/api/v1/public/feed` | 免登入 |
| POST | `/api/v1/reports` | `/api/v1/public/reports`（202，不回收據碼） | 免登入、限流 |
| POST | `/api/v1/reports/{token}/followups` | **不提供** | — |
| GET | `/api/v1/dashboard/summary` | 公開：`/api/v1/public/categories`、`/api/v1/public/resilience`；後台：`/api/v1/admin/dashboard` | 免登入／`events:read_all` |
| — | — | 新增 `/api/v1/public/progress` | 免登入 |
| GET | `/api/v1/admin/events` | 不變，另有 `GET /admin/events/{id}` | `events:read_all` |
| POST | `/api/v1/admin/events/{id}/verify` | 不變 | `events:verify` |
| POST | `/api/v1/admin/events/{id}/analyze` | 不變 | `events:analyze` |
| POST | `/api/v1/admin/alerts/{id}/retract` | 不變，另有 `GET /admin/alerts` | `alerts:retract` |
| POST | `/api/v1/admin/scenarios` | 不變，另有 `GET /admin/scenarios`、`/{id}` | `scenarios:create` |
| GET | `/api/v1/admin/ingestion-runs` | 不變，另有 `/health` | `ingestion_runs:read` |
| GET | `/api/v1/admin/audit-logs` | 不變 | `audit_logs:read`（敏感讀取，寫入稽核） |

新增的管理端路由（全部在 `/api/v1/admin/*`，需登入，不經通道）：events 的 `report`、`report/causes/{cause_id}/decision`、`actions`；`actions`（列表、詳細、PATCH、transitions）；`recurrences`；`relations`（列表、詳細、decision）；`publications`（列表、takedown、restore）；`resilience`；`trends/forecast`（GET／POST）；`skills`。另有 `/api/v1/auth/login`、`logout`、`me` 與 `/api/v1/health`。各端點權限見 `docs/architecture/rbac.md`。

### §10.1 標準 API 行為

不變：`{data, meta, error}`、cursor 分頁、HttpOnly Cookie session、CSRF、寫入產生 request_id 並寫稽核、冪等鍵。錯誤碼在 v1.0 七種之外另加 `INTERNAL_ERROR`（未預期例外，不帶細節）與 `METHOD_NOT_ALLOWED`。公開端另有：每用戶端 60 秒 60 次限流、`Cache-Control: public, max-age=30` 與弱 ETag、錯誤 `no-store`；`/docs` 預設關閉。

---

## §11 前端頁面與互動規格

| 介面 | v1.0 頁面 | v1.1 頁面 |
| --- | --- | --- |
| 公開 | 首頁 | `Home`：最新動態時間軸，可切換只看安全提醒 |
| 公開 | 事件列表／詳細 | `Events`、`EventReport`（公開版小報告：影響、建議措施、官方來源、改善進度） |
| 公開 | 通報頁 | `Report`：匿名表單、緊急提示；沒有收據碼與上傳 |
| 公開 | 公開報告 | `Resilience`（韌性成績單，標明「回報變多不等於校園變危險」）、`Progress`（改善進度）、`About`（非官方聲明、資料來源、下架說明） |
| 管理 | 事件工作台 | `EventWorkbench`：來源對照、風險分項、證據、事件說明、事件小報告、確認原因、公開狀態、加入改善追蹤 |
| 管理 | 警示中心 | `AlertCenter`：個人通知、疑似指控標記、再發生提醒；`Publications`：公開內容與一鍵下架 |
| 管理 | Skills 管理 | `SkillsDashboard`：版本、近 7 天執行統計、評測摘要 |
| 管理 | 關聯與模擬 | `RelationGraph`（`/admin/relations`）、`Scenario`（`/admin/scenarios`） |
| 管理 | 維運與稽核 | `Ops`：來源健康、擷取紀錄、稽核查詢 |
| 管理 | — | 新增 `ResponseActions`（改善追蹤）、`Resilience`（成績單與趨勢解讀） |

兩套設計系統都必須建置成功；風險等級與緊急資訊不得只靠顏色區分、不加裝飾動畫（ADR-0014）。

---

## §12 非功能需求與安全治理

| 項目 | v1.0 初始目標 | v1.1 |
| --- | --- | --- |
| 可用性 | 公開端 99.5% 月可用性 | **不設目標**：服務隨使用者電腦開關機；Quick Tunnel 網址每次啟動都會改變 |
| 效能 | 公開列表 p95 < 1.5 秒 | **不驗證**：負載測試與 G5-3 已刪除（ADR-0008）；以限流、快取標頭與 `limit ≤ 50` 控制 |
| 排程 | 擷取成功率 98% | 98% 不變，但以一晚（≥ 12 小時）網路正常時段計算（ADR-0006、ADR-0012） |
| 可靠性 | 重試、死信、冪等、每日備份 | 重試、死信、冪等完成；備份為手動執行的 `pg_dump` 腳本，**沒有每日排程**（使用者決定不併入 P5-T06）；還原演練通過（G5-2） |
| 權限 | 公開／管理隔離、最小權限 | 隔離靠公開閘道白名單與 127.0.0.1 綁定；未登入只能存取 health 與 `/api/v1/public/*` |
| 隱私 | 最小化、脫敏、保留與刪除流程 | 最小化與 PII 遮蔽完成；**沒有保留期限與刪除流程**；日誌不含個資、原文、金鑰 |
| 觀測 | 成功率、AI 延遲與費用、通知失敗率、trace id | Prometheus 指標（任務、AI 延遲與費用、HTTP、限流次數）、Grafana、7 條告警規則、trace_id 貫穿 Celery |
| 模型治理 | 版本化、防注入、schema 驗證 | 不變；另有對抗測試（`tests/security`）與錄製回應評測 |
| 可攜性 | Agent Skills、模型 Adapter | 不變；P7-T04 驗證（§5.1） |

---

## §13 分階段開發計畫

v1.0 的 6 個階段改為 8 個（ADR-0015 第 9 點）；任務與閘門以 `docs/plan/execution-plan.md` 和 `docs/progress/phases.yaml` 為準。

| 階段 | v1.1 目標 | 2026-11-05 狀態 |
| --- | --- | --- |
| P0 基線建置 | 乾淨 clone 一鍵啟動、CI | 完成 |
| P1 採集與來源追溯 | 3 個以上合法來源一晚穩定擷取、零重複、可反查 | 完成 |
| P2 AI 核心 | Skill 基礎設施＋S01～S08 達門檻 | 完成 |
| P3 產品與個人通知（MVP） | 個人版最小端到端 | 完成 |
| P4 真實資料、事件小報告與追蹤到底 | 真實資料每天產生事件、小報告、改善追蹤、再發生 | 完成 |
| P5 公開網站與上線 | 公開規則、公開 API 與前端、匿名回報、下架、觀測、備份、本機常駐、通道 | 完成 |
| P6 把點連成線與韌性成績單 | 關聯、趨勢、回測、CRI | 任務完成；G6-1 待審閱 |
| P7 What-if 模擬與收尾 | S09、S10、What-if、可攜性、規格書 v1.1、S09 管線、人工確認原因 | 本任務進行中；G7-1 待執行 |

v1.0 的 P4（關聯）、P5（模擬與部署維運）分別成為 v1.1 的 P6、P7；部署維運移到 P5。

### §13.1 最小可展示端到端版本

先以個人版完成（G3-5，附錄 A 案例資料），再以真實資料完成（G4-8：事件列表 → 事件小報告 → 改善追蹤 → 再發生提醒），之後才公開。社群資料不阻塞這條路徑（v1.0 不變）。

---

## §14 測試策略與驗證指標

| 類別 | v1.0 門檻 | v1.1 門檻與驗證 | 依據 |
| --- | --- | --- | --- |
| 採集 | 連續七天成功率 98%、無重複 | 一晚（≥ 12 小時）網路正常時段成功率 ≥ 98%（人工閘門）；重跑零重複 | ADR-0006、ADR-0012 |
| 相關性（新增） | — | S01 F1 ≥ 0.90 | P2 門檻表 |
| 抽取（新增） | — | S02 時間、地點正確率 ≥ 0.85 | P2 門檻表 |
| 分類 | 人工標記測試集 Macro-F1 0.85 | S03 Macro-F1 ≥ 0.85，以 **golden cases test split** 評測 | D33 |
| 同事件合併 | P 0.95、R 0.85（事件分組切分） | S04 same_event P ≥ 0.95、R ≥ 0.85，以配對式 golden cases 評測 | `eval/thresholds.yaml` |
| 查證（新增） | — | S05 無證據被標 verified 次數 = 0 | A08 |
| 高風險召回 | Recall 0.95 | S06 high_risk recall ≥ 0.95；同輸入結果重現 100% | 不變 |
| 假設與事實區分（新增） | — | S07／S08 basis 正確率 ≥ 0.90；另經人工審閱（60／60） | P2-T12 |
| 趨勢解讀（新增） | — | S09 explained_signals ≥ 0.90、caveats_complete = 1.0 | P7-T01 |
| 情境解釋（新增） | — | S10 explained_measures、key_driver、limitation_set ≥ 0.90；hypothetical_flagged = 1.0 | P7-T02 |
| 結構化輸出 | 99% 合格 | 不變（全部 Skill） | |
| 來源追溯 | 100% 可追溯或 abstain | 不變（G2-2） | |
| 自動發布 | 對抗測試，未核實具體指控錯誤公開 0 | 疑似指控、推測原因、FB 原文、投稿原文不出現在公開端（G5-5，`test_public_exposure`、`test_anonymous_report`）；原 G3-1 已刪除 | ADR-0008、ADR-0015 |
| 通知 | 相同版本無重複外送 | 不變（G3-2） | |
| 效能 | p95 < 1.5 秒 | **刪除**（G5-3） | ADR-0008 |
| 關聯（新增） | — | 人工審閱全部現有關聯，正確率 ≥ 0.85（G6-1） | ADR-0016 |
| 回測（新增） | — | 不使用未來資料（G6-2） | |
| 韌性成績單（新增） | — | 資料不足顯示 N/A，不假精確（G6-3） | |
| 模擬 | 相同參數／版本／種子可重現 | 不變（G7-1） | |
| 復原 | 可完成完整復原 | 不變（G5-2，還原演練） | |
| 下架（新增） | — | 即時生效並留稽核（G5-7） | |

評測先以真實模型錄製回應，CI 以錄製回應重跑。使用者決定不做模型比較，也不用 `data/labeled/` 的真實標記資料評測 S01／S03（2026-10-21）。

### §14.1 測試資料策略

- 標記樣本由 300～500 筆降為 **≥ 100 筆**（G1-5；實際 111 筆，dev 78、test 33），單層 9 類（facility、weather、security、traffic、health、fire、academic、infosec、none），標記檔只存文章 ID、內容雜湊與標記，不存內文（ADR-0009）。
- 樣本以官方公告為主，類別不平衡（none 占多數）。
- Skill 評測以各 Skill 的 golden cases 為準（至少 20 筆，含提示注入、PII、無證據等對抗案例），分 dev／test。

### §14.2 驗收紀錄

每個任務以 `tracker.py record <ID>` 產生驗收紀錄，每個階段以 `record P?` 產生閘門紀錄（`docs/progress/records/`）。人工閘門必須由使用者本人確認（ADR-0002）。

---

## §15 校園韌性指標

公式不變：CRI = 0.25 發現力 + 0.25 行動力 + 0.25 恢復力 + 0.25 進步力（Detection、Response、Recovery、Adaptation）。實際定義（P6-T05，使用者 2026-10-28 同意；參數 `CriParams`）：

| 構面 | 指標 | 換算（線性，錨點為 100 分／0 分） |
| --- | --- | --- |
| 發現力 | 事件建立日 − S02 發生日的中位數（負值算 0） | 1 天／14 天 |
| 行動力 | 事件建立到第一項改善措施進入處理的中位數 | 1 天／30 天 |
| 恢復力 | 措施第一次處理到之後第一次恢復的中位數 | 3 天／60 天 |
| 進步力 | 改善完成率（改進 ÷ 不含不追蹤）與 1 − 重複事件比例的平均 | 比例直接換算 |

- 任何指標樣本 < 5 → 該構面 N/A；四構面齊全才算 CRI，不重新加權；分數取整數。
- 時間窗後台可選 7～730 天，公開版固定 180 天，只統計以事件內容公開中的事件。
- 公開版呈現樣本量、覆蓋率、時間窗與限制說明；醒目標明「回報變多不等於校園變危險」（v1.0 不變）。

---

## §16 風險登錄與緩解措施

v1.0 的 8 項保留，對策更新；另新增 5 項。

| 風險 | 嚴重性 | v1.1 對策 |
| --- | --- | --- |
| 社群資料無合法機器存取管道 | 高 | Facebook 由使用者自己的瀏覽器每天讀一次，只讀不互動，後端不存登入資料；**帳號被停用的風險仍在**，由使用者承擔（ADR-0008） |
| 匿名惡意通報與公開誤報 | 極高 | 公開規則硬性邊界、投稿限流、一鍵下架；以投稿灌出 verified 仍可能觸發個人通知（使用者 2026-10-28 決定維持現狀） |
| 模型幻覺或將相關當因果 | 高 | evidence_ids、abstain、推測原因不公開；人工確認原因的依據不足時可能公開錯誤措施（ADR-0017） |
| PII、附件與原文外洩 | 極高 | 不收附件；原文不公開；`mask_pii()`；**不辨識人名與地址**，靠指控規則與 S02 規則擋下 |
| 模型成本與延遲上升 | 中 | 每次 US$0.05、每日 US$1.0 上限；超過即暫停分析 |
| 來源站版型改版 | 中 | `LayoutChangedError`、來源健康 API、告警規則 |
| 缺少真實資料驗證 What-if | 高 | 不變：標為假設情境，提供敏感度與限制 |
| 只有開發者管理、無校方窗口 | 中 | 每頁標明非官方；不寄信給處室 |
| **新增：公開內容的法律責任** | 極高 | 妨害名譽、個資法責任由使用者承擔；上線前使用者審閱威脅模型（G5-8） |
| **新增：校名使用** | 中 | 非官方聲明；校方要求時改名或下線 |
| **新增：管理端外露** | 高 | 管理端只綁 127.0.0.1；公開閘道白名單、清掉 Cookie／Authorization；`tunnel.sh check` 驗證 |
| **新增：公開端被洪水請求** | 中 | 行程內限流、閘道每秒 10 次限速、快取標頭 |
| **新增：小樣本評測代表性不足** | 中 | 評測報告與驗收紀錄註明樣本數；關聯審閱只有 2 組時暫不判定（ADR-0016） |

---

## §17 技術決策與未來轉正式導入

- 模組化單體不變（ADR-0001）。
- **部署**：使用者電腦上的 Docker Compose 常駐運行（`restart: unless-stopped`、日誌輪替、主機埠只綁 127.0.0.1），經 Cloudflare Quick Tunnel 對外，不使用網域；通道只連到公開閘道（`docs/runbooks/deploy.md`、`local-operation.md`）。v1.0 的負載均衡、異地備份與金鑰管理服務不在範圍內。
- 正式導入的前提不變（校方窗口、法遵審查、發布權責、申訴、資料治理協議），另加：取得各處室網站的書面再利用同意（ADR-0007）、Facebook 改為有授權的取得方式或移除、匿名回報補上保留期限與刪除流程。

§17.1 施工順序已全部完成。

---

## §18 參考資料

v1.0 列出的參考保留，另加：

- 政府資料開放授權條款－第 1 版：https://data.gov.tw/license（cwa-alerts）
- 中央氣象署開放資料平台：https://opendata.cwa.gov.tw/
- Cloudflare Quick Tunnel（TryCloudflare）：設定與上線步驟見 `docs/runbooks/deploy.md`

Meta 已在 2024 年移除 Graph API 的 Groups API，v1.0 引用的 Graph API 不再是可用的社團取得方式（ADR-0008）。Dcard 仍為待決定，沒有使用。

---

## 附錄 A：首批測試案例（v1.1 期望結果）

A01～A08 由 `make e2e`（真實全端，`tests/e2e/`）驗證（G3-4），公開部分由 G5-5、G5-6 驗證。

| 案例 | v1.1 期望結果 |
| --- | --- |
| A01 官方施工公告 | 建立已核實事件、分類 facility；公開事件內容與官方來源連結 |
| A02 社群單篇失竊指控 | 未核實；可產生中性防竊提醒（指控命中時不寫地點），不公開指控 |
| A03 三則相同失竊新聞轉載 | 事件數 1、證據數 3 |
| A04 兩起不同日失竊 | 建立不同事件（related_event 由關聯圖呈現） |
| A05 惡意誣指個人 | 改為社群貼文；通知不呈現指控、管理端標記「疑似指控，需人工判斷」；公開端不出現內容 |
| A06 官方宣布澄清 | 查證改判；個人通知自動撤回；公開內容撤下 |
| A07 重送相同任務 | 原文、事件、通知冪等 |
| A08 模型輸出無 evidence_ids | 不得標記 verified |
| A09 What-if 效果值全為未知 | 引擎回傳 not_estimable，介面顯示「無法估計」，不呼叫 S10（`tests/unit/simulations/test_engine.py`） |
| A10 儲存中斷或通知失敗 | 遞送以唯一約束佔位，失敗記為 failed、**不自動重試**；資料以備份還原（G5-2）。沒有獨立的端到端案例（D41） |

## 附錄 B：專案組織結構

以 `docs/architecture/project-structure.md` 為準。與 v1.0 的差異：前端沒有 `api/` 與 `components/` 頂層目錄，改為 `src/admin/`、`src/public/`、`src/design/{minimal,playful}/`；後端另有 `app/pipeline/`、`app/verification/`、`app/publishing/`、`app/reports/`、`app/relations/`、`app/analytics/`、`app/resilience/`、`app/labeling/`；開發用 skill 在 `.claude/skills/`（ADR-0008）；`infra/` 另有 `tunnel/`、`launchd/`、`scripts/`；`eval/` 存門檻與評測報告；`docs/` 另有 `decisions/`、`plan/`、`progress/`、`runbooks/`。

---

## 19. v1.0 → v1.1 差異清單

類型：**定位**（系統定位或範圍）、**來源**、**功能**、**schema**、**門檻**、**部署**、**文件**。

| # | 章節 | 類型 | v1.0 | v1.1 | 依據 |
| --- | --- | --- | --- | --- | --- |
| D01 | §0、§2 | 定位 | 後台給開發團隊，公開介面給一般使用者 | 2026-10-18 改為個人使用、刪除對外功能；2026-10-27 重新對外公開唯讀內容，管理端只有使用者一人 | ADR-0008、ADR-0015 |
| D02 | §0、§6.4 | 定位 | AI 判為高風險即自動對外警示 | 個人通知與公開內容分開；公開由確定性規則判斷，硬性邊界寫在程式裡、設定無法關閉 | ADR-0015 |
| D03 | §0、§1.3 | 定位 | 正式導入與專題並重 | 專題展示為主；不寄信給任何處室，只顯示建議轉給哪個單位 | ADR-0015 第 4 點 |
| D04 | §0、§1.3 | 定位 | — | 每頁標明「個人專題，非國立中央大學官方系統」與緊急聯絡方式 | ADR-0015 第 7 點 |
| D05 | §0.2 | 來源 | 校內公告、社群、新聞、匿名通報 | 12 個來源上線（9 個校方、氣象署、Facebook、匿名回報） | source-inventory、`config/sources.yaml` |
| D06 | §0.2 | 來源 | 新聞媒體 | 外部新聞媒體不核准；中央大學新聞網為主要新聞來源 | ADR-0007 |
| D07 | §0.2 | 來源 | RSS | 氣象署 RSS 不核准，改用開放資料 API；RSS Adapter 保留但沒有來源 | ADR-0005 |
| D08 | §0.2、§16 | 來源 | 社群無合法機器存取管道，需授權 | Facebook 社團以使用者的瀏覽器每天讀一次，只讀不互動、不存發文者與登入資料；公開端只顯示 AI 摘要 | ADR-0008、ADR-0015 |
| D09 | §0.2、F02 | 來源 | 來源 URL 可反查 | Facebook 以內文雜湊為識別碼，只能追溯到社團與發文時間（試行） | ADR-0011 |
| D10 | §0.2 | 來源 | — | 總務處暫緩；校方來源頻率改為一天兩次 | ADR-0010、使用者 2026-11-04 |
| D11 | §2 | 定位 | 匿名投稿者可取得收據碼並補件 | 只能送出，沒有收據碼、查詢與補件 | P5-T13 |
| D12 | §2 | 定位 | 開發團隊多人 RBAC | 單一使用者登入；RBAC 與權限名稱保留 | ADR-0008、P3-T01 |
| D13 | §3 | 部署 | 物件儲存（授權附件） | 不使用；系統不接受附件 | P5-T13 |
| D14 | §3、§17 | 部署 | 正式部署加負載均衡、備份、監控、金鑰管理 | 使用者電腦常駐＋Cloudflare Quick Tunnel＋公開閘道白名單；管理端只綁 127.0.0.1 | ADR-0015 第 6 點、P5-T06、P5-T15 |
| D15 | §3、§11 | 功能 | 單一設計 | 雙設計系統，建置時選擇 | ADR-0003、ADR-0014 |
| D16 | §3.1 | 功能 | 擷取後直接分析 | 每分鐘掃描未分析文章，只分析近 14 天、受每日費用上限控制 | P4-T07 |
| D17 | §4 F06 | 功能 | 根因假設、方案、責任建議 | 事件小報告；推測原因明確標示「推測，尚未查證」 | ADR-0015、P4-T09 |
| D18 | §4、§6.4 | 功能 | 推測原因相關內容不公開（隱含） | 推測原因可由使用者填確認依據後人工確認，相關措施可公開；原因本身不公開、公開頁不標示 | ADR-0017 |
| D19 | §7、§9 | schema | reports：receipt_hash、text_encrypted；原文加密；保留與刪除流程 | anonymous_reports：只有內容、地點、時間；未加密、不刪除；不存 IP | P5-T13 |
| D20 | §7 | 功能 | CAPTCHA、附件白名單、惡意檔案檢測、同文偵測 | 只有速率限制與格式限制 | P5-T13 |
| D21 | §6.4 | 功能 | 爭議處理聯絡管道、回收 SLA、錯誤通知 | 一鍵下架即時生效、每分鐘同步、快取 30 秒；沒有對外申訴管道 | P5-T14 |
| D22 | §4、§6.3 | 功能 | — | 新增事件說明（摘要）、再發生提醒、改善措施狀態機 | P4-T08、P4-T10、P4-T05 |
| D23 | §4 F07、§10 | 功能 | 公開聚合統計 `/dashboard/summary` | 後台儀表板＋公開類別統計與韌性成績單 | P3-T03、P5-T11、P6-T05 |
| D24 | §4 F03 | 功能 | 手動拆分與合併 | 未實作；同事件合併只由 S04 決定 | — |
| D25 | §5、§5.1 | schema | `campus-risk-assessor/`、`risk-output.schema.json`、`cases.json` | `skills/<name>/` 標準結構、input／output 兩份 schema、`cases.jsonl`＋錄製回應；`_template` 示範模板 | skill-standard、ADR-0013 |
| D26 | §5 | 功能 | Skills 可移交其他客戶端 | 已驗證：Claude Code 跑 161 筆通過 156 筆 | P7-T04 |
| D27 | §8.2 | 功能 | 移動平均、EWMA 或 Poisson；納入學期、假期、通報習慣 | EWMA＋Poisson 只偵測突增；資料覆蓋率由程式計算；學期與假期未納入模型；S09 只手動觸發 | P6-T03、P7-T01、P7-T06 |
| D28 | §8.1 | 功能 | 邊存人工決策 | method、decision、回滾；沒有產生 hypothesized_cause 的流程、沒有手動新增端點 | P6-T01 |
| D29 | §8.3 | 功能 | 輸入含時間窗、地點 | 未實作；介面不選關聯事件、基準手動填寫；Monte Carlo 分位數與成本分級門檻 | P7-T02、P7-T03 |
| D30 | §6.1 | 門檻 | 權重、分級、critical_override | 不變；新增保底「任一面向 ≥ 80 → 至少 high」 | P2-T11、`config/risk-policy.yaml` |
| D31 | §6.2、§6.3、§5 | schema | S05 輸出 `confirmed` | 統一為 `verified` | state-machines.md |
| D32 | §6.3 | schema | 事件、警示狀態 | 查證可互相改判、`resolved → monitoring`、警示 `blocked`；新增公開、改善措施、關聯決策狀態 | state-machines.md、P5-T10、P4-T05、P6-T01 |
| D33 | §14 | 門檻 | 分類以人工標記測試集評 Macro-F1 | 以 golden cases test split 評測；不使用 `data/labeled/` 評測 | 使用者 2026-10-21 |
| D34 | §14 | 門檻 | 七天擷取成功率 98% | 一晚（≥ 12 小時）、只計網路正常時段，改為人工閘門 | ADR-0006、ADR-0012 |
| D35 | §14.1 | 門檻 | 300～500 筆標記樣本 | ≥ 100 筆（實際 111 筆），9 類，不存內文 | ADR-0009 |
| D36 | §14 | 門檻 | 效能 p95 < 1.5 秒 | 刪除 | ADR-0008 |
| D37 | §14 | 門檻 | 未核實具體指控錯誤公開 0 | 改為 G5-5：疑似指控、推測原因、FB 原文、投稿原文不出現在公開端 | ADR-0008、ADR-0015 |
| D38 | §14 | 門檻 | — | 新增 S01、S02、S05、S07／S08、S09、S10 門檻與 G6-1（全部現有關聯 ≥ 0.85）、G6-2、G6-3、G5-7 | `eval/thresholds.yaml`、ADR-0016 |
| D39 | §12 | 部署 | 99.5% 可用性；每日備份；保留期限與刪除流程 | 不設可用性目標；備份手動執行、無排程；沒有保留期限與刪除流程 | ADR-0015、P5-T05、P5-T06 |
| D40 | §12 | 部署 | 觀測項目 | Prometheus、Grafana、7 條告警、trace_id | P5-T04 |
| D41 | 附錄 A | 門檻 | A01～A10 | A05 改為社群貼文、加上公開端期望；A09 由引擎測試驗證；A10 通知失敗不重試、沒有獨立案例 | P3-T10、P7-T02 |
| D42 | §9 | schema | 16 張表 | 新增 10 張表、2 個 materialized views 與多個欄位（migration 0001～0018） | §9 表格 |
| D43 | §9.2 | schema | 單一事件抽取摘要 schema | 各 Skill 各自的 output schema；publication_eligibility 由公開規則引擎決定 | skill-standard |
| D44 | §10、§10.1 | schema | 公開路由在 `/api/v1/*` | 公開路由移到 `/api/v1/public/*`；新增多個管理端路由；錯誤碼加 INTERNAL_ERROR、METHOD_NOT_ALLOWED | P5-T11、rbac.md |
| D45 | §11 | 功能 | 公開 4 頁、管理 5 頁 | 公開 7 頁、管理 10 頁（含登入） | 前端 `pages/` |
| D46 | §13 | 文件 | 6 個階段 | 8 個階段，關聯／模擬後移、公開網站獨立成 P5 | ADR-0015 第 9 點 |
| D47 | §15 | 門檻 | CRI 四構面、資料不足 N/A | 指標、錨點、樣本 < 5 → N/A、公開固定 180 天 | P6-T05 |
| D48 | §16 | 文件 | 8 項風險 | 對策更新，新增法律責任、校名、管理端外露、洪水請求、小樣本 5 項 | ADR-0015 |
| D49 | §18 | 文件 | 參考資料 | 加入政府資料開放授權、氣象署平台、Cloudflare Quick Tunnel；註明 Groups API 已移除 | ADR-0005、ADR-0008、P5-T15 |
| D50 | 附錄 B | 文件 | 建議目錄 | 以 project-structure.md 為準，新增多個模組與目錄 | project-structure.md |
| D51 | §0 | 文件 | 交付 PDF | v1.1 以 Markdown 交付，PDF 不修改 | 本任務 |

### 19.1 ADR-0008 與 ADR-0015 的定位變更對照

| 項目 | v1.0 | ADR-0008（10-18） | ADR-0015（10-27，目前有效） |
| --- | --- | --- | --- |
| 公開網站與 API | 有 | 刪除 | 重新加入，只讀、白名單 |
| 匿名通報 | 有（收據碼、補件） | 刪除 | 匿名回報（無收據碼） |
| 自動對外發布 | AI 判高風險即發 | 刪除 | 確定性規則＋硬性邊界 |
| 通知對象 | 對外 | 使用者本人 | 使用者本人（不變） |
| 管理者 | 開發團隊 | 使用者一人 | 使用者一人（不變） |
| Facebook | 需授權 | 核准，本機分析 | 核准，公開端只顯示 AI 摘要 |
| 部署 | 正式部署、負載測試 | 本機常駐 | 本機常駐＋通道對外 |
| 負載測試 | 有 | 刪除 | 仍刪除 |
| 刪除的任務與閘門 | — | P3-T02、P3-T04、P3-T06、P3-T09、P5-T07、G3-1、G5-3 | 不還原；公開功能以 P5-T10～T15 重建 |

## 20. 逐章檢查（完整性）

| v1.0 章節 | 是否變更 | 差異項 |
| --- | --- | --- |
| §0 文件說明與決策紀錄 | 變更 | D01～D05、D51 |
| §1.1 核心問題、§1.2 系統目標 | 不變 | — |
| §1.3 範圍與非目標 | 增補 | D03、D04 |
| §2 使用者與權限 | 變更 | D01、D11、D12 |
| §2.1 關鍵使用情境 | 期望結果調整 | D02 |
| §3 系統整體架構 | 變更 | D13、D14、D15 |
| §3.1 端到端資料流程 | 增補 | D16 |
| §4 F01～F10 | 變更 | D09、D17、D18、D22、D23、D24 |
| §5 可攜式 AI Skills | 變更 | D25、D31 |
| §5.1 Skill 封裝與跨 AI 共用 | 變更 | D25、D26 |
| §5.2 執行治理 | 不變（另加 CI 規則） | — |
| §6.1 五維風險模型 | 增補 | D30 |
| §6.2 獨立三種判斷 | 用詞統一 | D31 |
| §6.3 生命週期 | 變更 | D32 |
| §6.4 自動公開策略 | 取代 | D02、D18、D21 |
| §7 匿名通報與防護 | 變更 | D11、D19、D20 |
| §8.1 關聯分類 | 增補 | D28 |
| §8.2 趨勢預警 | 變更 | D27 |
| §8.3 情境模擬 | 增補 | D29 |
| §9 資料庫 | 變更 | D19、D42 |
| §9.1 關係與一致性 | 不變 | — |
| §9.2 事件抽取 schema | 取代 | D43 |
| §10 API | 變更 | D23、D44 |
| §10.1 標準 API 行為 | 增補 | D44 |
| §11 前端頁面 | 變更 | D15、D45 |
| §12 非功能需求 | 變更 | D36、D39、D40 |
| §13 分階段計畫 | 變更 | D46 |
| §13.1 最小可展示版本 | 變更 | D46 |
| §14 測試與指標 | 變更 | D33、D34、D36、D37、D38 |
| §14.1 測試資料 | 變更 | D35 |
| §14.2 驗收紀錄模板 | 不變（改由 tracker 產生） | — |
| §15 韌性指標 | 增補 | D47 |
| §16 風險登錄 | 變更 | D08、D48 |
| §17 技術決策與正式導入 | 變更 | D14 |
| §17.1 施工順序 | 不變（已完成） | — |
| §18 參考資料 | 增補 | D49 |
| 附錄 A 測試案例 | 變更 | D41 |
| 附錄 B 專案結構 | 變更 | D50 |

ADR 對照：ADR-0001（§3，不變）、0002（§14.2）、0003／0014（D15）、0004（§10.1 稽核強化，未改變規格）、0005（D07）、0006（D34）、0007（D06）、0008（D01、D08、D12、D36、D37）、0009（D35）、0010（D10）、0011（D09）、0012（D34）、0013（D25）、0015（D01～D04、D14、D46）、0016（D38）、0017（D18）。17 份 ADR 都已對應。
