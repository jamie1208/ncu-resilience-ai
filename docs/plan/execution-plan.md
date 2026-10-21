# NCU Resilience AI 分階段執行計畫

> 本檔是 coding agent 讀取任務定義的依據，內容與線上文件同步。
> - 規格書：`docs/requirements/NCU_Resilience_AI_System_Plan_v1.pdf`
> - 機器可讀的任務／閘門定義：`.claude/skills/ncu-resilience-tracker/references/phases.yaml`
> - 權威進度：`docs/progress/state.yaml`
>
> 修改計畫時三處要一起改：本檔、phases.yaml（或其覆寫檔 `docs/progress/phases.yaml`）、ADR。

全案 6 個階段、62 個任務、24 道閘門。任務 ID 為 `P{階段}-T{序號}`，閘門 ID 為 `G{階段}-{序號}`。Migration 檔名以實際的下一個可用編號為準（ADR-0009），計畫中寫成 `<下一個編號>_<名稱>.py`。標記「需你決定」的任務，AI 只能準備資料，必須由使用者本人定案。

**個人使用（ADR-0008）**：本系統只給使用者本人使用，不提供公開網站、公開 API、匿名通報或任何對外發布管道。已刪除的任務與閘門（P3-T02、P3-T04、P3-T06、P3-T09、P5-T07、G3-1、G5-3）不重新編號，ID 也不重複使用。

---

## Skill 開發標準循環（S01–S10 共用）

1. **定義 I/O**：寫 `schemas/input.schema.json` 與 `schemas/output.schema.json`；列舉值、數值範圍、`evidence_ids` 必填、允許 `abstain`。
2. **寫 SKILL.md**：frontmatter 含 `name`、`description`、`metadata.version`、`metadata.schema_version`；本文章節為「何時使用、輸入、步驟、證據優先序、禁止事項、Abstain／Fallback、輸出範例」。
3. **寫 references**：分類法、政策、判斷準則等長內容放 `references/`，SKILL.md 只放指引。
4. **寫 golden cases**：`tests/cases.jsonl` 至少 20 筆，含正常、邊界、對抗（提示注入、PII、無證據）三類；每筆有 `expected` 與 `must_not`，對抗案例加 `"type": "adversarial"`。
5. **靜態驗證**：`python scripts/skills/validate_skill.py skills/<name>`。
6. **評測**：先以錄製回應跑 CI，再以真實模型跑 `scripts/eval/run_eval.py --skill <name> --split test --live`，報告寫入 `eval/reports/<name>/<version>.json`。
7. **凍結**：達標後升版本號，把報告路徑寫進驗收紀錄；未達標回到步驟 2–4 迭代，並記錄每輪改了什麼。

**Skill 完成定義**：靜態驗證通過；golden cases 全過；評測達門檻；輸出 100% 通過 schema 或明確 abstain；評測報告與版本已記錄。

```text
skills/<skill-name>/
  SKILL.md
  schemas/input.schema.json
  schemas/output.schema.json
  references/*.md
  scripts/            # 可選：確定性計算，如 calculate_risk.py
  tests/cases.jsonl
  tests/recorded/     # CI 用錄製回應
```

---

## P0 基線建置

目標：從乾淨的 clone 一鍵跑起整個系統，CI 能擋下壞掉的變更。前置：無。

- **P0-T01 Repo 與 AI 工作規範**
  - 檔案：`README.md`、`AGENTS.md`、`CLAUDE.md`、`.gitignore`、`.editorconfig`、`docs/decisions/ADR-0001-architecture.md`
  - 驗證：AGENTS.md 含「先寫測試、不改測試遷就實作、不寫入金鑰、產品 skills 與開發 skills 分離」四條
  - 備註：骨架已預先建立這些檔案，本任務為審閱、補強、`git init` 並完成第一次 commit
- **P0-T02 後端骨架（FastAPI）**
  - 檔案：`backend/pyproject.toml`（uv 管理）、`backend/app/main.py`、`app/core/config.py`（pydantic-settings）、`app/core/logging.py`、`app/api/v1/health.py`、`backend/tests/unit/test_health.py`
  - 驗證：`cd backend && uv run pytest tests/unit/test_health.py`
- **P0-T03 前端骨架（React + TS）**
  - 檔案：`frontend/`（Vite react-ts）、`src/public/`、`src/admin/`、`src/components/`、`src/api/client.ts`
  - 樣式基礎（ADR-0003，設計規範見 `ncu-frontend-design` skill）：`vite.config.ts`（`@tailwindcss/vite`）、`src/styles/index.css`（Tailwind 匯入、`@theme` 設計 token、含繁中 fallback 的字體堆疊、`prefers-reduced-motion` 基礎規則）、`index.html`（Google Fonts：Inter、Calistoga、JetBrains Mono、Noto Sans TC、Noto Serif TC）、`src/lib/cn.ts`（`clsx` + `tailwind-merge`）、`src/lib/cn.test.ts`
  - 套件：`tailwindcss`、`@tailwindcss/vite`、`framer-motion`、`class-variance-authority`、`tailwind-merge`、`clsx`、`vitest`
  - 驗證：`cd frontend && npm run lint && npm run test && npm run build`
- **P0-T04 Docker Compose 環境**
  - 檔案：`infra/docker/docker-compose.yml`（pgvector/pgvector:pg16、redis、api、worker、web）、`backend/Dockerfile`、`frontend/Dockerfile`、`.env.example`、`Makefile`（up／down／test／migrate／reset）
  - 驗證：`make up` 後 `curl localhost:8000/api/v1/health` 回 200
- **P0-T05 資料庫 Migration 與核心模型**
  - 檔案：`backend/alembic.ini`、`infra/migrations/versions/0001_core.py`、`backend/app/models/`（sources、ingestion_runs、source_items、source_item_versions、events、event_evidence、locations、audit_logs）、`backend/app/db/session.py`
  - 驗證：migration up → down → up 往返無錯；唯一約束依規格 §9
