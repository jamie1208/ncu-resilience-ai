# NCU Resilience AI 分階段執行計畫

> 本檔是 coding agent 讀取任務定義的依據，內容與線上文件同步。
> - 規格書：`docs/requirements/NCU_Resilience_AI_System_Plan_v1.pdf`
> - 機器可讀的任務／閘門定義：`.claude/skills/ncu-resilience-tracker/references/phases.yaml`
> - 權威進度：`docs/progress/state.yaml`
>
> 修改計畫時三處要一起改：本檔、phases.yaml（或其覆寫檔 `docs/progress/phases.yaml`）、ADR。

全案 9 個階段、87 個任務、37 道閘門。任務 ID 為 `P{階段}-T{序號}`，閘門 ID 為 `G{階段}-{序號}`。Migration 檔名以實際的下一個可用編號為準（ADR-0009），計畫中寫成 `<下一個編號>_<名稱>.py`。標記「需你決定」的任務，AI 只能準備資料，必須由使用者本人定案。

**對外公開（ADR-0015，部分取代 ADR-0008）**：管理端只有使用者本人；P5 起新增公開網站、公開唯讀 API 與匿名回報入口。公開由確定性規則自動判斷，硬性邊界：未查證只公開中性提醒、疑似指控永不自動公開、推測原因不公開、Facebook 只公開 AI 摘要、使用者可一鍵下架。通知只送給使用者本人。

**公開規則放寬（ADR-0018，P8-T06 起生效）**：未查證事件公開 AI 摘要並標「尚未查證」；疑似指控照原文摘要公開，公開時立即提醒使用者並附一鍵下架；改善建議公開但不寫依據的原因。不變：推測原因不公開、Facebook 與匿名投稿只公開 AI 摘要、全部經 PII 遮蔽、一鍵下架。

已刪除的任務與閘門不重新編號，ID 也不重複使用：ADR-0008 刪除 P3-T02、P3-T04、P3-T06、P3-T09、P5-T07、G3-1、G5-3；ADR-0015 把 P4-T01–T04、P4-T06、P5-T01–T03、P5-T08、P5-T09 與 G4-1–G4-3、G5-1、G5-4 搬到 P6／P7 並改用新 ID（對照表見 ADR-0015）。

---

## Skill 開發標準循環（S01–S13 共用）

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
  - 後續變更（2026-10-27 更新 tracker 路徑）：`src/public/` 在 P3-T05 依 ADR-0008 移除（P5-T12 依 ADR-0015 重建）；`src/styles/index.css` 在 P3-T11 依 ADR-0014 拆成共用的 `src/styles/base.css` 與各設計的 `src/design/*/styles.css`
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
  - 實作調整（AI 自行決定，2026-10-21）：S07 每個原因標 `basis`（confirmed 只限 `ncu-` 官方來源的肯定說明，附逐字原句；其他一律 hypothesized，附 to_verify），沒有原因線索時 abstain（no_cause_evidence）；S08 輸入加上 S06 分級與 S07 原因，每項措施回填 cause_basis，待查證原因必須有 investigate 措施；「假設與事實區分正確率」以衍生欄位做自動代理指標（S07 `cause_basis`、S08 `basis_by_cause`，accuracy ≥ 0.90，寫入 `eval/thresholds.yaml`），人工審閱（`docs/progress/records/P2-T12-review.md`）由使用者 2026-10-21 完成，60/60 正確；另外修改 `app/ai/runner/validate.py`（巢狀 evidence_ids／evidence_spans 檢查）、`app/ai/eval/runner.py`（登記衍生欄位）、`docs/architecture/skill-standard.md`，新增 `app/ai/eval/derived.py`、`tests/unit/ai/test_validate_nested.py`、`tests/unit/ai/test_eval_decision.py`；golden cases S07 26 筆、S08 22 筆；本任務 API 費用上限 US$0.8
- **P2-T13 分析管線**：`app/pipeline/analyze_item.py`（Celery chain S01→S02→S03→S04→S05→S06）、`POST /api/v1/admin/events/{id}/analyze`
  - 實作調整（AI 自行決定，2026-10-21）：一個 Celery 任務依序執行六步（不拆成 chain，避免來源原文進入 Redis），每次 Skill 執行後提交；S04 依距離逐一判斷最多 3 個候選，第一個 same_event 併入、related_event 寫 event_relations；文章已屬於某事件時改為重跑該事件；API 重跑只做 S05→S06，回 202 並排入佇列（`pipeline.analyze_event`），已封存回 CONFLICT；evidence_id 用文章 id 的十六進位。另外修改 `app/api/v1/admin/__init__.py`、`app/worker/celery_app.py`（任務名稱與 include），新增 `app/pipeline/__init__.py`、`app/api/v1/admin/events.py`（P3-T03 會擴充）；修正 `app/ai/runner/validate.py` 把存在於輸入的 evidence_id 誤判為學號／電話（新增 `tests/unit/ai/test_validate_pii_ids.py`）
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

目標：完成個人版的最小可展示端到端版本（規格 §13.1 去除公開與通報部分，ADR-0008）。分 P3a 個人管理介面（T01、T03、T11、T05）、P3c 個人通知（T07、T08、T10）；原 P3-T02、P3-T04、P3-T06、P3-T09 已刪除。系統不提供任何對外發布管道。前置：G2 通過。

### P3a 個人管理介面

- **P3-T01 使用者驗證**（ADR-0008：改為單一使用者）
  - 檔案：`app/core/security.py`（argon2 密碼、HttpOnly Secure Cookie session、CSRF；移除匿名投稿者與公開閱讀角色）、`app/api/deps.py`、`infra/migrations/versions/<下一個編號>_users_roles.py`、`scripts/admin/create_admin.py`、`docs/architecture/rbac.md`（移除公開端點）
  - 驗證：`tests/integration/test_auth.py`（未登入 401、CSRF 缺失拒絕；除 health 外沒有任何免登入的端點）
- **P3-T03 管理 API**
  - 檔案：`app/api/v1/admin/events.py`（列表、verify、analyze）、`alerts.py`（retract）、`dashboard.py`、`audit_logs.py`、`app/api/pagination.py`（cursor）、`app/api/errors.py`（{data,meta,error} 與錯誤碼）；所有寫入帶 request_id 並寫稽核
  - 驗證：`tests/integration/test_admin_api.py`
  - 範圍調整（使用者決定，2026-10-21）：計畫原本沒有任務建立 alerts 表，改由本任務新增 `app/models/alert.py`（alerts、alert_deliveries，依規格 §9）與 `infra/migrations/versions/0008_alerts.py`，P3-T08 直接沿用
  - 實作調整（AI 自行決定，2026-10-21）：另加 `GET /admin/events/{id}`（證據來源、風險歷史，供 P3-T05 事件工作台）與 `GET /admin/alerts`（通知中心）；儀表板為 `GET /admin/dashboard`（取代規格的公開 `/dashboard/summary`，需 `events:read_all`）；verify 可設定 detected、analyzing 以外的事件狀態，轉移不合法回 CONFLICT；只有 published、updated 可撤回；備註／撤回原因含個資回 VALIDATION_ERROR；稽核查詢的稽核 action 為 `audit_logs.read`（`authorize()` 既有命名）；未預期例外回 500 `INTERNAL_ERROR`（統一格式、不帶細節）。另外修改 `app/api/v1/admin/__init__.py`、`app/models/__init__.py`、`docs/architecture/rbac.md`
- **P3-T11 雙設計系統**（ADR-0014，使用者決定，2026-10-21）
  - 檔案：`docs/decisions/ADR-0014-dual-design-system.md`；`frontend/src/design/contract.ts`（兩套元件庫共同的元件介面與必要 token 清單）、`frontend/src/design/registry.ts`（設計名稱、字體、建置時選擇）；`frontend/src/design/minimal/`（既有 Minimalist Modern，由 `src/components/` 與 `src/styles/index.css` 搬入）、`frontend/src/design/playful/`（Playful Geometric 元件與 token）；`frontend/src/styles/base.css`（兩套共用的 base 與 reduced-motion）；`frontend/vite.config.ts`、`frontend/tsconfig.app.json`、`frontend/index.html`、`frontend/package.json`、`frontend/Dockerfile`（`VITE_DESIGN` 建置參數與 `@design` 別名）；`.github/workflows/ci.yml`（兩套都建置）；`.claude/skills/ncu-frontend-design/`（加入 Playful Geometric 規範）
  - 內容：頁面只從 `@design` 匯入元件；`VITE_DESIGN=minimal|playful` 在建置時決定別名指向哪一套，預設 minimal，未知值建置失敗；兩套元件庫實作同一份介面、提供同一組語意 token；風險等級與緊急資訊在兩套都使用語意色且不加裝飾動畫
  - 驗證：`cd frontend && npm run lint && npm run test && npm run build && VITE_DESIGN=playful npm run build`
- **P3-T05 管理前端**（唯一的前端；頁面只從 `@design` 匯入元件，兩套設計都要能建置，P3-T11）
  - 檔案：`frontend/src/admin/pages/`（EventWorkbench：來源對照、風險分項、證據；AlertCenter：個人通知中心；SkillsDashboard：版本、費用、失敗率、評測結果；Ops）；移除 P0-T03 的 `frontend/src/public/` 骨架與「回公開網站」連結，根路徑改為登入頁
  - 驗證：Playwright 管理流程測試
  - 實作調整（AI 自行決定，2026-10-21）：另加 `GET /admin/skills`（`app/api/v1/admin/skills.py`，SkillsDashboard 需要的版本、近 7 天執行統計與評測摘要；需 `events:read_all`，只回摘要）與 `tests/integration/test_admin_skills_api.py`；`@design` contract 新增 Badge、Input、Select、Textarea（兩套都實作，`src/design/forms.test.tsx`）；管理端共用模組 `src/admin/{api,format,router,auth,useLoad,Link,ui,AdminApp}`，路由以 History API 自寫、不加套件；Playwright 測試放 `frontend/e2e/`（`*.e2e.ts`，API 以 page.route 模擬、共用 `src/test/fixtures.ts`），`npm run e2e`／`e2e:playful`，另加 `playwright.config.ts`、`tsconfig.e2e.json` 並納入 CI；`App.test.tsx` 依本任務移除公開首頁而改寫（根路徑改為登入頁，免責聲明與 110／119 斷言保留）

### P3c 個人通知

- **P3-T07 個人通知規則**（規則內容需你決定）
  - 檔案：`app/alerts/policy.py`（確定性規則：哪些事件、哪個風險等級要通知使用者本人）、`app/alerts/pii.py`、`app/alerts/templates/`（通知模板）、`config/notification-policy.yaml`；移除 `.env.example` 的 `AUTO_PUBLISH_ENABLED`
  - 內容：未核實的具體指控不得呈現為已核實；通知內容先經 PII 遮蔽
  - 驗證：`tests/unit/alerts/test_policy.py`；附錄 A 的 A02、A05 不得把指控寫成事實
- **P3-T08 警示狀態機與個人遞送**
  - 檔案：`app/alerts/service.py`（candidate→policy_checked→eligible→published→updated／retracted／expired；published 表示已通知使用者本人）、`app/alerts/delivery.py`（先做管理介面通知中心，Email 留介面）；idempotency_key ＝ hash(event_id, alert_version, audience)
  - 驗證：`tests/integration/test_alert_idempotency.py`（重送不重複、撤回後停止）
  - 範圍調整（使用者決定，2026-10-26）：接上呼叫端，另外修改 `app/pipeline/analyze_item.py`（S06 之後同步通知）與 `app/api/v1/admin/events.py`（verify 之後同步通知）
  - 實作調整（AI 自行決定，2026-10-26）：入口為 `sync_event_alert(session, event_id)`，以事件列 `FOR UPDATE` 串行化；不符合規則時不建立通知列（`blocked` 不落地）；查證 false → 自動 retracted，事件 resolved／archived 或不再符合規則 → expired，之後再符合規則則建立新通知、版本接續；最新一則已撤回 → 不再通知該事件；不在 S03 類別的事件不通知（不拋錯）；執行者為 `service:notifier`，自動改版／撤回／過期只需 `alerts:publish`（`alerts:retract` 仍只供手動撤回）；monitoring 視為 unverified；官方來源依最新成功 S05 的 claim_type＝official 且 supports 的證據；遞送以 alert_deliveries 唯一約束 `ON CONFLICT DO NOTHING` 佔位後才送出，失敗記 `failed` 不重試
- **P3-T10 端到端測試**
  - 檔案：`tests/e2e/`（Playwright），涵蓋附錄 A 的 A01–A08，期望結果依個人版改寫（A05 改為社群貼文惡意誣指個人：通知不呈現指控，管理端標記異常）
  - 驗證：`make e2e` 全過
  - 範圍調整（使用者決定，2026-10-26）：真實全端（FastAPI＋PostgreSQL＋前端，不模擬 API）；`tests/e2e/` 自成一組 Playwright 設定（借用 frontend 的 `@playwright/test`），G3-4 指令改為 `make e2e`，Makefile 加 `e2e` 目標；A04 只驗證建立不同事件，related_event 的呈現留到 P4-T01／P4-T02；A05 的「管理端標記異常」另外修改 `app/api/v1/admin/alerts.py`（通知列表回傳 `flags`，讀取時依 P3-T07 的指控規則重算）與 `frontend/src/admin/{api.ts,pages/AlertCenter.tsx}`（「疑似指控，需人工判斷」標記）；e2e 發現查證 false 但風險同時降低時通知被標為過期而非撤回，修正 `app/alerts/policy.py`（略過時一律帶出 verification_false）
  - 實作調整（AI 自行決定，2026-10-26）：`tests/e2e/seed_appendix_a.py` 在獨立資料庫（名稱必須以 `_e2e` 結尾）以真實的 `store_items` 與 `analyze_item`（腳本化假模型）建立案例，並檢查只有資料庫看得到的不變量（A03 證據數、A07 冪等）；API 跑在 8001、Redis 用 db 15，開發用 worker 收不到任務；頁面的 `/api` 請求由 `fixtures.ts` 以 page.route 轉到 e2e API

```bash
cd backend && uv run pytest tests/integration tests/unit/alerts -q
cd ../frontend && npm run test && npx playwright test
make e2e   # 啟動資料庫，在獨立 e2e 資料庫建立附錄 A 案例並以真實 API＋前端跑 Playwright
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G3-2 | 同版本通知無重複遞送 | test_alert_idempotency |
| G3-3 | 未登入無法存取任何事件資料 | test_auth（除 health 外沒有免登入端點） |
| G3-4 | 附錄 A 的 A01–A08（個人版）全過 | Playwright e2e |
| G3-5 | MVP 展示路徑可運作 | 依 §13.1（個人版）以附錄 A 的 e2e 案例資料錄一段展示並附在驗收紀錄；真實資料展示移到 G4-8（ADR-0015） |

---

## P4 真實資料、事件小報告與追蹤到底

目標：真實來源每天穩定產生事件；後台事件頁有一份看得懂的「事件小報告」（說明、影響、值得查查看、同學／學校可以怎麼做、建議轉給哪個單位），每一項都能回溯證據，推測不會被呈現為事實；改善措施有完整的狀態紀錄，同地點同類事件再發生時會提醒使用者（ADR-0015）。前置：G3 通過。

- **P4-T07 真實資料管線診斷與修復**
  - 背景：P1–P3 完成後主資料庫仍是 0 個事件（2026-10-27）。
  - 檔案：先診斷，從擷取、標準化、`analyze_item` 觸發、S01 過濾到事件建立逐段確認，把原因與要修改的檔案提給使用者後再修；`docs/runbooks/pipeline-health.md`（怎麼確認每天有事件產生、常見故障與處理）
  - 驗證：修正處先有失敗的測試；真實來源跑一輪後主資料庫有事件；之後連續 3 天每天都有新事件（G4-7）
  - 診斷結果（2026-10-27）：擷取正常（288 篇），但沒有任何程式把文章排入 `pipeline.analyze_item`（ai_analysis_runs 為 0）；修好後又發現容器沒有掛載 `skills/`，容器內分析一律失敗；Facebook 08:00 擷取因喚醒後網路未就緒（ENOTFOUND）自 10-20 起沒有新檔
  - 範圍調整（使用者決定，2026-10-27）：以定期掃描未分析文章銜接擷取與分析——`infra/migrations/versions/0009_source_item_analysis.py`（source_items 加 analysis_status／analysis_attempts／analysis_queued_at）、`app/models/source.py`、`app/pipeline/analyze_item.py`（掃描任務與結束時更新狀態）、`app/worker/celery_app.py`（beat 排程）、`app/ingestion/store.py`（內容改變時重置）、`tests/integration/test_analysis_dispatch.py`；既有文章只分析近 14 天發布的，較舊的標為 skipped；`infra/docker/docker-compose.yml` 唯讀掛載 `skills/`；`tests/unit/worker/test_ingest_tasks.py` 的 beat 斷言改為逐項比對；Facebook 擷取加上等網路與重試（`scripts/facebook/run_capture.sh`、`infra/launchd/tw.ncu-resilience.fb-capture.plist.template`、`tests/unit/ingestion/test_facebook_capture_retry.py`）
  - 實作調整（AI 自行決定，2026-10-27）：掃描每分鐘一次，排隊中最多 3 篇、新的先做；時間依 published_at，沒有時用 fetched_at；台北日已花費達 daily_usd 時不排；queued 超過 60 分鐘重排；管線完成或 abstain → done，費用上限 → pending 不計次，其他失敗計次、3 次 → failed；FB 包裝腳本等網路最多 15 分鐘、API Error 或結束碼非 0 時隔 10 分鐘重試最多 3 次，「需要使用者處理」不重試
- **P4-T08 事件說明（摘要）**
  - 檔案：`skills/event-extractor/`（輸出加 `summary`：一到三句中性說明，不含人名、店名與個資，附 evidence_ids；SKILL.md 升版並重跑評測，門檻不得調低）、`infra/migrations/versions/<下一個編號>_event_summary.py`、`app/pipeline/analyze_item.py`（寫入摘要；同事件多篇來源時以最新、證據最強的一篇為準）、`app/api/v1/admin/events.py`（詳情回傳摘要）、後台事件頁顯示
  - 驗證：`tests/integration/test_event_summary.py`（摘要寫入與更新、含個資時被遮蔽或拒絕）；S02 評測維持 P2 門檻
  - 範圍調整（使用者決定，2026-10-27）：S02 輸出 schema 為 strict（所有欄位必填），既有測試的假 S02 輸出補上 `summary` 欄位（`tests/integration/test_pipeline.py`、`tests/e2e/seed_appendix_a.py`、`tests/fixtures/adversarial/samples.json`，斷言不變）；以真實 gpt-5-mini 重錄 S02 的 30 個案例；另外修改 `app/models/event.py`（欄位）與 `frontend/src/admin/{api.ts,pages/EventWorkbench.tsx}`、`frontend/src/test/fixtures.ts`（後台事件頁「事件說明」）
  - 實作調整（AI 自行決定，2026-10-27）：`summary` 為 `{text, evidence_ids}` 或 null（不是事件、abstain）；events 加 summary、summary_evidence_ids、summary_source_item_id、summary_ai_run_id、summary_updated_at；出處強弱為（是否官方來源, 發布時間或擷取時間），新文章嚴格較強才覆寫，沒有摘要不覆寫；含個資或引用不存在的證據由 Runner 拒絕（重試後 abstain，管線停在 S02），寫入前另經 `mask_pii()`；既有事件不回填
- **P4-T09 事件小報告（接上 S07／S08）**
  - 檔案：`app/reports/service.py`（S06 之後執行 S07、S08；風險中以上自動產生，其他可在後台手動觸發；事件版本更新時重算）、`infra/migrations/versions/<下一個編號>_event_reports.py`（保存每次產生的報告與 AI run 關聯）、`app/api/v1/admin/events.py`（詳情回傳小報告）、後台事件頁「事件小報告」區塊
  - 呈現：可能的影響（S06 分項）；值得查查看（S07 `hypothesized`，明確標示「推測，尚未查證」）與已確認原因（S07 `confirmed`，附原句）；可以怎麼做（S08，依同學／學校與短／中／長期分組）；建議轉給哪個單位（S08 `responsible_unit`，只顯示給使用者，系統不寄信）；S07／S08 abstain 時顯示「證據不足，暫不分析」
  - 驗證：`tests/integration/test_event_report.py`（每一項都有存在的 evidence_ids；hypothesized 永遠帶推測標示；abstain 不產生空白或捏造內容；事件被查證為不實時報告標為失效）；Playwright 事件頁流程
  - 範圍調整（使用者決定，2026-10-27）：另外修改 `app/pipeline/analyze_item.py`（`PipelineDeps.report_hook`，S06 之後呼叫）、`app/models/report.py`（event_reports）、`app/worker/celery_app.py`（任務 `reports.generate_event_report`）、`frontend/src/admin/{api.ts,pages/EventWorkbench.tsx}`、`frontend/src/test/fixtures.ts`、`frontend/e2e/mockApi.ts`；手動觸發為 `POST /api/v1/admin/events/{id}/report`
  - 實作調整（AI 自行決定，2026-10-27）：報告為獨立 Celery 任務；風險 medium 以上自動產生，low 但已有有效報告時重算；確認原因必須有官方來源的逐字原句，否則改判 hypothesized，措施的 cause_basis 依改判結果重填；缺 evidence_ids 或引用非事件證據的項目捨棄；報告保留歷史、失效不刪除；手動觸發沿用 `events:analyze`，不實或封存回 CONFLICT
- **P4-T05 改善措施追蹤（F10）**
  - 檔案：`app/resilience/actions.py`（可從 S08 的校方措施建立追蹤項目，也可手動新增）、`infra/migrations/versions/<下一個編號>_response_actions.py`（含狀態變更歷史）、後台改善追蹤頁
  - 驗證：`tests/integration/test_response_actions.py`（發現→處理→恢復→改進完整紀錄）
  - 範圍調整（使用者決定，2026-10-27）：另外新增 `app/resilience/__init__.py`、`app/models/response_action.py`（並在 `app/models/__init__.py` 註冊）、`app/api/v1/admin/actions.py`（並在 `app/api/v1/admin/__init__.py` 註冊）；後台路由 `GET /api/v1/admin/actions`、`GET`／`PATCH /api/v1/admin/actions/{id}`、`POST /api/v1/admin/actions/{id}/transitions`、`POST /api/v1/admin/events/{id}/actions`；權限沿用 `events:read_all`／`events:verify`，不新增權限；前端 `frontend/src/admin/pages/ResponseActions.tsx`（含測試）、`frontend/src/admin/{api.ts,router.ts,AdminApp.tsx}`、`frontend/src/test/fixtures.ts`，以及 `frontend/src/admin/pages/EventWorkbench.tsx`（校方措施旁「加入改善追蹤」、手動新增）
  - 實作調整（AI 自行決定，2026-10-27）：狀態 discovered／handling／recovered／improved，另有 dropped（不追蹤）；發現、處理、恢復可改為不追蹤，恢復可回到處理，改進與不追蹤為終點；轉到恢復、改進必須附佐證；只有有效報告的校方措施可建立，同一份報告的同一項措施只能建立一次，保存措施快照、負責人預設為建議單位；事件不實或封存時不能新增；名稱、負責人、備註、佐證含個資時拒絕；每次狀態變更（含建立）寫入 response_action_transitions 並與稽核紀錄共用 request_id；期限在前端以台北當日 23:59:59 為準，已改進或不追蹤的項目不算逾期
- **P4-T10 再發生偵測與提醒**
  - 檔案：`app/resilience/recurrence.py`（確定性規則：同地點、同類別在設定的時間窗內再次發生，或已標記「已改善」的事件再次發生）、`config/recurrence.yaml`（時間窗與門檻）、通知中心提醒「可能不是單純個案，要不要整批檢查？」
  - 驗證：`tests/unit/resilience/test_recurrence.py`（合成資料：時間窗內再發生會提醒、不同地點或類別不會、改善後再發生會標示）
  - 範圍調整（使用者決定，2026-10-27）：提醒在讀取時計算，不加 migration、不接管線；另外新增 `app/api/v1/admin/recurrences.py`（`GET /api/v1/admin/recurrences`，權限沿用 `alerts:read_all`，並在 `app/api/v1/admin/__init__.py` 註冊）與 `tests/integration/test_recurrence_api.py`；前端 `frontend/src/admin/{api.ts,pages/AlertCenter.tsx,pages/AlertCenter.test.tsx}`、`frontend/src/test/fixtures.ts`、`frontend/e2e/mockApi.ts`（通知中心「再發生提醒」區塊）；提醒無法標為已看過
  - 實作調整（AI 自行決定，2026-10-27）：時間窗從同組最近一次往回 30 天（含）、門檻 2 件；最近一次不在 30 天內的組不顯示；改進日之後（不含當天）365 天內再發生標示 after_improvement；事件日期用 S02 發生日期，沒有時用建立時間的台北日期；改進日取最早一次轉到 improved；地點不明、類別 none、查證不實的事件不列入；同地點以 location_id 判斷（地點依名稱去重）；類別中文名稱沿用 notification-policy.yaml；地點與標題經 `mask_pii()`

```bash
cd backend && uv run pytest tests/integration/test_event_summary.py tests/integration/test_event_report.py tests/integration/test_response_actions.py tests/unit/resilience -q
uv run python ../scripts/eval/run_eval.py --skill event-extractor
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G4-4 | 改善生命週期完整 | test_response_actions |
| G4-5 | 小報告每項可回溯證據、推測不呈現為事實 | test_event_report |
| G4-6 | 同地點同類事件再發生可被偵測 | test_recurrence |
| G4-7 | 真實資料連續 3 天產生事件 | 使用者確認 `pipeline-health.md` 的檢查結果 |
| G4-8 | 真實資料展示路徑可運作 | 以真實資料錄一段展示（事件列表 → 事件小報告 → 改善追蹤 → 再發生提醒）並附在驗收紀錄 |