- **P0-T06 狀態機與列舉**
  - 檔案：`backend/app/domain/states.py`（EventStatus、VerificationStatus、AlertState 與合法轉移表）、`docs/architecture/state-machines.md`、`backend/tests/unit/test_state_machines.py`
  - 驗證：所有非法轉移都拋出例外（狀態依規格 §6.3）
- **P0-T07 RBAC 與稽核規格**
  - 檔案：`docs/architecture/rbac.md`、`backend/app/core/security.py`（介面先行）、`backend/app/services/audit.py`、migration 內加入禁止 UPDATE／DELETE audit_logs 的 trigger
  - 驗證：`backend/tests/integration/test_audit_append_only.py`
- **P0-T08 CI 流水線**
  - 檔案：`.github/workflows/ci.yml`（ruff、mypy、pytest、migration 往返、前端 lint／build、skill 靜態驗證預留、tracker 測試 `python3 -m pytest .claude/skills/ncu-resilience-tracker/tests -q`）
  - 驗證：PR 上 CI 全綠
- **P0-T09 來源授權盤點**（需你決定）
  - 檔案：`docs/governance/source-inventory.md`（來源、URL、類型、授權依據、robots.txt、條款連結、擷取頻率、核准狀態）
  - 驗證：至少 3 個來源標記「核准」
- **P0-T10 進度追蹤機制**
  - 檔案：`.claude/skills/ncu-resilience-tracker/`、`docs/progress/state.yaml`、`docs/progress/records/`、`docs/progress/reviews/`
  - 驗證：`python .claude/skills/ncu-resilience-tracker/scripts/tracker.py status` 正常輸出
  - 備註：骨架已完成，只需確認並標記
- **P0-T11 tracker 人工閘門保護**（ADR-0002 新增）
  - 檔案：`.claude/skills/ncu-resilience-tracker/scripts/tracker.py`、`.claude/skills/ncu-resilience-tracker/tests/test_tracker.py`、`.claude/skills/ncu-resilience-tracker/SKILL.md`、`docs/progress/phases.yaml`、`docs/decisions/ADR-0002-tracker-human-gate.md`
  - 驗證：`python3 -m pytest .claude/skills/ncu-resilience-tracker/tests -q`（系統 Python + pytest，不依賴 backend 環境）
  - 備註：`manual: true` 閘門標記 pass、`human: true` 任務標記 done 須加 `--confirmed-by-user`，否則拒絕且不改動 state.yaml
- **P0-T12 稽核與權限強化（code review 修正）**（ADR-0004 新增；進入 P1 前完成）
  - 檔案：`backend/app/services/audit.py`、`backend/app/core/security.py`、`backend/app/domain/states.py`、`backend/tests/unit/test_security.py`、`backend/tests/unit/test_audit_metadata.py`、`backend/tests/integration/test_audit_append_only.py`、`backend/tests/integration/conftest.py`、`docs/architecture/rbac.md`、`docs/decisions/ADR-0004-audit-hardening.md`；移除既有測試中的 `type: ignore`
  - 內容：稽核 metadata 檢查值（PII 樣式、字串長度）、UUID／datetime 正規化、欄位名稱改為分段比對；無請求上下文時產生 request_id；`Principal` 的 kind 與 roles 必須一致；敏感權限集中稽核（`authorize()`）；`permissions` 快取；測試資料庫可清空 audit_logs；移除所有 `type: ignore`
  - 驗證：`cd backend && uv run pytest -q && uv run mypy app`，且 `grep -rn "type: ignore" backend/app backend/tests` 無結果

```bash
make up
curl -f http://localhost:8000/api/v1/health
cd backend && uv run ruff check . && uv run mypy app && uv run pytest
uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head
cd ../frontend && npm run lint && npm run build
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G0-1 | 乾淨 clone 一鍵啟動 | 新目錄 `git clone` 後 `make up`，health 回 200 |
| G0-2 | Migration 可往返 | up／down／up 無錯 |
| G0-3 | CI 全綠 | GitHub Actions 最新一次 main 成功 |
| G0-4 | 至少 3 個合法來源 | source-inventory.md 核准數 ≥ 3 |

---

## P1 採集與來源追溯

目標：至少 3 個合法來源連續一晚（≥ 12 小時）穩定擷取，重跑零重複，任一筆資料可反查原文與擷取時間。前置：G0 全部通過。（ADR-0006：由規格 §13 的七天改為一晚）

- **P1-T01 Source Adapter 介面**
  - 檔案：`backend/app/ingestion/base.py`（`SourceAdapter` Protocol：`fetch_since(cursor) -> list[RawItem]`）、`app/ingestion/registry.py`、`app/ingestion/types.py`
  - 驗證：`tests/unit/ingestion/test_registry.py`
- **P1-T02 RSS Adapter**
  - 檔案：`app/ingestion/adapters/rss.py`（feedparser）、`tests/fixtures/rss/*.xml`、`tests/unit/ingestion/test_rss.py`
  - 驗證：固定 fixture 解析出正確筆數與欄位
- **P1-T03 校內公告 HTML Adapter**
  - 檔案：`app/ingestion/adapters/ncu_announcements.py`（httpx + selectolax，遵守 robots.txt、速率限制、User-Agent 標示）、`tests/fixtures/html/` 頁面快照、`tests/unit/ingestion/test_ncu_announcements.py`
  - 驗證：快照解析測試；抽取欄位缺漏時拋出 `LayoutChangedError`
- **P1-T04 人工匯入 Adapter（社群備援）**
  - 檔案：`app/ingestion/adapters/manual_import.py`（CSV／JSON）、`docs/governance/manual-import-format.md`
  - 內容（ADR-0008）：格式要能承接 P1-T13 的 Facebook 擷取結果：每筆有來源代碼、貼文網址（作為 `external_id`）、貼文時間、內文、擷取時間；不設發文者姓名、帳號欄位，含這類欄位時拒絕匯入
  - 驗證：`tests/unit/ingestion/test_manual_import.py`
- **P1-T05 標準化**
  - 檔案：`app/ingestion/normalize.py`（時間轉 aware UTC、文字清洗、語系偵測、`content_hash = sha256(normalized_text)`）
  - 驗證：`tests/unit/ingestion/test_normalize.py`（含民國年、無時區、全形字）
- **P1-T06 冪等寫入與版本**
  - 檔案：`app/ingestion/store.py`（單一交易：upsert source_items `ON CONFLICT`、版本 unique(item_id, content_hash)）
  - 驗證：`tests/integration/test_idempotent_ingest.py`：同批資料寫入 3 次筆數不變；內容改變才新增版本
- **P1-T07 排程、重試與死信**
  - 檔案：`app/worker/celery_app.py`、`app/worker/tasks/ingest.py`、beat 排程設定、`infra/migrations/versions/0003_ingestion_dead_letters.py`（ADR-0009：實際編號）
  - 驗證：模擬來源失敗 → 指數退避重試 3 次 → 進死信表；ingestion_runs 記錄 counts 與 status
- **P1-T08 來源健康與管理 API**
  - 檔案：`app/ingestion/health.py`、`app/api/v1/admin/ingestion_runs.py`（`GET /api/v1/admin/ingestion-runs`）
  - 驗證：`tests/integration/test_ingestion_runs_api.py`
- **P1-T09 標記資料集啟動**（金標準需你確認；ADR-0009）
  - 檔案：`data/labeling/guidelines.md`（分類法、真偽、同事件判準）、`data/labeling/label.schema.json`（由 `app/labeling/models.py` 產生）、`backend/app/labeling/`（標記模型、規則式預標、匯出、匯入、切分與統計）、`scripts/labeling/export_for_labeling.py`、`scripts/labeling/import_labels.py`、`scripts/labeling/dataset_stats.py`、`backend/tests/unit/labeling/`、`backend/tests/integration/test_export_for_labeling.py`
  - 內容：匯出 CSV 給使用者在試算表標記（含規則式預標），匯入時驗證後寫入 `data/labeled/labels.jsonl`；**標記檔只存文章 ID、內容雜湊與標記，不存內文**，因此不需加密；含內文的工作檔放 `data/raw/labeling/`（不進 git）。AI 預標（原 `scripts/labeling/ai_prelabel.py`）延到 S01–S03（P2-T08）完成後再補
  - 驗證：`cd backend && uv run pytest tests/unit/labeling tests/integration/test_export_for_labeling.py -q`；依事件／時間切分 dev／test。「累積 ≥ 100 筆人工確認樣本」改由閘門 G1-5 管控（`dataset_stats.py --check-min 100`），不是本任務的結案條件（使用者 2026-10-18 決定，ADR-0009）
- **P1-T10 一晚穩定運行報告**（ADR-0006：原為七天；ADR-0010：在 P1-T16 之後執行，涵蓋處室來源）
  - 檔案：`scripts/reports/ingestion_report.py`、`docs/progress/records/P1-stability.md`
  - 實作調整（使用者 2026-10-18～19 決定）：報告邏輯放在 `backend/app/ingestion/report.py`；補上手動執行指令 `python -m app.worker.tasks.ingest --source <代碼|all> --once`（`app/worker/tasks/ingest.py`）；`PageFetcher` 改為 `Connection: close`（`app/ingestion/http.py`，修正 keep-alive 競態）；自動停用不計入可重試的失敗（`app/ingestion/health.py`）
  - 驗證：報告顯示連續 ≥ 12 小時的成功率、各來源筆數、重複寫入數 = 0
- **P1-T11 氣象署開放資料 Adapter（cwa-alerts）**（ADR-0005 新增；P1-T02 查無可用的 RSS 來源）
  - 檔案：`app/ingestion/adapters/cwa_open_data.py`（httpx2，JSON API：天氣警特報 W-C0033-001、顯著有感地震報告 E-A0015-001，篩選桃園市）、`app/core/config.py`（`CWA_API_KEY`，SecretStr）、`tests/fixtures/cwa/*.json`（依官方欄位格式製作，不含授權碼）、`tests/unit/ingestion/test_cwa_open_data.py`
  - 內容：授權碼只從環境變數讀取，不得出現在日誌、錯誤訊息與 fixture；未設定授權碼時拋出不可重試的錯誤；依政府資料開放授權條款保留出處資訊（提供機關、資料名稱）；連線設定依 P1-T01 的決定登記在 `config/sources.yaml`
  - 驗證：`cd backend && uv run pytest tests/unit/ingestion/test_cwa_open_data.py`（CI 不呼叫真實 API）
- **P1-T12 中央大學新聞網 Adapter（ncu-news）**（ADR-0007 新增；新聞網改為主要新聞來源）
  - 檔案：`app/ingestion/adapters/ncu_news.py`（httpx2 + selectolax，沿用 `app/ingestion/http.py`）、`config/sources.yaml`（登記 `ncu-news`，options 列出三個列表網址）、`tests/fixtures/html/ncu_news/` 頁面快照（中大新聞、焦點新聞、學術新聞列表與一篇詳細頁）、`tests/unit/ingestion/test_ncu_news.py`
  - 內容：擷取 `https://ncusec.ncu.edu.tw/news/` 的中大新聞 `headlines.php`、焦點新聞 `focus-news.php`、學術新聞 `academic-news.php`；從詳細頁 `headlines_content.php?H_ID=<id>` 取得內文；以 `H_ID` 為 `external_id`，同一篇出現在多個列表只產出一筆並保留分類標籤；首次擷取只取各列表第一頁；遵守速率限制與 User-Agent 標示；必要欄位抽不到時拋出 `LayoutChangedError`
  - 驗證：`cd backend && uv run pytest tests/unit/ingestion/test_ncu_news.py`（快照解析、跨列表去重、版型變動錯誤；CI 不連外）
- **P1-T13 Facebook 社團每日擷取**（ADR-0008 新增）
  - 檔案：`.claude/skills/ncu-fb-capture/SKILL.md`（在已登入的 Chrome 讀取社團過去 24 小時的貼文，只讀不互動，輸出 P1-T04 匯入格式）、`infra/launchd/tw.ncu-resilience.fb-capture.plist.template`（每天 08:00 以 `claude -p` 執行）、`scripts/facebook/install_schedule.sh`（安裝與移除排程）、`config/sources.yaml`（登記 `facebook`，類型 `manual_import`）、`infra/docker/docker-compose.yml`（worker 唯讀掛載 `../../data:/data:ro`，P1-T04 驗收時決定）、`backend/tests/unit/ingestion/test_facebook_capture_format.py`
  - 內容：社團網址登記在 `config/sources.yaml`；輸出到 `data/raw/facebook/<YYYY-MM-DD>.json`（不進 git）；不保存發文者姓名、帳號、頭像、留言者資訊；後端不保存 Facebook 帳號、密碼或 cookie；首次執行前先實測 `claude -p` 能否連上 Claude in Chrome，不行就改為每天 08:00 發出提醒，由使用者手動觸發
  - 實作調整（ADR-0011，試行）：實測取不到單篇貼文網址，擷取端不讀連結，`url` 填社團網址，`manual_import` 以 `derive_external_id` 用內文雜湊識別；另外修改 `app/ingestion/adapters/manual_import.py`（`source_url`、`derive_external_id` 選項）與 `docs/governance/manual-import-format.md`；新增 `backend/tests/fixtures/facebook/sample_capture.json`；G1-4 抽樣排除 facebook
  - 驗證：`cd backend && uv run pytest tests/unit/ingestion/test_facebook_capture_format.py`（範例輸出通過匯入格式驗證、含發文者欄位時被拒絕）；手動執行一次擷取並以 `manual_import` 匯入，重跑零重複
- **P1-T14 HTML Adapter 共用 HTTP 模組**（ADR-0008 新增；P1-T12 驗收時發現的重複程式）
  - 檔案：`app/ingestion/http.py`（既有 `HostRateLimiter` 之外，新增 robots 檢查、串流讀取、錯誤分類）、`app/ingestion/adapters/ncu_announcements.py`、`app/ingestion/adapters/ncu_news.py`、`tests/unit/ingestion/test_http.py`（共用函式的新測試，先寫）
  - 內容：兩個 Adapter 的行為不變；不修改既有測試
  - 驗證：`cd backend && uv run pytest tests/unit/ingestion -q` 全部通過，且 `test_ncu_announcements.py`、`test_ncu_news.py` 沒有修改
- **P1-T15 來源分類標籤寫入資料庫**（ADR-0008 新增；使用者決定保留 P1-T12 的分類）
  - 檔案：`app/ingestion/types.py`（`RawItem.tags: frozenset[str]`，預設空集合）、`app/ingestion/store.py`（寫入時與既有標籤取聯集；只有標籤改變時不新增內容版本）、`app/models/`（`source_items.tags`）、`infra/migrations/versions/<下一個編號>_source_item_tags.py`（text[]，預設空陣列，可 downgrade）、`app/ingestion/adapters/ncu_news.py`（分類改放進 `tags`，移除 `categories` 屬性）、`tests/integration/test_source_item_tags.py`
  - 實作調整（使用者 2026-10-18 決定）：A. `ncu_news` **保留** `categories` 屬性（改由 tags 推算），不移除，避免修改 P1-T12 的既有測試；C. 範圍擴大到 RPage（`list_urls` 每項可寫成 `{url, tag}`，`ncu-cc` 9 個分類、`ncu-aa` 2 個分類）與生輔組（公告類別）。額外修改 `app/ingestion/normalize.py`（標籤要經 `NormalizedItem` 傳到 store）、`ncu_announcements.py`、`ncu_osa_life.py`、`list_detail.py`、`config/sources.yaml`，新增 `tests/unit/ingestion/test_source_tags.py`；migration 為 `0004_source_item_tags.py`
  - 驗證：`cd backend && uv run pytest tests/integration/test_source_item_tags.py tests/integration/test_idempotent_ingest.py tests/unit/ingestion -q`；migration up → down → up 往返無錯
- **P1-T16 處室來源補齊（總務處、電算中心、圖書館、環安中心、生輔組）**（ADR-0010 新增）
  - 檔案：`config/sources.yaml`（登記 `ncu-oga-news`、`ncu-cc`、`ncu-lib`、`ncu-epsc`、`ncu-osa-life`）；不是 RPage 的網站各一個 `app/ingestion/adapters/<來源>.py`、`tests/fixtures/html/<來源>/` 頁面快照、`tests/unit/ingestion/test_<來源>.py`；`app/ingestion/adapters/__init__.py`；網址有變動時更新 `docs/governance/source-inventory.md`
  - 實作結果（ADR-0010「實作時的調整」）：計中（9 個分類）、環安中心、圖書館、生輔組上線；總務處由使用者決定暫緩；新增共用模組 `app/ingestion/html.py`、`app/ingestion/adapters/list_detail.py`
  - 內容：開工時先逐一確認列表網址與版型（2026-10-18：計中是 RPage；總務處 `/news` 已改版、被導向錯誤頁），列出確定的檔案清單再實作；新 Adapter 沿用 P1-T14 的共用 HTTP 模組；遵守 robots、速率限制與專案 User-Agent；擷取頻率依盤點建議（總務處 30 分鐘，其他 60 分鐘）；網域或 robots 有變動時請使用者重新核准
  - 驗證：`cd backend && uv run pytest tests/unit/ingestion -q`（CI 不連外）；`uv run pytest tests/unit/ingestion/test_source_settings.py` 確認五個來源已核准、類型已註冊；本機手動擷取一輪，每個來源至少取得 1 篇

```bash
cd backend && uv run pytest tests/unit/ingestion tests/integration -q
uv run python -m app.worker.tasks.ingest --source all --once   # 手動觸發一輪
uv run python -m app.worker.tasks.ingest --source all --once   # 再跑一次，確認無新增重複
uv run python ../scripts/reports/ingestion_report.py --hours 12
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G1-1 | ≥ 3 個來源上線 | sources 表 enabled 且核准 ≥ 3 |
| G1-2 | 一晚（≥ 12 小時）網路正常時段成功率 ≥ 98%（ADR-0006、ADR-0012） | 人工確認 `P1-stability.md`；`ingestion_report.py --hours 12 --check-success 0.98` 用於重跑查證 |
| G1-3 | 重跑零重複 | test_idempotent_ingest 通過且報告重複數 0 |
| G1-4 | 可反查原文 | 隨機抽 20 筆皆有 url 與 fetched_at |
| G1-5 | ≥ 100 筆標記樣本 | `scripts/labeling/dataset_stats.py --check-min 100` |

---

## P2 AI 核心

目標：建好「可替換模型、可驗證輸出、可回溯證據」的 Skill 基礎設施，並讓 S01–S08 達到評測門檻。分 P2a 基礎設施（T01–T05）、P2b 事件理解（T06–T10）、P2c 風險與決策（T11–T14）。前置：G1 通過，標記樣本 ≥ 100 筆。

### P2a 基礎設施

- **P2-T01 Skill 規格與模板**
  - 檔案：`skills/_template/`（完整 7 件套）、`docs/architecture/skill-standard.md`（frontmatter 欄位、章節、命名、版本規則）
  - 實作調整（ADR-0013，使用者 2026-10-19 決定）：資料夾保留 `_template`，frontmatter `name: template`（底線開頭的資料夾是模板，Runner 不載入）；示範任務為公告類型分類，附 20 筆 golden cases 與手寫錄製回應
  - 驗證：逐項核對 7 件套；用 jsonschema 確認兩份 schema 合法，以及每筆 case 的輸入與錄製輸出都通過 schema（「模板通過 validate_skill」移到 P2-T02，ADR-0013）
- **P2-T02 Skill 靜態驗證工具**
  - 檔案：`scripts/skills/validate_skill.py`（名稱＝資料夾（模板去掉開頭底線，ADR-0013）、小寫連字號、description ≤ 1024 字元、schema 合法、cases ≥ 20、必含對抗案例）；CI 加入 `validate_skill.py skills/*`
  - 實作調整（使用者 2026-10-19 決定）：測試放在 `scripts/skills/tests/test_validate_skill.py`（比照 `scripts/governance/tests/`，在 CI 開發工具 job 執行）；jsonschema 只加裝在 CI 開發工具 job；除了上列項目，也檢查 `docs/architecture/skill-standard.md` 的靜態規則（7 件套、版本格式、章節、輸出 strict 子集與共同欄位、輸入 evidence、case 欄位與 must_not 格式、錄製回應），輸出是否符合 expected／must_not 留給 P2-T05
  - 驗證：`python3 -m pytest scripts/skills/tests -q`（壞掉的範例皆被擋下）；`skills/_template` 通過 validate_skill（ADR-0013，自 P2-T01 移入）
- **P2-T03 LLM Adapter**
  - 檔案：`app/ai/llm/base.py`（`LLMProvider.generate_structured(messages, json_schema, model, timeout, max_output_tokens)`）、`app/ai/llm/openai_provider.py`（Structured Outputs，strict JSON schema）、`app/ai/llm/fake_provider.py`（讀錄製回應，CI 不打 API）、`app/ai/llm/cost.py`（token 與費用記帳）
  - 實作調整（使用者 2026-10-19 決定）：維持 OpenAI；以 httpx2 直接呼叫 Responses API（不加 openai SDK）；設定放在 `config/llm.yaml`（gpt-5-mini、每次 US$0.05、每日 US$1），由 `app/ai/llm/config.py` 讀取與建立 Provider；`app/core/config.py` 新增 `openai_api_key`；以使用者金鑰實測一次
  - 驗證：`tests/unit/ai/test_llm_adapter.py`；模型名稱只出現在設定檔
- **P2-T04 Skill Runner**
  - 檔案：`app/ai/runner/loader.py`（掃描 `skills/`、解析 frontmatter、載入 references）、`app/ai/runner/prompt.py`（系統指令＝SKILL.md；來源內容一律包在 `<untrusted_source id=…>` 中）、`app/ai/runner/validate.py`（schema、列舉、範圍、evidence_id 存在、PII）、`app/ai/runner/run.py`（無效輸出重試 ≤ 2 次 → abstain、費用上限、逾時、寫入 ai_analysis_runs）、`infra/migrations/versions/<下一個編號>_ai_runs.py`（ai_analysis_runs、risk_assessments、event_relations）
  - 實作調整（使用者 2026-10-19 決定）：migration 為 `0005_ai_runs.py`；`ai_analysis_runs.event_id` 可空（S01、S02 在事件產生前執行），不存提示詞、來源原文與無效的原始輸出，另記 evidence_ids、validation_issues（代碼）、token 與 cost_usd；個資檢查電話、email、學號、身分證號；暫時性失敗重試 ≤ 2 次（2、4 秒或 Retry-After）後 failed，與無效輸出共用最多 3 次呼叫；每日上限依台北日加總 cost_usd。額外檔案：`app/models/ai.py`、`app/ai/runner/__init__.py`、`tests/integration/test_ai_runs.py`、`backend/pyproject.toml`（jsonschema、types-jsonschema）
  - 驗證：`tests/unit/ai/test_runner.py`（無效 JSON、不存在的 evidence_id、超支皆正確處理）
- **P2-T05 評測框架**
  - 檔案：`scripts/eval/run_eval.py`、`app/ai/eval/metrics.py`（Macro-F1、Precision／Recall、配對式合併指標）、`tests/golden/test_skills.py`（錄製模式）、`eval/reports/`
  - 實作調整（使用者 2026-10-20 決定）：指標與門檻集中在 `eval/thresholds.yaml`；評測不寫資料庫，費用記在報告，`--max-cost` 限制一次執行的總費用；報告只放統計與每筆的判定，完整輸出放 `eval/runs/`。評測流程在 `backend/app/ai/eval/runner.py`（`run_eval.py` 只是入口）；golden 測試在 `backend/tests/golden/test_skills.py`；另新增 `tests/unit/ai/test_eval_metrics.py`、`test_eval_runner.py`
  - 驗證：用模板 skill 跑出一份完整報告

### P2b 事件理解

- **P2-T06 S01 relevance-filter**：`skills/relevance-filter/`；輸出 is_campus_relevant、reason、evidence_spans
  - 實作調整（使用者 2026-10-20 決定）：校內單位的徵才與徵求受試者算相關、校外不算（同步修正 `data/labeling/guidelines.md`）；evidence_spans 由模型逐字抄錄原句（{evidence_id, quote}），Runner 比對原句確實存在、字元位置由程式計算（修改 `app/ai/runner/validate.py`、`run.py`，新增 `tests/unit/ai/test_validate_spans.py`）；golden cases 30 筆；門檻寫入 `eval/thresholds.yaml`；本任務 API 費用上限 US$0.5
- **P2-T07 S02 event-extractor**：`skills/event-extractor/`；輸出事件、時間、地點、證據字元位置；同時標記涉及可識別人物／店家（供 P3 發布規則使用）
  - 實作調整（使用者 2026-10-20 決定）：每篇最多 1 個主要事件（is_event）；時間由模型換算為台北時間的開始／結束日期與時間（相對時間以發布時間為基準），正確率以開始日期完全相等計算；地點寫成標準名稱或「全校」「校外」「不明」，以完全相等計算；證據位置沿用 P2-T06 的原句比對（evidence_spans 加 field）；mentions_person 依標記規範第 7 節；golden cases 30 筆；門檻寫入 `eval/thresholds.yaml`；本任務 API 費用上限 US$0.5
- **P2-T08 S03 event-classifier**：`skills/event-classifier/` + `references/taxonomy.md`；輸出類別、標籤、classification_confidence
  - 實作調整（AI 自行決定，2026-10-21）：分類法沿用標記規範的 9 類（含 none），同時符合多類時依原因歸類；輸入只有證據（與 S02 相同），不依賴 S02 的輸出；tags 0～5 個，優先用 taxonomy.md 的建議標籤；證據沿用 P2-T06 的原句比對；golden cases 56 筆（9 類各 6 筆、dev／test 各半，另 2 筆 abstain）；門檻寫入 `eval/thresholds.yaml`；本任務 API 費用上限 US$0.8
- **P2-T09 S04 event-linker**：`skills/event-linker/` + `app/ai/embeddings.py`（pgvector 候選召回＋時間地點視窗）；輸出 same_event／related_event／none 與信心
  - 實作調整（使用者 2026-10-21 決定：範圍 A、text-embedding-3-small）：另外修改 `app/ai/llm/`（base、openai_provider、fake_provider、config 加上 embedding）、`config/llm.yaml`（embedding 模型、維度與價格）、`app/models/event.py`（`occurrence_start_date`、`embedding vector(1536)`），新增 migration `0006_event_embeddings.py`、`pgvector` 依賴、`tests/unit/ai/test_embeddings.py`、`tests/integration/test_event_candidates.py`；候選召回依類別、事件日期 ±7 天、cosine 距離排序，地點不過濾；S04 每次判斷一對（新文章, 候選事件），以 relation = same_event 為正類計算 precision／recall；golden cases 41 筆；本任務 API 費用上限 US$0.8
- **P2-T10 S05 evidence-verifier**：`skills/evidence-verifier/` + `app/verification/rules.py`；規則優先（官方來源→confirmed），模型只提供建議，狀態變更一律經規則與狀態機
  - 實作調整（AI 自行決定，2026-10-21）：S05 逐份輸出立場（supports／contradicts／refutes／unrelated）與陳述性質，另給 suggested_status；最終狀態由 `rules.py` 依標記規範第 4 節決定（官方以最新一份為準；非官方：支持與反對並存 → disputed、兩份以上第一手 → verified），`confirmed` 統一為 `verified`；門檻以新的計次指標 `unsupported_count`（max 0）表示，另外修改 `app/ai/eval/metrics.py`、`app/ai/eval/runner.py`，新增 `tests/unit/ai/test_eval_unsupported.py`、`tests/unit/verification/test_rules.py`；golden cases 28 筆；本任務 API 費用上限 US$0.8

### P2c 風險與決策

- **P2-T11 風險引擎 + S06 risk-assessor**（權重與門檻需你決定）：`app/risk/engine.py`、`config/risk-policy.yaml`（權重、分級、critical_override，帶 policy_version）、`skills/risk-assessor/scripts/calculate_risk.py`（與引擎共用同一函式）；模型只給五維分數與不確定性，加權由程式計算
  - 實作調整（使用者 2026-10-21 決定 1A–5A）：權重與分級採規格 §6.1 初始值；critical_override 為人身安全 ≥ 80 → 極高，另加保底「任一面向 ≥ 80 → 至少高」；不看查證狀態（假設事件屬實）；評測的高風險 = 分級為高或極高。AI 自行決定：以 Decimal 計算、四捨五入到小數一位；`risk_assessments.dimensions` 另存 `_rules`、`_weighted_level`；評測新增「衍生欄位」機制（SKILL.md `metadata.derived_fields`，評測時由程式計算後併入輸出再比對，錄製只存模型原始輸出），另外修改 `app/ai/eval/runner.py`、`scripts/skills/validate_skill.py`（含其測試）、`docs/architecture/skill-standard.md`，新增 `tests/unit/risk/test_engine.py`、`tests/unit/ai/test_eval_derived.py`
- **P2-T12 S07 root-cause-analyst ＋ S08 solution-planner**：`skills/root-cause-analyst/`、`skills/solution-planner/`；必須區分 confirmed／hypothesized，證據不足時 abstain
- **P2-T13 分析管線**：`app/pipeline/analyze_item.py`（Celery chain S01→S02→S03→S04→S05→S06）、`POST /api/v1/admin/events/{id}/analyze`
  - 驗證：`tests/integration/test_pipeline.py` 端到端（fake provider）
- **P2-T14 提示注入與 PII 測試**：`tests/security/test_prompt_injection.py`、`tests/fixtures/adversarial/`（來源文章內嵌「忽略規則」「公開此人」等指令）
  - 驗證：所有對抗樣本皆不改變系統行為

| Skill | 主要指標 | 門檻 |
| --- | --- | --- |
| S01 relevance-filter | 相關性 F1 | ≥ 0.90 |
| S02 event-extractor | 時間／地點欄位正確率；證據可追溯 | ≥ 0.85；100% |
| S03 event-classifier | Macro-F1 | ≥ 0.85 |
| S04 event-linker | 同事件 Precision／Recall | ≥ 0.95／≥ 0.85 |
| S05 evidence-verifier | 無 evidence_ids 被標 verified 的次數 | 0 |
| S06 risk-assessor | 高風險 Recall；同輸入結果重現 | ≥ 0.95；100% |
| S07／S08 | 人工審閱：假設與事實區分正確率 | ≥ 0.90 |
| 全部 | Schema 合格率 | ≥ 99% |

```bash
python scripts/skills/validate_skill.py skills/*
cd backend && uv run pytest tests/unit/ai tests/golden tests/security -q
uv run python ../scripts/eval/run_eval.py --skill event-classifier --split test --live
uv run python ../scripts/eval/run_eval.py --all --split test --live --summary eval/reports/P2-summary.json
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G2-1 | S01–S08 達到上表門檻 | P2-summary.json |
| G2-2 | 事實性結論 100% 可追溯或 abstain | eval 報告 traceability 欄位 |
| G2-3 | 更換模型只需改設定 | 以 fake／另一模型跑同一 golden 套件 |
| G2-4 | 對抗測試全過 | tests/security |

---

## P3 產品與個人通知（MVP）

目標：完成個人版的最小可展示端到端版本（規格 §13.1 去除公開與通報部分，ADR-0008）。分 P3a 個人管理介面（T01、T03、T05）、P3c 個人通知（T07、T08、T10）；原 P3-T02、P3-T04、P3-T06、P3-T09 已刪除。系統不提供任何對外發布管道。前置：G2 通過。

### P3a 個人管理介面

- **P3-T01 使用者驗證**（ADR-0008：改為單一使用者）
  - 檔案：`app/core/security.py`（argon2 密碼、HttpOnly Secure Cookie session、CSRF；移除匿名投稿者與公開閱讀角色）、`app/api/deps.py`、`infra/migrations/versions/<下一個編號>_users_roles.py`、`scripts/admin/create_admin.py`、`docs/architecture/rbac.md`（移除公開端點）
  - 驗證：`tests/integration/test_auth.py`（未登入 401、CSRF 缺失拒絕；除 health 外沒有任何免登入的端點）
- **P3-T03 管理 API**
  - 檔案：`app/api/v1/admin/events.py`（列表、verify、analyze）、`alerts.py`（retract）、`dashboard.py`、`audit_logs.py`、`app/api/pagination.py`（cursor）、`app/api/errors.py`（{data,meta,error} 與錯誤碼）；所有寫入帶 request_id 並寫稽核
  - 驗證：`tests/integration/test_admin_api.py`
- **P3-T05 管理前端**（唯一的前端）
  - 檔案：`frontend/src/admin/pages/`（EventWorkbench：來源對照、風險分項、證據；AlertCenter：個人通知中心；SkillsDashboard：版本、費用、失敗率、評測結果；Ops）；移除 P0-T03 的 `frontend/src/public/` 骨架與「回公開網站」連結，根路徑改為登入頁
  - 驗證：Playwright 管理流程測試

### P3c 個人通知

- **P3-T07 個人通知規則**（規則內容需你決定）
  - 檔案：`app/alerts/policy.py`（確定性規則：哪些事件、哪個風險等級要通知使用者本人）、`app/alerts/pii.py`、`app/alerts/templates/`（通知模板）、`config/notification-policy.yaml`；移除 `.env.example` 的 `AUTO_PUBLISH_ENABLED`
  - 內容：未核實的具體指控不得呈現為已核實；通知內容先經 PII 遮蔽
  - 驗證：`tests/unit/alerts/test_policy.py`；附錄 A 的 A02、A05 不得把指控寫成事實
- **P3-T08 警示狀態機與個人遞送**
  - 檔案：`app/alerts/service.py`（candidate→policy_checked→eligible→published→updated／retracted／expired；published 表示已通知使用者本人）、`app/alerts/delivery.py`（先做管理介面通知中心，Email 留介面）；idempotency_key ＝ hash(event_id, alert_version, audience)
  - 驗證：`tests/integration/test_alert_idempotency.py`（重送不重複、撤回後停止）
- **P3-T10 端到端測試**
  - 檔案：`tests/e2e/`（Playwright），涵蓋附錄 A 的 A01–A08，期望結果依個人版改寫（A05 改為社群貼文惡意誣指個人：通知不呈現指控，管理端標記異常）
  - 驗證：`npx playwright test` 全過

```bash
cd backend && uv run pytest tests/integration tests/unit/alerts -q
cd ../frontend && npm run test && npx playwright test
make e2e   # 啟動完整環境並跑附錄 A 案例
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G3-2 | 同版本通知無重複遞送 | test_alert_idempotency |
| G3-3 | 未登入無法存取任何事件資料 | test_auth（除 health 外沒有免登入端點） |
| G3-4 | 附錄 A 的 A01–A08（個人版）全過 | Playwright e2e |
| G3-5 | MVP 展示路徑可運作 | 依 §13.1（個人版）錄一段展示並附在驗收紀錄 |

---

## P4 關聯、趨勢與韌性追蹤

目標：跨事件關聯經人工確認可用，趨勢偵測回測不使用未來資料，韌性指標在資料不足時誠實顯示 N/A。前置：G3 通過。

- **P4-T01 關聯圖資料與 API**
  - 檔案：`app/relations/service.py`（same_event／related_event／hypothesized_cause，邊存 evidence_ids、方法、分數、人工決策）、`app/api/v1/admin/relations.py`（查詢、確認、駁回）
  - 驗證：`tests/integration/test_relations.py`（禁止 self-loop、人工駁回可回滾）
- **P4-T02 關聯圖介面**
  - 檔案：`frontend/src/admin/pages/RelationGraph.tsx`（Cytoscape.js）
  - 驗證：Playwright 確認／駁回流程
- **P4-T03 時序統計與異常偵測**
  - 檔案：`app/analytics/timeseries.py`（週／月頻率、地點分布、EWMA、Poisson 變化偵測）、`infra/migrations/versions/<下一個編號>_analytics_views.py`（materialized views）
  - 驗證：`tests/unit/analytics/test_timeseries.py`（合成資料注入突增可被偵測）
- **P4-T04 趨勢回測**
  - 檔案：`scripts/eval/backtest_trends.py`（滾動切點，只用切點前資料）、`tests/unit/analytics/test_no_leakage.py`
  - 驗證：洩漏測試通過；回測報告寫入 `eval/reports/trends/`
- **P4-T05 改善措施追蹤（F10）**
  - 檔案：`app/resilience/actions.py`、`infra/migrations/versions/<下一個編號>_response_actions.py`（含狀態變更歷史）、管理端改善追蹤頁
  - 驗證：`tests/integration/test_response_actions.py`（發現→處理→恢復→改進完整紀錄）
- **P4-T06 校園韌性指數 CRI 與報告頁**（ADR-0008：原為公開報告）
  - 檔案：`app/resilience/cri.py`（四構面標準化，資料不足回傳 None）；管理端報告頁加入資料覆蓋率、時間窗、樣本量、來源限制
  - 驗證：`tests/unit/resilience/test_cri.py`（缺資料顯示 N/A，不產生假精確分數）

```bash
cd backend && uv run pytest tests/unit/analytics tests/unit/resilience tests/integration/test_relations.py -q
uv run python ../scripts/eval/backtest_trends.py --start 2026-01-01 --step week
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G4-1 | 關聯經人工評估可用 | 抽 50 組關聯人工審閱，正確率 ≥ 0.85 |
| G4-2 | 回測無未來資料 | test_no_leakage |
| G4-3 | 韌性指標不假精確 | test_cri |
| G4-4 | 改善生命週期完整 | test_response_actions |

---

## P5 情境模擬與部署維運

目標：完成 S09–S10 與 What-if，系統可監控、可備份還原、可在本機常駐運行，並驗證 Skills 能在其他 AI 客戶端運作。前置：G4 通過。

- **P5-T01 S09 trend-forecaster**：`skills/trend-forecaster/`；輸入 P4 的統計結果，只產生「異常解釋與前瞻訊號」，必須標註聲量≠事件數、資料覆蓋率
- **P5-T02 What-if 引擎 + S10 scenario-simulator**
  - 檔案：`app/simulations/engine.py`（baseline × Π(1 − effect_i)、效果區間、敏感度、固定隨機種子）、`skills/scenario-simulator/`（只負責解釋假設與限制，數值由引擎計算）、`infra/migrations/versions/<下一個編號>_scenario_runs.py`
  - 驗證：`tests/unit/simulations/test_engine.py`（同輸入同種子結果一致；效果值未知時回傳「無法估計」）
- **P5-T03 What-if 介面**
  - 檔案：`frontend/src/admin/pages/Scenario.tsx`（參數表單、龍捲風敏感度圖、明顯的「假設情境，非成效預測」標示）
  - 驗證：Playwright
- **P5-T04 可觀測性**
  - 檔案：`app/core/metrics.py`（Prometheus：任務成功率、AI 延遲與費用、通知失敗率）、`infra/monitoring/`（Prometheus、Grafana dashboard JSON、告警規則）
  - 驗證：Grafana 顯示上述指標；trace_id 貫穿 API→worker→AI run
- **P5-T05 備份與還原**
  - 檔案：`infra/scripts/backup.sh`（pg_dump + 物件儲存）、`infra/scripts/restore.sh`、`docs/runbooks/restore.md`
  - 驗證：還原演練紀錄 `docs/progress/records/P5-restore-drill.md`
- **P5-T06 本機常駐運行**（ADR-0008：原為正式部署設定）
  - 檔案：`infra/docker/docker-compose.local.yml`（restart 策略、只綁定 127.0.0.1）、`docs/runbooks/local-operation.md`（開機自動啟動、P1-T13 排程檢查、資料只存本機）
  - 驗證：重新開機後服務自動恢復，所有服務只監聽 127.0.0.1；原 P5-T07 負載測試已刪除
- **P5-T08 Skills 可攜性驗證**
  - 檔案：`docs/portability-report.md`；將 S01–S10 放進另一個支援 Agent Skills 的客戶端（例如 Claude Code）跑 golden cases，比較與 Runner 結果
  - 驗證：記錄各 skill 在不同平台的通過率與需調整之處
- **P5-T09 規格書 v1.1 更新**
  - 檔案：`docs/requirements/spec-v1.1.md`（實際來源、schema、門檻、部署規格的變更；含 ADR-0008 個人使用的刪減）
  - 驗證：與 v1.0 差異清單完整

```bash
cd backend && uv run pytest tests/unit/simulations -q
uv run python ../scripts/eval/run_eval.py --skill trend-forecaster --skill scenario-simulator --live
bash infra/scripts/backup.sh && bash infra/scripts/restore.sh --target local
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G5-1 | 模擬結果可重現 | test_engine |
| G5-2 | 備份可完整還原 | 還原演練紀錄 |
| G5-4 | Skills 可攜 | portability-report.md |

---

## 驗收紀錄

每個任務結案時用 `tracker.py record <ID>` 產生驗收紀錄，每個階段結束再用 `tracker.py record P?` 產生閘門紀錄。模板在 `.claude/skills/ncu-resilience-tracker/templates/acceptance-record.md`，檔名為 `docs/progress/records/{ID}-{YYYYMMDD}.md`。