---

## P5 公開網站與上線

目標：任何人都能看到的公開網站。公開與否由確定性規則自動判斷，疑似指控、推測原因、Facebook 原文與匿名投稿原文永不外流；使用者可以一鍵下架；經通道從使用者的電腦上線，管理端不對外（ADR-0015）。前置：G4 通過。

- **P5-T10 公開規則引擎**
  - 檔案：`app/publishing/policy.py`（純函式，不呼叫模型）、`config/publish-policy.yaml`（可調整的門檻；硬性邊界寫在程式裡，設定無法關閉）、`infra/migrations/versions/<下一個編號>_publications.py`（公開狀態、版本、下架紀錄）
  - 規則：已查證（官方來源證實或使用者查證為 true）才公開事件內容；未查證只公開 P3-T07 的中性安全提醒；疑似指控永不自動公開；S07 `hypothesized` 不公開；Facebook 來源只公開 AI 摘要，不放原文與連結；公開內容一律經 PII 遮蔽
  - 驗證：`tests/unit/publishing/test_publish_policy.py`（每條硬性邊界各有正反案例；附錄 A 的 A02、A05 不會公開事件內容）
  - 範圍調整（AI 自行決定，2026-10-27）：使用者同意在 G4-7、G4-8 尚未通過時超前開始；另外新增 `app/publishing/__init__.py`、`app/models/publication.py` 與 `app/models/__init__.py` 的匯入（`test_models_match_migrations` 要求 ORM 與 migration 一致）、`config/README.md` 加一行說明
  - 實作調整（AI 自行決定，2026-10-27）：migration 為 `0013_publications.py`（publications 每事件一筆：level content／safety_notice／none、state published／withdrawn／taken_down、version、needs_review；publication_versions 為已遮蔽 PII 的內容快照；publication_takedowns 為下架紀錄）；「官方證實」與「使用者查證」以輸入 `confirmed_by` 表示，只有兩份第一手來源支持的 verified 不公開內容；疑似指控比對所有會公開的文字（標題、摘要、影響理由、措施），命中時只公開不寫地點的中性提醒並標記 needs_review（A02 允許中性防竊提醒）；公開版小報告不放任何 S07 原因（ADR-0015：只放影響與建議），依據推測原因的 S08 措施也不公開；來源只列官方來源（`is_official_source`）的名稱與連結；類別名稱、防護建議、指控規則與中性模板沿用 P3-T07 的 notification-policy.yaml 與 general_safety 模板；未經證實事件的安全提醒門檻預設 high、critical（與個人通知相同），沒有風險評估不公開
- **P5-T11 公開唯讀 API 與威脅模型**
  - 檔案：`app/api/v1/public/`（最新動態、安全提醒、事件分類查詢、公開版小報告、改善進度；只讀取 P5-T10 判定可公開的版本；cursor 分頁、rate limit、快取標頭）、`docs/architecture/threat-model-public.md`（每個公開端點的資料、濫用方式與防護）、`docs/architecture/rbac.md`（加入公開端點）
  - 驗證：`tests/security/test_public_exposure.py`（以附錄 A 與對抗案例確認疑似指控、推測原因、FB 原文、投稿原文、個資不出現在任何公開回應）；`tests/integration/test_public_api.py`
  - 範圍調整（使用者同意，2026-10-28）：新增 `app/publishing/sync.py`（從資料庫組出 P5-T10 的輸入、寫入 publications 與版本快照、Celery 任務）與 `app/worker/celery_app.py` 的每分鐘 beat 排程；`app/main.py` 掛上公開 router；`tests/integration/test_auth.py` 允許 `/api/v1/public/` 下的 GET 免登入（其他方法不得存在）；`tests/unit/worker/test_ingest_tasks.py` 的 beat 排程集合加入 `sync-publications`
  - 實作調整（AI 自行決定，2026-10-28）：改善進度也經公開規則（`policy.py` 新增 `ActionInput`，只放名稱、狀態、預計完成日、更新時間；依據推測原因的不公開；名稱納入指控比對），存在快照內，公開 API 不讀原始資料表；查證依據 user 取自最近一次有效的 `event.verify` 稽核、official 沿用個人通知的 S05 判斷；monitoring、resolved 沿用進入前的查證結果；封存事件撤下；已下架不覆蓋；端點為 feed、notices、categories、events、events/{id}、progress，`id` 為 publication ID；rate limit 為行程內固定時間窗（每用戶端 60 秒 60 次，加鹽雜湊識別）；快取 `public, max-age=30` + 弱 ETag，錯誤 `no-store`
- **P5-T14 一鍵下架**
  - 檔案：`app/publishing/takedown.py`、`app/api/v1/admin/publications.py`、後台事件頁與公開內容列表的下架按鈕
  - 驗證：`tests/integration/test_takedown.py`（下架後公開 API 立即不再回傳、快取失效、寫入稽核）
  - 實作調整（AI 自行決定，2026-10-28）：端點為 `GET /api/v1/admin/publications`（`alerts:read_all`）、`POST …/{id}/takedown`（`alerts:retract`，原因選填、經 PII 檢查，寫入 publication_takedowns 與稽核 `publication.take_down`）、`POST …/{id}/restore`（`events:verify`，只改回 withdrawn 後交回公開規則判斷，不直接公開，稽核 `publication.restore`）；published 與 withdrawn 都可下架（撤下中的也能阻止日後自動重新公開）；`sync.py` 讀 publication 改為 `SELECT … FOR UPDATE` 與下架互斥；快取失效＝API 無伺服器端快取、ETag 依內容計算，下架後舊 ETag 不再 304（瀏覽器最多殘留 30 秒，通道邊緣快取留給 P5-T15）；後台新增「公開內容」頁（`/admin/publications`），事件頁新增「公開狀態」卡片，兩者共用下架元件；另更新 `rbac.md`、`threat-model-public.md`
- **P5-T12 公開前端**
  - 檔案：`frontend/src/public/`（首頁最新動態與安全提醒、事件分類查詢、事件頁（公開版小報告）、改善進度、關於本站）；每頁頁尾標明「個人專題，非國立中央大學官方系統」並列出校安中心、110、119；兩套設計系統都要實作（ADR-0014）
  - 驗證：Vitest 元件測試；Playwright 公開瀏覽流程（納入 `make e2e`）
  - 範圍調整（使用者同意，2026-10-28）：公開網站放在根路徑，管理端與登入改從 `/admin` 進入（`App.tsx` 依路徑分流、`src/admin/router.ts`）；既有測試 `src/App.test.tsx`、`frontend/e2e/admin.e2e.ts`、`tests/e2e/fixtures.ts` 的登入入口改為 `/admin`；`tests/e2e/seed_appendix_a.py` 最後執行 `sync_all`；新增 `src/test/publicFixtures.ts`、`frontend/e2e/public.e2e.ts`、`tests/e2e/public.e2e.ts`
  - 實作調整（AI 自行決定，2026-10-28）：頁面只用 `@design` 既有元件與語意 token，不新增設計元件（兩套設計直接適用）；首頁以 `/feed` 呈現時間軸，可切換只看 `/notices`；安全提醒沒有連結；小報告的影響只顯示面向與理由、不顯示分數；來源連結只接受 http(s)；類別篩選放在元件狀態
- **P5-T13 匿名回報入口**
  - 檔案：`app/api/v1/public/reports.py`（不保存 IP 與裝置資訊；速率限制只用短期雜湊；長度與格式限制）、`app/ingestion/adapters/anonymous_report.py`（以 `<untrusted_source>` 進入既有管線，視為獨立來源）、公開前端回報表單（說明投稿不會公開原文）
  - 驗證：`tests/security/test_anonymous_report.py`（不落地 IP、prompt injection 無法改變分析指令、投稿原文不出現在公開端、洪水投稿被限速）
  - 範圍調整（使用者同意，2026-10-28）：新增公開寫入端點 `POST /api/v1/public/reports`，`tests/integration/test_auth.py` 的公開路由規則改為「GET，另外只允許這一條 POST」；投稿先寫入收件匣表再由 Adapter 拉取（`infra/migrations/versions/0014_anonymous_reports.py`、`app/models/anonymous_report.py`、`app/models/__init__.py`）；`docs/governance/source-inventory.md` 登記 `anonymous-report` 為核准來源、`config/sources.yaml` 加入該來源；`backend/Dockerfile` 加 `--no-access-log`；`app/api/v1/public/__init__.py`、`route.py`（`client_host` 改為公開函式）、`app/main.py`（限速器）、`app/ingestion/adapters/__init__.py`；前端 `routes.ts`、`api.ts`、`PublicApp.tsx`（導覽列）、`routes.test.ts`；`threat-model-public.md`、`rbac.md`
  - 實作調整（AI 自行決定，2026-10-28）：收件匣只有 id（遞增，作為 cursor）、內容、地點、收到時間，資料不刪除；限速為每用戶端 10 分鐘 3 則、全站 10 分鐘 30 則，在解析前計數（格式錯誤也算）；只收 `application/json`、本文最多 8 KiB、內容 10–2000 字、地點最多 100 字、只允許 `content`／`location`，拒絕控制字元與雙向文字控制字元；回 202 與固定內容、不回顯也不回傳 id；稽核 `anonymous_report.submit` 只記字數與有無地點；來源代碼 `anonymous-report`（非 `ncu-*`，不是官方來源），每 10 分鐘拉取（既有測試的擷取間隔下限），內容為「地點：…」加本文，網址為 `report_url?id=<id>` 作為內部識別；前端 `/report` 頁，送出時不帶 cookie
- **P5-T04 可觀測性**
  - 檔案：`app/core/metrics.py`（Prometheus：任務成功率、AI 延遲與費用、公開 API 錯誤率與限速次數）、`infra/monitoring/`（Prometheus、Grafana dashboard JSON、告警規則）
  - 驗證：Grafana 顯示上述指標；trace_id 貫穿 API→worker→AI run
  - 實作調整（AI 自行決定，2026-10-28）：新增依賴 `prometheus-client`；api 與 worker 子行程各自在 `NCU_METRICS_PORT`（compose 設 9100）輸出 `/metrics`，只在 Docker 內部網路、不發布到主機也不經通道，不在 FastAPI 路由上；指標為 `ncu_task_runs_total`／`ncu_task_duration_seconds`（Celery 訊號）、`ncu_ai_runs_total`／`ncu_ai_run_latency_seconds`／`ncu_ai_cost_usd_total`／`ncu_ai_daily_cost_limit_usd`（`run_skill` 寫入紀錄後；上限值取自 `config/llm.yaml`，worker 啟動時先寫入）、`ncu_http_requests_total`／`ncu_http_request_duration_seconds`（依 public／admin／auth／other 介面、路由樣板、狀態類別，不記原始路徑、IP、request_id）、`ncu_public_rate_limited_total`（public_read、report）；trace：送出 Celery 任務時把 request_id 放進標頭 `trace_id`，worker 執行前綁定（格式不安全時另產生），`analyze_item`／`analyze_event`／`generate_report` 沒有 trace 參數時沿用；worker 與 beat 日誌改為 JSON；compose 新增 prometheus（127.0.0.1:9090，保留 30 天）與 grafana（127.0.0.1:3000，必填 `GRAFANA_ADMIN_PASSWORD`、關閉匿名與註冊、dashboard 由 repo 管理）；告警規則 7 條（抓取失敗、任務失敗率、AI 延遲、AI 失敗率、24 小時費用達每日上限 80%、公開 API 5xx 比例、限速暴增），不設 Alertmanager、不對外送通知；另修改 `app/main.py`、`app/core/config.py`、`app/worker/celery_app.py`、`app/ai/runner/run.py`、`app/api/v1/public/{route,reports}.py`、`app/pipeline/analyze_item.py`、`app/reports/service.py`、`.env.example`、`infra/README.md`
- **P5-T05 備份與還原**
  - 檔案：`infra/scripts/backup.sh`（pg_dump）、`infra/scripts/restore.sh`、`docs/runbooks/restore.md`
  - 驗證：還原演練紀錄 `docs/progress/records/P5-restore-drill.md`
- **P5-T06 本機常駐運行**
  - 檔案：`infra/docker/docker-compose.local.yml`（restart 策略、所有服務只綁定 127.0.0.1）、`docs/runbooks/local-operation.md`（開機自動啟動、P1-T13 排程檢查）
  - 驗證：重新開機後服務自動恢復，所有服務只監聽 127.0.0.1
  - 實作調整（AI 自行決定，2026-10-28）：覆寫檔對 8 個服務設 `restart: unless-stopped` 與 json-file 日誌輪替（10 MB × 5），有發布的埠號以 `!override` 取代基礎檔並只綁 127.0.0.1；新增 `backend/tests/unit/test_local_compose.py`（解析兩份 compose 檢查上述規則）、`Makefile` 的 `up-local`（疊上覆寫檔啟動）與 `listen`（列出 Docker 監聽的主機埠）；開機自動啟動靠 Docker Desktop 的登入時啟動，不另寫 launchd；更正 `infra/README.md`、`docs/runbooks/README.md` 過時的 prod.yml／caddy／deploy.md 敘述
  - 範圍（使用者決定，2026-10-28）：資料庫角色分離與備份排程不併入本任務
- **P5-T15 通道對外上線**（需你決定：通道服務、網域、上線時間）
  - 檔案：`infra/tunnel/`（通道設定：只轉送公開網站與 `/api/v1/public/*`，管理端與登入不經通道）、`docs/runbooks/deploy.md`（上線、暫停、緊急下線步驟）
  - 驗證：從外部網路只能存取公開路徑；`/admin`、`/api/v1/admin/*`、登入從外部一律無法連線
  - 決定（使用者，2026-10-28）：Cloudflare Quick Tunnel、不使用網域（網址每次啟動都會換）；本任務只完成設定、不上線，G5-8 通過後依 `docs/runbooks/deploy.md` 自行上線
  - 實作調整（AI 自行決定，2026-10-28）：Quick Tunnel 不支援 ingress 路徑規則，改由公開閘道 `infra/tunnel/nginx-public.conf`（nginx，127.0.0.1:8090）做白名單，通道只連到閘道；`infra/tunnel/docker-compose.tunnel.yml` 疊在基礎檔與常駐覆寫檔上（tunnel 放在 profile `tunnel`，需明確啟動），並給 api 設 `FORWARDED_ALLOW_IPS`，閘道以 `CF-Connecting-IP` 覆寫 `X-Forwarded-For`，讓既有限速依每個用戶端計算；閘道不記存取日誌、清掉 Cookie／Authorization／UA／Referer、公開 API 加 `Cloudflare-CDN-Cache-Control: no-store`；`infra/tunnel/tunnel.sh` 提供 gateway／check／up／url／down／status，`check` 對運行中的閘道（或外部網址）逐一驗證放行與拒絕的路徑；另新增 `backend/tests/unit/test_tunnel_config.py`（設定檔不變條件），更新 `threat-model-public.md` 中交給 P5-T15 的待辦

```bash
cd backend && uv run pytest tests/unit/publishing tests/security tests/integration/test_public_api.py tests/integration/test_takedown.py -q
make e2e
bash infra/scripts/backup.sh && bash infra/scripts/restore.sh --target local
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G5-2 | 備份可完整還原 | 還原演練紀錄 |
| G5-5 | 疑似指控、推測原因、FB 原文與投稿原文不出現在公開端（P8-T06 起依 ADR-0018 改為：推測原因、FB 原文與投稿原文不出現；未查證與疑似指控內容帶標籤並經 PII 遮蔽） | test_public_exposure、test_anonymous_report |
| G5-6 | 公開端與附錄 A 端到端測試全過 | `make e2e` |
| G5-7 | 下架即時生效並留稽核 | test_takedown |
| G5-8 | 上線前確認威脅模型與公開端點清單 | 使用者本人審閱 `threat-model-public.md` 並同意上線 |

---

## P6 把點連成線與韌性成績單

目標：跨事件關聯經人工確認可用，趨勢偵測回測不使用未來資料，韌性成績單（發現力、行動力、恢復力、進步力）在資料不足時誠實顯示 N/A（原 P4，ADR-0015）。前置：G5 通過。

- **P6-T01 關聯圖資料與 API**（原 P4-T01）
  - 檔案：`app/relations/service.py`（same_event／related_event／hypothesized_cause，邊存 evidence_ids、方法、分數、人工決策）、`app/api/v1/admin/relations.py`（查詢、確認、駁回）
  - 驗證：`tests/integration/test_relations.py`（禁止 self-loop、人工駁回可回滾）
  - 實作調整（AI 自行決定，2026-10-28）：新增 migration `0015_relation_decisions.py`（type 加入 hypothesized_cause；新增 method（ai／rule／manual，既有列回填 ai）、decision（pending／confirmed／rejected）、decided_at、decided_by、decision_note，並約束 pending ⇔ 無決策者與時間；順便把 0005 雙重前綴的 type 約束改為與 model 一致的名稱，降版還原並刪除 hypothesized_cause 列）、`app/relations/__init__.py`，修改 `app/models/ai.py`（EventRelation）、`app/api/v1/admin/__init__.py`（掛上路由）；邊沿用既有 `evidence` 欄位，API 以 `evidence_ids` 輸出；決策只能 pending → confirmed／rejected，已決定的只能回到 pending（回滾，清除決策者、時間、備註，邊與證據不變），不能直接翻轉；確認、駁回、回滾共用 `POST /api/v1/admin/relations/{id}/decision`，稽核 `relation.decide`（from／to／type）即決策歷史；查詢 `GET /api/v1/admin/relations`（event_id、type、decision、include_rejected，預設不含駁回）與 `GET /api/v1/admin/relations/{id}`，回傳 Cytoscape 用的 nodes／edges；新增關聯只在 service（`add_relation`：禁止 self-loop、同一對事件同類型不論方向只能一條、evidence_ids 必須屬於兩端事件、hypothesized_cause 至少一個證據），不開新增端點
- **P6-T02 關聯圖介面**（原 P4-T02）
  - 檔案：`frontend/src/admin/pages/RelationGraph.tsx`（Cytoscape.js）
  - 驗證：Playwright 確認／駁回流程
  - 實作調整（AI 自行決定，2026-10-28）：新增依賴 `cytoscape`（自帶型別），`AdminApp.tsx` 以 `React.lazy` 載入關聯圖頁，Cytoscape 不進主 bundle；修改 `admin/api.ts`（`listRelations`、`decideRelation` 與型別）、`admin/format.ts`（類型／方法／決策標籤）、`admin/router.ts` 與 `AdminApp.tsx`（`/admin/relations`、導覽「事件關聯」）；測試新增 `RelationGraph.test.tsx`（vitest，Cytoscape 以假模組替代）、`e2e/relations.e2e.ts`，並擴充 `e2e/mockApi.ts`、`src/test/fixtures.ts`。圖只呈現結構（點邊可選取），確認、駁回、回滾一律在可鍵盤操作的清單與詳情中進行；已決定的只顯示「回滾到待確認」，不能直接翻轉；駁回後留在畫面上可立即回滾，重新載入後預設隱藏，勾選「包含已駁回」才顯示；推測原因標示「推測，尚未查證」與「只在後台」。顏色取自設計 token，兩套設計通用
- **P6-T03 時序統計與異常偵測**（原 P4-T03）
  - 檔案：`app/analytics/timeseries.py`（週／月頻率、地點分布、EWMA、Poisson 變化偵測）、`infra/migrations/versions/<下一個編號>_analytics_views.py`（materialized views）
  - 驗證：`tests/unit/analytics/test_timeseries.py`（合成資料注入突增可被偵測）
  - 實作調整（AI 自行決定，2026-10-28）：migration 為 `0016_analytics_views.py`（`analytics_event_counts_week`／`_month`，依週期起日、類別、地點名稱計數；事件日期與再發生偵測一致：S02 發生日期，沒有時用建立時間的台北日期；排除 false 與類別 none；週以 ISO 週一起算；不自動更新，由 `refresh_views` 在目前交易內整理）；新增 `app/analytics/__init__.py` 與 `tests/integration/test_analytics_views.py`（views 與 Python `bucket()` 結果一致、只在整理後變動、日期範圍篩選）；變化偵測只看突增：第 t 期期望值為之前各期的 EWMA（不看當期與未來，供 P6-T04 回測），Poisson 右尾 p 值 < 0.001 且當期至少 5 件才判定、至少 4 期歷史、期望值下限 0.5；參數為 `DetectionParams` 預設值（純統計參數，不放設定檔）；`load_counts` 讀出的地點名稱經 `mask_pii()`；本任務不加排程與 API，由 P6-T04／P6-T05／P7-T01 呼叫
- **P6-T04 趨勢回測**（原 P4-T04）
  - 檔案：`scripts/eval/backtest_trends.py`（滾動切點，只用切點前資料）、`tests/unit/analytics/test_no_leakage.py`
  - 驗證：洩漏測試通過；回測報告寫入 `eval/reports/trends/`
  - 實作調整（AI 自行決定，2026-10-28）：邏輯放在新增的 `app/analytics/backtest.py`，腳本只解析參數、讀資料庫、寫報告（沿用 `run_eval.py` 慣例，測試可直接匯入）；切點對齊週期起日，代表台北時間當天 00:00 當下，只用已建立（created_at 早於切點）且發生日期早於切點的事件，晚到的回報不算；每個切點以 EWMA 預測從切點開始那一期的件數（歷史不足 `min_history` 期時不預測），並以 `detect_changes` 判定前一期是否突增；評分用 `as_of`（執行當天）已知的全部事件：預測 MAE（只對已結束的週期）、線上與事後突增的一致（precision／recall，分母為 0 時為 null）、切點當下對前一期件數的完整度；範圍為全部與各類別；報告 `eval/reports/trends/backtest-<step>-<start>.json` 只含件數與統計，不含地點與內文；查證狀態與類別沿用目前值（事件無逐版歷史），列為已知限制
- **P6-T05 校園韌性成績單 CRI（後台與公開版）**（原 P4-T06）
  - 檔案：`app/resilience/cri.py`（四構面標準化，資料不足回傳 None）；後台報告頁加入資料覆蓋率、時間窗、樣本量、來源限制；公開版成績單頁標明「回報變多不等於校園變危險」
  - 驗證：`tests/unit/resilience/test_cri.py`（缺資料顯示 N/A，不產生假精確分數）
  - 實作調整（指標定義與公開端點經使用者 2026-10-28 同意，其餘 AI 自行決定）：發現 = 事件建立台北日期 − S02 發生日期（負值算 0）的中位數，錨點 1／14 天；回應 = 事件建立到第一項改善措施進入處理的中位數，1／30 天；恢復 = 措施第一次處理到之後第一次恢復的中位數，3／60 天；調適 = 改善完成率（改進 ÷ 不含不追蹤）與 1 − 重複事件比例（時間窗內同地點同類別不只一件）的平均；錨點間線性換算；任何指標樣本 < 5 → 構面 None，四構面齊全才算 CRI、不重新加權，分數取整數；時間窗預設 180 天（後台可選 7–730），只用 as_of 當天結束前的事件與狀態變更，排除 false 與類別 none；參數為 `CriParams` 預設值。新增 `GET /api/v1/admin/resilience`（`app/api/v1/admin/resilience.py`，reports:read_internal）與公開端點 `GET /api/v1/public/resilience`（`app/api/v1/public/resilience.py`，只統計以事件內容公開中的事件、時間窗固定 180 天、只回彙總數字與限制說明，不含事件、地點、措施或 ID）；前端新增 `src/lib/resilience.ts`（共用型別與格式）、`admin/pages/Resilience.tsx`（`/admin/resilience`，可切換時間窗）、`public/pages/Resilience.tsx`（`/resilience`，醒目標明「回報變多不等於校園變危險」）及對應測試與 `src/test/resilienceFixtures.ts`，修改兩邊的路由、導覽與 api；公開閘道白名單（`infra/tunnel/nginx-public.conf`、`tunnel.sh` 檢查清單、`docs/runbooks/deploy.md`）加入 `/resilience`，`docs/architecture/threat-model-public.md` 列入新端點；新增 `tests/integration/test_resilience_api.py`。限制說明避開來源名稱（`test_public_exposure` 禁止公開回應出現 Facebook）

```bash
cd backend && uv run pytest tests/unit/analytics tests/unit/resilience tests/integration/test_relations.py -q
uv run python ../scripts/eval/backtest_trends.py --start 2026-01-01 --step week
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G6-1 | 關聯經人工評估可用 | 人工審閱全部現有關聯（2026-11-04 為 15 組），正確率 ≥ 0.85（原為抽 50 組，ADR-0016） |
| G6-2 | 回測無未來資料 | test_no_leakage |
| G6-3 | 韌性成績單不假精確 | test_cri |

---

## P7 What-if 模擬與收尾

目標：完成 S09–S10 與 What-if，驗證 Skills 能在其他 AI 客戶端運作，並更新規格書（原 P5，ADR-0015）。前置：G6 通過。

- **P7-T01 S09 trend-forecaster**（原 P5-T01）：`skills/trend-forecaster/`；輸入 P6 的統計結果，只產生「異常解釋與前瞻訊號」，必須標註聲量≠事件數、資料覆蓋率
  - 實作調整（AI 自行決定，2026-11-04）：使用者決定 G6-1 暫不審閱（主資料庫關聯只剩 2 組），先做本任務；輸入為 P6-T03 的序列與突增結果（`series`，最多 6 個訊號）、程式算好的資料覆蓋率（`coverage.level` full／partial／low）與異常週期的事件摘要（`evidence`）；異常與數字都由程式決定，模型只解釋 `anomaly` 不為 null 的訊號（driver：incident_cluster／repeated_reports／coverage_change／seasonal／unexplained，basis：supported／hypothesized）並提出不含件數的質性前瞻訊號；`caveats` 一律含 volume_not_event_count，覆蓋率不是 full 時含 limited_coverage；覆蓋率 low → abstain insufficient_data，沒有異常 → no_anomaly；評測指標為衍生欄位 `explained_signals`（accuracy ≥ 0.90）與 `caveats_complete`（= 1.0），寫在 `eval/thresholds.yaml`，衍生欄位在 `app/ai/eval/derived.py`；本任務不接後端管線與 API
- **P7-T02 What-if 引擎 + S10 scenario-simulator**（原 P5-T02）
  - 檔案：`app/simulations/engine.py`（baseline × Π(1 − effect_i)、效果區間、敏感度、固定隨機種子）、`skills/scenario-simulator/`（只負責解釋假設與限制，數值由引擎計算）、`infra/migrations/versions/<下一個編號>_scenario_runs.py`
  - 驗證：`tests/unit/simulations/test_engine.py`（同輸入同種子結果一致；效果值未知時回傳「無法估計」）
  - 實作調整（管理端 API 經使用者 2026-11-04 同意放進本任務，其餘 AI 自行決定）：migration 為 `0017_scenario_runs.py`（id、event_id〔事件刪除時設 NULL〕、input〔含 seed 與措施名稱、效果、成本、依據〕、output〔引擎結果〕、engine_version、seed、ai_run_id〔S10 的 ai_analysis_runs〕、created_by、created_at）；新增 `app/models/scenario.py`、`app/simulations/__init__.py`、`app/simulations/service.py`（建立、查詢、S10 輸入組裝）、`app/api/v1/admin/scenarios.py`（`POST /api/v1/admin/scenarios` 回 201，scenarios:create，稽核 `scenario.create`；`GET /api/v1/admin/scenarios`〔最近 50 筆，可依 event_id 篩選〕與 `GET /api/v1/admin/scenarios/{id}`，events:read_all；只在管理端），修改 `app/api/v1/admin/__init__.py`、`app/models/__init__.py`、`docs/architecture/rbac.md`；測試新增 `tests/integration/test_scenarios_api.py`、`tests/unit/ai/test_eval_scenario.py` 與空的 `tests/unit/simulations/__init__.py`（與 `tests/unit/risk/test_engine.py` 同名，需要套件名稱區分）。引擎：effect 是件數降低比例的假設區間 [low, high]（0–1）；點估計取中點，範圍為效果全取上限／下限，分位數 p05／p50／p95 以固定種子 Monte Carlo（均勻、獨立，2000 次）；敏感度一次只移動一項措施、其他固定在中點；效果未知的措施排除並列出，全部未知為 not_estimable（不呼叫 S10）；成本分級低於 5 萬 low、低於 50 萬 medium、其餘 high，未填為 unknown；參數為 `EngineParams` 預設值；措施最多 10 項，代號依序為 m1…m10；未給種子時以 `secrets` 產生並存下。S10 在請求內同步執行，失敗時仍保存引擎結果；措施名稱與依據以 `<untrusted_source>` 交給 S10；評測指標為 `explained_measures`、`key_driver`、`limitation_set`（accuracy ≥ 0.90）與 `hypothetical_flagged`（= 1.0），衍生欄位在 `app/ai/eval/derived.py`
- **P7-T03 What-if 介面**（原 P5-T03）
  - 檔案：`frontend/src/admin/pages/Scenario.tsx`（參數表單、龍捲風敏感度圖、明顯的「假設情境，非成效預測」標示）
  - 驗證：Playwright
  - 實作調整（AI 自行決定，2026-11-04）：路徑 `/admin/scenarios`（導覽「What-if 模擬」），沿用 P7-T02 的管理端 API，不新增端點；修改 `admin/api.ts`（型別、`createScenario`、`listScenarios`）、`admin/router.ts`、`AdminApp.tsx`；測試新增 `Scenario.test.tsx`、`e2e/scenario.e2e.ts`、`src/test/scenarioFixtures.ts`，擴充 `e2e/mockApi.ts`。效果以百分比輸入、換成 0–1 送出，上下限都留空為效果未知；頁首警示區塊與每次結果都標示「假設情境，非成效預測」；龍捲風圖依 rank 排序、直線為點估計、每列附文字數值（長條只是輔助）；not_estimable 顯示「無法估計」不畫圖；S10 失敗時仍顯示引擎結果；介面不選關聯事件、基準件數手動填寫
- **P7-T04 Skills 可攜性驗證**（原 P5-T08）
  - 檔案：`docs/portability-report.md`；將 S01–S10 放進另一個支援 Agent Skills 的客戶端（例如 Claude Code）跑 golden cases，比較與 Runner 結果
  - 驗證：記錄各 skill 在不同平台的通過率與需調整之處
- **P7-T05 規格書 v1.1 更新**（原 P5-T09）
  - 檔案：`docs/requirements/spec-v1.1.md`（實際來源、schema、門檻、部署規格的變更；含 ADR-0008 與 ADR-0015 的定位變更）
  - 驗證：與 v1.0 差異清單完整
- **P7-T06 S09 管線串接與資料覆蓋率**（使用者 2026-11-04 決定新增；P7-T01 只建立 Skill，沒有呼叫端）
  - 檔案：`app/analytics/coverage.py`（依 `ingestion_runs` 計算統計期間各核准來源的實際擷取天數 ÷ 應擷取天數，產生 S09 輸入的 `coverage`：level full／partial／low、ratio、note；分級門檻在實作時決定並記錄）、`app/analytics/forecast.py`（由 P6-T03 的 `load_counts` 與突增偵測組出 S09 的 `series`，取異常週期中的事件標題與摘要（經 `mask_pii()`）為 `evidence`，經 Skill Runner 呼叫 S09，結果寫入 `ai_analysis_runs`）、`app/api/v1/admin/trends.py`（管理端 API：查詢最近一次趨勢解讀、手動觸發；只在 `/api/v1/admin/*`，不對外公開）、後台顯示（在既有的管理頁加上「趨勢解讀」區塊，聲量≠事件數與覆蓋率的標註要明顯）
  - 驗證：`tests/unit/analytics/test_coverage.py`（覆蓋率分級、新增來源與擷取缺漏的說明、全部缺資料為 low）、`tests/integration/test_trend_forecast.py`（fake provider；沒有異常時不呼叫模型或得到 no_anomaly；輸入的數字與 P6-T03 一致；結果不經公開端點）
  - 排程（是否每週自動執行）由實作時提出選項，使用者決定
- **P7-T07 人工確認原因**（使用者 2026-11-04 決定新增，ADR-0017；推測原因的學校措施在公開端全部被濾掉）
  - 檔案：`infra/migrations/versions/<下一個編號>_report_cause_decisions.py`（小報告中各原因的人工決策：確認依據、決定者、時間）、`app/reports/cause_decisions.py`（確認、回滾；只限最新一份有效小報告中 basis 為 hypothesized 的原因；確認依據必填、經 PII 檢查；稽核 `report.cause_decide`）、管理端 API（掛在既有的 `app/api/v1/admin/events.py` 小報告路由下，只在 `/api/v1/admin/*`）、`app/publishing/policy.py` 與 `app/publishing/sync.py`（措施依據的原因經人工確認時視為 confirmed；疑似指控、PII 遮蔽規則不變）、後台 `frontend/src/admin/pages/EventWorkbench.tsx`（推測原因旁的「確認原因」與回滾，需填確認依據）、`docs/architecture/threat-model-public.md`
  - 驗證：`tests/integration/test_cause_confirmation.py`（確認後相關措施進入公開快照、回滾後撤下；疑似指控事件確認後仍不公開內容；沒有確認依據或含個資時拒絕；重新產生小報告後需重新確認）、`tests/unit/publishing/` 新增案例（既有 G5-5 測試不修改）、前端 vitest
  - 公開頁不標示「人工確認」（使用者決定）
  - 實作調整（AI 自行決定，2026-11-04）：migration 為 `0018_report_cause_decisions.py`（append-only，最新一筆為目前狀態，保留確認依據歷史），ORM model 放在 `app/models/report.py`（修改 `app/models/__init__.py`）；端點 `POST /api/v1/admin/events/{id}/report/causes/{cause_id}/decision`（body 帶 report_id，報告已重新產生或失效回 CONFLICT；`events:verify`），事件詳細的原因附 `manual_confirmation`；公開規則以 `ReportInput.confirmed_cause_ids`、`ActionInput.cause_confirmed` 判斷，改善措施只認同一份報告的確認；修改 `admin/api.ts`、`docs/architecture/rbac.md`；單元測試新增 `tests/unit/publishing/test_cause_confirmation_policy.py`
  - 實作調整（排程經使用者 2026-11-04 決定只手動觸發，其餘 AI 自行決定）：端點為 `GET`／`POST /api/v1/admin/trends/forecast`（events:read_all／events:analyze，稽核 `trend_forecast.run`），修改 `app/api/v1/admin/__init__.py`、`docs/architecture/rbac.md`；覆蓋率分級 low：ratio < 0.5 或無來源，full：ratio ≥ 0.95、每個來源 ≥ 0.9 且期間內沒有新增來源，其餘 partial；截止日預設台北昨日，往前 26 週／12 個月但不早於最早核准來源新增日；最近 4 期內的突增優先、最多 6 個訊號、12 個事件（evidence_id 為事件 id）；覆蓋率 low 或沒有異常時不呼叫模型；最近一次以稽核紀錄為準，不新增資料表；後台顯示放在韌性成績單頁（`Resilience.tsx`），修改 `admin/api.ts`，測試新增 `TrendForecast.test.tsx`

```bash
cd backend && uv run pytest tests/unit/simulations -q
uv run python ../scripts/eval/run_eval.py --skill trend-forecaster --skill scenario-simulator --live
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G7-1 | 模擬結果可重現 | test_engine |
| G7-2 | Skills 可攜 | portability-report.md |

---

## P8 系列事件與公開內容擴充

目標：相似事件可以成系列觀察（頻率、規律、改善後是否下降）；地點由 AI 判斷；公開內容放寬但帶標籤、可下架；補做 v1.0 缺口（ADR-0018）。前置：各任務列出的已完成任務；與 G6-1、G7-2 同時進行（使用者決定，ADR-0018）。

新產品 Skill 依「Skill 開發標準循環」開發：S11 series-matcher、S12 series-analyst、S13 location-resolver。S12、S13 輸出引用的事件 id 必須屬於輸入、逐字證據必須出現在該事件的證據中，否則整項丟棄；計數一律由程式依引用的事件 id 計算，模型不寫數字。

- **P8-T01 手動合併／拆分事件**（v1.0 F03，D24）
  - 檔案：`infra/migrations/versions/<下一個編號>_event_merges.py`（合併紀錄：被併入事件、目標事件、決定者、時間；可 downgrade）、`app/events/merge.py`（合併：來源文章、證據、措施移到目標事件，被併入的事件標為 merged 並導向目標；拆分：把指定來源文章移到新事件並重新分析；稽核 `event.merge`、`event.split`）、管理端 API（掛在 `app/api/v1/admin/events.py`）、後台事件工作台的合併／拆分操作
  - 驗證：`tests/integration/test_event_merge.py`（合併後公開快照只剩目標事件、被併入事件的公開內容撤下；拆分後兩邊各自重新分析；合併後 S04 不再把同一篇文章併回被併入的事件）、前端 vitest
  - 實作調整（AI 自行決定，2026-11-05）：migration 為 `0019_event_merges.py`（id、merged_event_id〔唯一〕、target_event_id、decided_by、decided_at；兩個事件 id 不同；任一事件刪除時 CASCADE）；「標為 merged」以 archived 狀態＋event_merges 導向表示，不新增事件狀態（新增狀態要改 P0-T06 的窮舉狀態機測試），archived 已被候選召回、公開同步、通知與小報告排除；分析中（detected、analyzing）或已封存的事件不能合併或拆分（CONFLICT）；合併時文章的 S01–S03 執行紀錄跟著文章移動，目標事件的說明依 P4-T08 規則從移入文章的 S02 結果重挑，被併入事件的小報告失效（`event_merged`），事件關聯改指向目標事件（連到目標本身的刪除；同一對事件同類型只留一條，已有人工決策的優先），同一交易內同步通知與公開狀態；拆分的新事件標題由使用者填寫（經 PII 檢查），沿用原事件的類型、地點與開始日期，從 analyzing 開始，措施與小報告留在原事件；拆分時兩邊的向量清空，重跑分析時依各自最早的文章重新計算（使用者 2026-11-05 要求；修改 `app/pipeline/analyze_item.py`：`analyze_event` 遇到沒有向量的事件先補算，失敗不影響 S05、S06）；端點 `POST /api/v1/admin/events/{id}/merge`（200）與 `/split`（201），`events:verify`，提交後把目標事件（合併）或兩個事件（拆分）排入重跑 S05、S06；事件詳細新增 `merged_into`；新增 `app/events/__init__.py`、ORM `EventMerge` 放在 `app/models/event.py`（修改 `app/models/__init__.py`），修改 `admin/api.ts`、`src/test/fixtures.ts`（`merged_into: null`）、`docs/architecture/rbac.md`
- **P8-T02 S13 location-resolver 與代表地點**（ADR-0018 決策 1-5）
  - 檔案：`skills/location-resolver/`（輸入：新地點名稱與其證據原句、既有代表地點清單；輸出：alias_of／inside_of／new 與目標地點 id、理由、逐字證據；資訊不足時 abstain，當成新地點）、`infra/migrations/versions/<下一個編號>_location_canonical.py`（`locations` 加 `canonical_id`、`parent_id`；地點判斷紀錄表含使用者拆開的決定）、`app/locations/resolver.py`（新名稱才呼叫 S13，同名稱沿用；拆開後不再自動合併；既有地點補判一次的指令；稽核 `location.resolve`、`location.split`）、修改 `app/pipeline/analyze_item.py`、後台地點檢視與拆開操作
  - 驗證：golden cases ≥ 20（別名、包含、同名不同地、提示注入、只有「學校」這類無效地點）；`tests/integration/test_location_resolver.py`（fake provider；同名稱不重複呼叫；abstain 時建新地點；拆開後不再合併；補判可重跑且結果相同）
  - 實作調整（評測門檻經使用者 2026-11-05 決定，其餘 AI 自行決定）：門檻 `decision_accuracy`、`alias_precision`（alias_of）、`target_accuracy` 皆 ≥ 0.90（寫入 `eval/thresholds.yaml`）；golden cases 24 筆（dev／test 各 12）；migration 為 `0020_location_canonical.py`（`locations.canonical_id`／`parent_id`，自參照 SET NULL；`location_resolutions`：decision alias_of／inside_of／new／split、target_id、method ai／fallback／manual、ai_run_id、decided_by、created_at，最新一筆為目前狀態）；別名攤平指向代表地點；候選為最多 20 個代表地點（字元重疊度排序，代號 L1…）；「全校」「校外」直接建立、不判斷、不當候選；沒有候選或證據時不呼叫；abstain、失敗、target_id 不在候選中一律當新地點（method fallback）；有呼叫 S13 時才寫稽核 `location.resolve`；補判命令 `uv run python -m app.locations.resolver backfill`（依名稱排序、證據取地點最新事件最早加入的文章，已有紀錄的地點跳過）；後台 API `GET /api/v1/admin/locations`（events:read_all）、`POST /api/v1/admin/locations/{id}/split`（events:verify）；後台頁 `/admin/locations`（導覽「地點」）；新增 `app/locations/__init__.py`、`app/api/v1/admin/locations.py`、`frontend/src/admin/pages/Locations.tsx`、`Locations.test.tsx`，修改 `app/models/event.py`、`app/models/__init__.py`、`app/api/v1/admin/__init__.py`、`admin/api.ts`、`admin/router.ts`、`AdminApp.tsx`、`docs/architecture/rbac.md`
- **P8-T03 再發生與突增偵測改用代表地點**
  - 檔案：`app/resilience/recurrence.py`、`app/analytics/timeseries.py`、`app/analytics/forecast.py`（依 `canonical_id` 分組，顯示代表地點名稱；`parent_id` 不併入計數）
  - 被手動合併的事件不計數（使用者 2026-11-05 決定從 P8-T01 移到本任務，ADR-0018「合併後只算 1 件」）：`app/resilience/recurrence.py`、`app/resilience/cri.py` 與時序統計 view（新 migration 修改 0016 的 view）排除有 `event_merges` 紀錄的事件；驗證新增合併後只算 1 件的案例
  - 驗證：`tests/unit/resilience/test_recurrence_canonical.py`、`tests/unit/analytics/` 新增別名合併案例；P4-T10、P6-T03 既有測試不修改且通過；`config/recurrence.yaml` 門檻不變
  - 實作調整（CRI 重複事件改用代表地點經使用者 2026-11-05 決定，其餘 AI 自行決定）：migration 為 `0021_analytics_views_canonical.py`（重建週／月 views；downgrade 重建 0016 版本）；代表地點沿著 `canonical_id` 找到最上層（循環時維持自己），Python 版 `timeseries.representative()`／`load_locations()`，SQL 版為 views 中的 recursive CTE；views 仍依代表地點名稱分組；再發生以 `recurrence.canonicalize()` 換成代表地點 id 與名稱；CRI 的 `load_records` 把 `location_id` 換成代表地點（重複事件比例依代表地點）；被合併事件以 `NOT EXISTS event_merges` 在查詢中排除（再發生、CRI、views、趨勢解讀的證據）；測試新增 `tests/unit/analytics/test_timeseries_canonical.py`、`tests/integration/test_canonical_counts.py`
- **P8-T04 S11 series-matcher 與系列自動歸組**（ADR-0018 決策 1-1～1-4）
  - 檔案：`skills/series-matcher/`（輸入：新事件與同類別的候選系列／事件；輸出：是否同主題、目標系列、理由、逐字證據；不看地點）、`infra/migrations/versions/<下一個編號>_event_series.py`（系列、子事件關係、使用者拆出紀錄）、`app/series/grouping.py`（同類別＋S11 同主題即歸組，2 件成立；拆出後不再自動歸回；解散；子事件改判 false 移出、剩不到 2 件自動解散；合併後的事件只算 1 件；稽核 `series.*`）、管理端 API `app/api/v1/admin/series.py`
  - 驗證：golden cases ≥ 20；`tests/integration/test_series_grouping.py`（fake provider；以上每條規則各一案例；同一組輸入重跑結果相同）
  - ADR-0020 修改（使用者 2026-11-05 同意）：每個事件都屬於一個系列，S11 判斷不同主題時建立只有它一件的系列（第 1 起就有分析頁）；拆出或改判 false 移出的事件各自成為 1 件的系列，件數 0 的系列刪除（取代「2 件成立」「剩不到 2 件自動解散」）；驗證新增 1 件系列、拆出後自成系列的案例
- **P8-T05 S12 series-analyst、系列時間軸與前後比較**（ADR-0018 決策 1-5～1-8）
  - 檔案：`skills/series-analyst/`（輸入：系列的子事件摘要與證據、程式算出的件數序列與突增；輸出：爆發解釋、地點分組、規律、可能的共同原因、改善建議（同學／學校／相關單位）；規律與原因一律標為推測；證據不足時 abstain）、`app/series/analysis.py`（引用驗證、程式計數、每個規律至少 2 件；新事件加入時重新分析，每系列每天最多一次，受每日費用上限）、`app/series/timeline.py`（日／週／月件數、4 期移動平均、措施開始處理／完成標記、範圍 1M／3M／6M／1Y／全部；前後比較：措施開始前後各 8 週平均，任一邊少於 5 件回傳「資料還不夠」）、系列措施（`response_actions` 加系列層級；可從建議一鍵加入）、後台 `frontend/src/admin/pages/Series.tsx`（時間軸、地點篩選、規律、推測原因、措施）
  - 驗證：golden cases ≥ 20（含捏造規律、引用不存在事件、只有 1 件支持的規律）；`tests/unit/series/test_timeline.py`（移動平均、粒度、前後比較門檻、「變少不一定是措施的功勞」說明一律出現）；`tests/integration/test_series_analysis.py`（引用驗證丟棄、計數由程式算、每天最多一次）；前端 vitest（雙設計系統）
  - ADR-0020 修改（使用者 2026-11-05 同意）：S12 承接 S07、S08 的欄位（原因的 `to_verify`、改善建議的 `audience`／`horizon`／`priority`）；1 件的系列也分析（規律仍需 ≥ 2 件，否則該區 abstain）；原因與規律提到特定個人、群體、店家或單位時以既有指控比對標記（不擋，交給公開規則比照疑似指控處理）；系列措施與原因的人工確認（`series.cause_decide`，沿用 ADR-0017 的規則）；golden cases 新增 1 件系列、提到個人或單位的原因（須被標記）
- **P8-T06 公開規則放寬與公開系列頁**（ADR-0018 決策 2、1-9）
  - 檔案：`app/publishing/policy.py`、`app/publishing/sync.py`（未查證事件公開 AI 摘要並標「尚未查證」；疑似指控照原文摘要公開並立即在通知中心提醒、附一鍵下架；改善建議公開但不寫依據的原因；推測原因、FB 原文、投稿原文仍不公開；全部經 PII 遮蔽）、`app/api/v1/public/series.py`（系列頁：時間軸、子事件、前後比較、改善建議、改善進度；不含推測原因與規律）、`frontend/src/public/pages/Series.tsx`、`docs/architecture/threat-model-public.md`、`docs/architecture/rbac.md`、通道閘道白名單
  - 驗證：`tests/unit/publishing/test_publish_policy.py`、`tests/security/test_public_exposure.py` 依 G5-5 新定義修改（使用者 2026-11-05 同意，只限 ADR-0018 放寬的規則）；新增系列頁不含推測原因、規律與 FB 原文的案例；公開前端 vitest 與 `tests/e2e/public.e2e.ts`
  - ADR-0020 修改（使用者 2026-11-05 同意）：公開系列頁加上規律與可能原因（一律標「推測，尚未查證」，人工確認的標「已確認」；首次公開或改變時通知中心提醒並附一鍵下架；提到特定個人、群體、店家或單位的比照疑似指控公開並在提醒中註明）；公開首頁改為依類別分區的橫向滑動快報卡片（類別圖示與配色、查證標籤、系列第 N 起）；快報全文頁不含單一事件小報告，只連到系列分析頁；底部放韌性成績單與匿名回報入口；AGENTS.md 推測原因的硬性邊界與 G5-5 定義依 ADR-0020 修改；`test_public_exposure` 依新定義修改（使用者 2026-11-05 同意，只限 ADR-0020 放寬的規則），新增「提到個人或單位的原因公開時必定產生提醒與下架入口」「未帶推測標籤不得公開」的案例
- **P8-T07 通知失敗重試**（v1.0 A10，D41）
  - 檔案：`app/alerts/delivery.py`（failed 最多重試 3 次、遞增間隔；唯一約束仍防重複遞送）、`infra/migrations/versions/<下一個編號>_delivery_attempts.py`（嘗試次數、下次時間）、Celery 排程
  - 驗證：`tests/integration/test_delivery_retry.py`（第 3 次仍失敗就停止；成功後不再重送；不重複遞送）、`tests/e2e/a10-delivery.e2e.ts`
- **P8-T08 匿名回報收據碼與補件**（v1.0 §2、D11）
  - 檔案：`app/api/v1/public/reports.py`（送出時回傳高熵收據碼，只存雜湊；`GET` 查詢只回 已收到／處理中／已處理；`POST` 補件，同樣限流與格式限制，以 `<untrusted_source>` 進入既有管線）、`infra/migrations/versions/<下一個編號>_report_receipts.py`、公開通報頁的收據碼顯示與查詢
  - 驗證：`tests/security/test_report_receipt.py`（資料庫中沒有明文收據碼；錯的收據碼與不存在的回應相同；查詢不回投稿內容；補件原文不公開；限流）、`tests/security/test_anonymous_report.py` 不修改且通過
- **P8-T09 What-if 時間窗與地點**（v1.0 D29）
  - 檔案：`app/simulations/baseline.py`（依時間窗、類別、代表地點自動計算歷史基準件數）、修改 `app/simulations/service.py`、`app/api/v1/admin/scenarios.py`、`frontend/src/admin/pages/Scenario.tsx`（自動帶入、可手改）
  - 驗證：`tests/integration/test_scenario_baseline.py`（基準與 P6-T03 計數一致；手改值優先）；P7-T02 G7-1 測試不修改且通過
- **P8-T10 介面文案改為播報員語氣**（ADR-0018 決策 3）
  - 檔案：`docs/governance/tone-guide.md`（語氣規則、例外：緊急資訊與「尚未查證」標籤平實、不用表情符號、範例）、公開網站與後台介面文案、`app/alerts/templates/`
  - 驗證：前端 vitest 斷言的文案依新文案修改（使用者 2026-11-05 同意）；緊急資訊與「尚未查證」文案不變的測試
  - ADR-0020 修改（使用者 2026-11-05 同意）：匿名回報頁與校園韌性成績單頁改成白話（韌性四個構面改成四個問題、說明「幾天算好是本專題自己訂的目標」）；分數規則不變；緊急資訊與「尚未查證」維持平實
- **P8-T11 AI 產生內容改為播報員語氣**
  - 檔案：S02、S07、S08、S11、S12 的 `SKILL.md` 與 `tests/recorded/`、`eval/reports/<name>/<version>.json`
  - 驗證：每個 Skill 重錄、重跑 `--live` 評測、升版本；評測不低於現有門檻
- **P8-T12 規格書 v1.2 更新**（含 ADR-0020）
  - 檔案：`docs/requirements/spec-v1.2.md`（v1.1 差異清單；ADR-0018 的變更）
  - 驗證：與 v1.1 差異清單完整
- **P8-T13 停用單一事件小報告並遷移到系列**（ADR-0020 決策 3，使用者 2026-11-05 同意）
  - 檔案：`app/reports/service.py`、`app/pipeline/analyze_item.py`（不再排入 S07、S08，移除 `report_hook` 的產生報告）、`infra/migrations/versions/<下一個編號>_series_actions_backfill.py`（既有事件層級的改善措施與原因確認掛到所屬系列，保留原 event_id；可 downgrade、不刪資料）、`app/api/v1/admin/events.py`（舊小報告唯讀、停用產生報告端點回 GONE 或 CONFLICT）、`app/publishing/sync.py`（改依系列分析）、後台 `EventWorkbench.tsx`（「舊版事件小報告（已停用）」）、公開 `EventReport.tsx`（改為快報全文）、`skills/root-cause-analyzer/`、`skills/solution-generator/` 標示停用（不刪）
  - 驗證：`tests/integration/test_event_report.py`、`tests/integration/test_cause_confirmation.py`、`tests/unit/publishing/test_cause_confirmation_policy.py`、相關前端 vitest 與 `e2e/admin.e2e.ts` 改為驗證停用與遷移（使用者 2026-11-05 同意修改既有測試，只限 ADR-0020 決策 3）；新增 `tests/integration/test_report_migration.py`（措施與確認遷移後可在系列查到、舊資料唯讀、事件建立後不再呼叫 S07／S08）；G4-5 保留為歷史紀錄
- **P8-T14 校園地點清單匯入**（使用者 2026-11-05 要求，接續 P8-T02）
  - 檔案：`config/locations.yaml`（代表地點清單：正式名稱、平面圖代號、別名、上層地點；來自 2024-08 校園導覽圖與現有事件地點，使用者 2026-11-05 確認：常用簡稱全部採用、總圖書館與中正圖書館算同一個地點、K書中心／校史館／校史館旁為獨立地點）、`app/locations/seed.py`（匯入命令 `uv run python -m app.locations.seed`：清單名稱建立或沿用為代表地點，別名建立或改指向該代表地點，`parent` 設 `parent_id`；每個有變動的地點寫一筆 `location_resolutions`〔method manual、decided_by `seed`〕並以 P8-T02 的攤平規則改指向既有別名與下層地點；使用者拆開過〔最新一筆為 split〕的地點不覆蓋；清單外的地點不刪除；可重跑且結果相同；稽核 `location.seed` 只記數量與 list_version）
  - 匯入後：清單內的名稱同名稱直接沿用、不呼叫 S13；清單外的新名稱照 P8-T02 由 S13 從代表地點（含清單）判斷，alias_of 記成別名、new 新增代表地點，之後同名稱即可找到
  - 驗證：`tests/unit/locations/test_seed_file.py`（清單格式：名稱與別名全域不重複、不含「全校」「校外」「不明」、parent 必須在清單中、只允許 name／code／aliases／parent 欄位）；`tests/integration/test_location_seed.py`（匯入後別名同名沿用且不呼叫 S13；重跑結果相同；既有代表地點成為別名時其別名與下層地點攤平；拆開過的不覆蓋；清單外地點保留；補判跳過匯入過的地點）
  - 前置：P8-T02
- **P8-T15 依改善建議評估效益（S14 impact-evaluator）**（ADR-0019，使用者 2026-11-05 決定；ADR-0020 決策 6 改以系列為單位）
  - 檔案：`skills/impact-evaluator/`（依「Skill 開發標準循環」：SKILL.md、schema、golden cases、錄製回應、評測報告；輸入：系列總覽、子事件摘要、可能原因〔推測／已確認〕、全部改善建議並標示哪些被選、系列件數基準、改善追蹤進度，建議與事件內容以 `<untrusted_source>` 包裹；輸出：改善百分比區間 `improvement`〔整數 0–100 或 null〕、改善前／後情境 `before`／`after`〔播報員語氣〕、`covered_causes`／`uncovered_causes`、`overlaps`、`risks`〔至少 1 項〕、`suggestion`〔只能引用未被選的建議〕、`evidence_ids`）、`app/simulations/impact.py`（輸入組裝、業務驗證：`0 ≤ low ≤ high ≤ improvement_cap`、引用 id 存在、PII 檢查、推測原因不得寫成已確定；保存；改善前後件數由程式以 基準 ×（1 − 百分比）換算）、`infra/migrations/versions/<下一個編號>_impact_evaluations.py`（或擴充 `scenario_runs`，實作時決定並記錄；可 downgrade）、`app/api/v1/admin/scenarios.py`（`POST /api/v1/admin/scenarios/impact`，`scenarios:create`，稽核 `scenario.impact`；`GET` 查詢；只在 `/api/v1/admin/*`）、設定檔 `improvement_cap`（預設 80）、`frontend/src/admin/pages/Scenario.tsx`（新分頁「依建議評估」：只勾選建議、結果卡顯示改善百分比〔標「AI 估計，非實證」〕、改善前／後兩欄對照、涵蓋與未涵蓋原因、重疊提醒、風險、補強建議可一鍵加入再評估；原自訂措施模式保留為另一分頁；雙設計系統）、後台系列分析頁的「評估效益」入口（`/admin/scenarios?series=<id>`）
  - 驗證：S14 評測 `improvement_valid` = 1.0、`cause_coverage` accuracy ≥ 0.90、`abstain_when_insufficient`、`hypothesized_not_asserted` = 1.0；`tests/integration/test_impact_evaluation.py`（fake provider；沒有勾選時不呼叫模型；引用不存在的原因或建議 id 被拒；超過上限被拒；件數由程式換算；系列分析更新後舊評估標示過期）；`tests/security/` 新增公開快照與公開端點不含評估結果的案例；前端 vitest 與 Playwright（勾選到結果）；P7-T02／P7-T03 的 G7-1 測試不修改且通過
  - 前置：P8-T05、P8-T09、P8-T10、P8-T13

```bash
cd backend && uv run pytest tests/unit/series tests/security -q
uv run python ../scripts/eval/run_eval.py --skill series-matcher --skill series-analyst --skill location-resolver --skill impact-evaluator --split test
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G8-1 | 系列歸組、拆出、解散與前後比較可重現 | test_series_grouping、tests/unit/series |
| G8-2 | S11–S14 評測達門檻且引用全部可驗證 | run_eval |
| G8-3 | 新版公開邊界安全測試通過 | tests/security |
| G8-4 | 使用者確認新公開端點、閘道白名單與威脅模型 | 人工 |
| G8-5 | 播報員語氣改版後 S01–S14 評測不低於門檻 | run_eval --all |

---

## 驗收紀錄

每個任務結案時用 `tracker.py record <ID>` 產生驗收紀錄，每個階段結束再用 `tracker.py record P?` 產生閘門紀錄。模板在 `.claude/skills/ncu-resilience-tracker/templates/acceptance-record.md`，檔名為 `docs/progress/records/{ID}-{YYYYMMDD}.md`。
