# NCU Resilience AI 分階段執行計畫

> 本檔是 coding agent 讀取任務定義的依據，內容與線上文件同步。
> - 規格書：`docs/requirements/NCU_Resilience_AI_System_Plan_v1.pdf`
> - 機器可讀的任務／閘門定義：`.claude/skills/ncu-resilience-tracker/references/phases.yaml`
> - 權威進度：`docs/progress/state.yaml`
>
> 修改計畫時三處要一起改：本檔、phases.yaml（或其覆寫檔 `docs/progress/phases.yaml`）、ADR。

全案 6 個階段、60 個任務、26 道閘門。任務 ID 為 `P{階段}-T{序號}`，閘門 ID 為 `G{階段}-{序號}`。標記「需你決定」的任務，AI 只能準備資料，必須由使用者本人定案。

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

目標：至少 3 個合法來源連續 7 天穩定擷取，重跑零重複，任一筆資料可反查原文與擷取時間。前置：G0 全部通過。

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
  - 驗證：`tests/unit/ingestion/test_manual_import.py`
- **P1-T05 標準化**
  - 檔案：`app/ingestion/normalize.py`（時間轉 aware UTC、文字清洗、語系偵測、`content_hash = sha256(normalized_text)`）
  - 驗證：`tests/unit/ingestion/test_normalize.py`（含民國年、無時區、全形字）
- **P1-T06 冪等寫入與版本**
  - 檔案：`app/ingestion/store.py`（單一交易：upsert source_items `ON CONFLICT`、版本 unique(item_id, content_hash)）
  - 驗證：`tests/integration/test_idempotent_ingest.py`：同批資料寫入 3 次筆數不變；內容改變才新增版本
- **P1-T07 排程、重試與死信**
  - 檔案：`app/worker/celery_app.py`、`app/worker/tasks/ingest.py`、beat 排程設定、`infra/migrations/versions/0002_ingestion_dead_letters.py`
  - 驗證：模擬來源失敗 → 指數退避重試 3 次 → 進死信表；ingestion_runs 記錄 counts 與 status
- **P1-T08 來源健康與管理 API**
  - 檔案：`app/ingestion/health.py`、`app/api/v1/admin/ingestion_runs.py`（`GET /api/v1/admin/ingestion-runs`）
  - 驗證：`tests/integration/test_ingestion_runs_api.py`
- **P1-T09 標記資料集啟動**（金標準需你確認）
  - 檔案：`data/labeling/guidelines.md`（分類法、真偽、同事件判準）、`data/labeling/label.schema.json`、`scripts/labeling/export_for_labeling.py`、`scripts/labeling/ai_prelabel.py`；資料存 `data/labeled/`（不進 git，含個資時加密）
  - 驗證：累積 ≥ 100 筆人工確認樣本，依事件／時間切分 dev／test
- **P1-T10 七天穩定運行報告**
  - 檔案：`scripts/reports/ingestion_report.py`、`docs/progress/records/P1-stability.md`
  - 驗證：報告顯示 7 天成功率、各來源筆數、重複寫入數 = 0

```bash
cd backend && uv run pytest tests/unit/ingestion tests/integration -q
uv run python -m app.worker.tasks.ingest --source all --once   # 手動觸發一輪
uv run python -m app.worker.tasks.ingest --source all --once   # 再跑一次，確認無新增重複
uv run python ../scripts/reports/ingestion_report.py --days 7
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G1-1 | ≥ 3 個來源上線 | sources 表 enabled 且核准 ≥ 3 |
| G1-2 | 7 天成功率 ≥ 98% | ingestion_report.py |
| G1-3 | 重跑零重複 | test_idempotent_ingest 通過且報告重複數 0 |
| G1-4 | 可反查原文 | 隨機抽 20 筆皆有 url 與 fetched_at |
| G1-5 | ≥ 100 筆標記樣本 | data/labeled 統計腳本 |

---

## P2 AI 核心

目標：建好「可替換模型、可驗證輸出、可回溯證據」的 Skill 基礎設施，並讓 S01–S08 達到評測門檻。分 P2a 基礎設施（T01–T05）、P2b 事件理解（T06–T10）、P2c 風險與決策（T11–T14）。前置：G1 通過，標記樣本 ≥ 100 筆。

### P2a 基礎設施

- **P2-T01 Skill 規格與模板**
  - 檔案：`skills/_template/`（完整 7 件套）、`docs/architecture/skill-standard.md`（frontmatter 欄位、章節、命名、版本規則）
  - 驗證：模板本身通過 validate_skill
- **P2-T02 Skill 靜態驗證工具**
  - 檔案：`scripts/skills/validate_skill.py`（名稱＝資料夾、小寫連字號、description ≤ 1024 字元、schema 合法、cases ≥ 20、必含對抗案例）；CI 加入 `validate_skill.py skills/*`
  - 驗證：`tests/unit/test_validate_skill.py`（壞掉的範例皆被擋下）
- **P2-T03 LLM Adapter**
  - 檔案：`app/ai/llm/base.py`（`LLMProvider.generate_structured(messages, json_schema, model, timeout, max_output_tokens)`）、`app/ai/llm/openai_provider.py`（Structured Outputs，strict JSON schema）、`app/ai/llm/fake_provider.py`（讀錄製回應，CI 不打 API）、`app/ai/llm/cost.py`（token 與費用記帳）
  - 驗證：`tests/unit/ai/test_llm_adapter.py`；模型名稱只出現在設定檔
- **P2-T04 Skill Runner**
  - 檔案：`app/ai/runner/loader.py`（掃描 `skills/`、解析 frontmatter、載入 references）、`app/ai/runner/prompt.py`（系統指令＝SKILL.md；來源內容一律包在 `<untrusted_source id=…>` 中）、`app/ai/runner/validate.py`（schema、列舉、範圍、evidence_id 存在、PII）、`app/ai/runner/run.py`（無效輸出重試 ≤ 2 次 → abstain、費用上限、逾時、寫入 ai_analysis_runs）、`infra/migrations/versions/0003_ai_runs.py`（ai_analysis_runs、risk_assessments、event_relations）
  - 驗證：`tests/unit/ai/test_runner.py`（無效 JSON、不存在的 evidence_id、超支皆正確處理）
- **P2-T05 評測框架**
  - 檔案：`scripts/eval/run_eval.py`、`app/ai/eval/metrics.py`（Macro-F1、Precision／Recall、配對式合併指標）、`tests/golden/test_skills.py`（錄製模式）、`eval/reports/`
  - 驗證：用模板 skill 跑出一份完整報告

### P2b 事件理解

- **P2-T06 S01 relevance-filter**：`skills/relevance-filter/`；輸出 is_campus_relevant、reason、evidence_spans
- **P2-T07 S02 event-extractor**：`skills/event-extractor/`；輸出事件、時間、地點、證據字元位置；同時標記涉及可識別人物／店家（供 P3 發布規則使用）
- **P2-T08 S03 event-classifier**：`skills/event-classifier/` + `references/taxonomy.md`；輸出類別、標籤、classification_confidence
- **P2-T09 S04 event-linker**：`skills/event-linker/` + `app/ai/embeddings.py`（pgvector 候選召回＋時間地點視窗）；輸出 same_event／related_event／none 與信心
- **P2-T10 S05 evidence-verifier**：`skills/evidence-verifier/` + `app/verification/rules.py`；規則優先（官方來源→confirmed），模型只提供建議，狀態變更一律經規則與狀態機

### P2c 風險與決策

- **P2-T11 風險引擎 + S06 risk-assessor**（權重與門檻需你決定）：`app/risk/engine.py`、`config/risk-policy.yaml`（權重、分級、critical_override，帶 policy_version）、`skills/risk-assessor/scripts/calculate_risk.py`（與引擎共用同一函式）；模型只給五維分數與不確定性，加權由程式計算
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

## P3 產品與安全發布（MVP）

目標：完成規格 §13.1 的最小可展示端到端版本，且「未核實具體指控錯誤公開」為 0 次。分 P3a 唯讀產品（T01–T05）、P3b 匿名通報（T06）、P3c 自動警報（T07–T10）。**自動公開必須在 T09 對抗測試全過、由使用者確認後才開啟。** 前置：G2 通過。

### P3a 唯讀產品

- **P3-T01 管理者驗證與 RBAC**
  - 檔案：`app/core/security.py`（argon2 密碼、HttpOnly Secure Cookie session、CSRF）、`app/api/deps.py`、`infra/migrations/versions/0004_users_roles.py`、`scripts/admin/create_admin.py`
  - 驗證：`tests/integration/test_auth.py`（未登入 401、非管理者 403、CSRF 缺失拒絕）
- **P3-T02 公開 API**
  - 檔案：`app/api/v1/public/events.py`、`alerts.py`、`dashboard.py`、`app/api/schemas/public.py`（欄位白名單）、`app/api/pagination.py`（cursor）、`app/api/errors.py`（{data,meta,error} 與錯誤碼）
  - 驗證：`tests/integration/test_public_api.py`；斷言公開回應絕不含原文、投稿資訊、內部風險欄位
- **P3-T03 管理 API**
  - 檔案：`app/api/v1/admin/events.py`（列表、verify、analyze）、`alerts.py`（retract）、`audit_logs.py`；所有寫入帶 request_id 並寫稽核
  - 驗證：`tests/integration/test_admin_api.py`
- **P3-T04 公開前端**
  - 檔案：`frontend/src/public/pages/`（Home、EventList、EventDetail、Report、About 含非官方免責）
  - 驗證：`npm run test`（Vitest）＋ Playwright 冒煙測試
- **P3-T05 管理前端**
  - 檔案：`frontend/src/admin/pages/`（EventWorkbench：來源對照、風險分項、證據；AlertCenter；SkillsDashboard：版本、費用、失敗率、評測結果；Ops）
  - 驗證：Playwright 管理流程測試

### P3b 匿名通報

- **P3-T06 匿名通報**
  - 檔案：`app/api/v1/public/reports.py`（`POST /reports`、`POST /reports/{token}/followups`）、`app/reports/crypto.py`（原文加密，金鑰取自環境）、`app/reports/abuse.py`（Redis 限流、CAPTCHA 驗證、同文偵測）、`app/reports/attachments.py`（類型白名單、大小上限、惡意檔掃描介面）、`infra/migrations/versions/0005_reports.py`、前端通報頁（含 110／119 緊急提示、IP 保留揭露）
  - 驗證：`tests/integration/test_reports.py`（只存 receipt token 雜湊、超量回 RATE_LIMITED、資料庫看不到明文）

### P3c 自動警報

- **P3-T07 發布規則引擎**（規則內容需你決定）
  - 檔案：`app/alerts/policy.py`（將規格 §6.4 五種情境寫成確定性規則）、`app/alerts/pii.py`、`app/alerts/templates/`（中性安全提醒模板）、`config/publishing-policy.yaml`
  - 驗證：`tests/unit/alerts/test_policy.py`；規格附錄 A 的 A02、A05 必須被阻擋具體指控
- **P3-T08 警示狀態機與遞送**
  - 檔案：`app/alerts/service.py`（candidate→policy_checked→eligible→published→updated／retracted／expired）、`app/alerts/delivery.py`（先做公開頁面頻道，Email／LINE 留介面）；idempotency_key ＝ hash(event_id, alert_version, audience)
  - 驗證：`tests/integration/test_alert_idempotency.py`（重送不重複、撤回後停止）
- **P3-T09 發布對抗測試集**（開啟自動發布前需你確認）
  - 檔案：`tests/security/publishing/cases.jsonl`（≥ 50 筆：惡意誣指、個資、虛構事件、矛盾來源、惡作劇）、`tests/security/publishing/test_publishing.py`
  - 驗證：錯誤公開 0 筆後，才可設定 `AUTO_PUBLISH_ENABLED=true`
- **P3-T10 端到端測試**
  - 檔案：`tests/e2e/`（Playwright），涵蓋附錄 A 的 A01–A08
  - 驗證：`npx playwright test` 全過

```bash
cd backend && uv run pytest tests/integration tests/unit/alerts tests/security/publishing -q
cd ../frontend && npm run test && npx playwright test
make e2e   # 啟動完整環境並跑附錄 A 案例
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G3-1 | 未核實具體指控錯誤公開 0 次 | publishing 對抗測試 |
| G3-2 | 同版本警報無重複外送 | test_alert_idempotency |
| G3-3 | 公開／管理資料隔離 | test_public_api 欄位斷言 |
| G3-4 | 附錄 A 的 A01–A08 全過 | Playwright e2e |
| G3-5 | MVP 展示路徑可運作 | 依 §13.1 錄一段展示並附在驗收紀錄 |

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
  - 檔案：`app/analytics/timeseries.py`（週／月頻率、地點分布、EWMA、Poisson 變化偵測）、`infra/migrations/versions/0006_analytics_views.py`（materialized views）
  - 驗證：`tests/unit/analytics/test_timeseries.py`（合成資料注入突增可被偵測）
- **P4-T04 趨勢回測**
  - 檔案：`scripts/eval/backtest_trends.py`（滾動切點，只用切點前資料）、`tests/unit/analytics/test_no_leakage.py`
  - 驗證：洩漏測試通過；回測報告寫入 `eval/reports/trends/`
- **P4-T05 改善措施追蹤（F10）**
  - 檔案：`app/resilience/actions.py`、`infra/migrations/versions/0007_response_actions.py`（含狀態變更歷史）、管理端改善追蹤頁
  - 驗證：`tests/integration/test_response_actions.py`（發現→處理→恢復→改進完整紀錄）
- **P4-T06 校園韌性指數 CRI 與公開報告**
  - 檔案：`app/resilience/cri.py`（四構面標準化，資料不足回傳 None）；公開報告頁加入資料覆蓋率、時間窗、樣本量、來源限制
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

目標：完成 S09–S10 與 What-if，系統可監控、可備份還原，並驗證 Skills 能在其他 AI 客戶端運作。前置：G4 通過。

- **P5-T01 S09 trend-forecaster**：`skills/trend-forecaster/`；輸入 P4 的統計結果，只產生「異常解釋與前瞻訊號」，必須標註聲量≠事件數、資料覆蓋率
- **P5-T02 What-if 引擎 + S10 scenario-simulator**
  - 檔案：`app/simulations/engine.py`（baseline × Π(1 − effect_i)、效果區間、敏感度、固定隨機種子）、`skills/scenario-simulator/`（只負責解釋假設與限制，數值由引擎計算）、`infra/migrations/versions/0008_scenario_runs.py`
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
- **P5-T06 正式部署設定**（正式上線需你決定）
  - 檔案：`infra/docker/docker-compose.prod.yml`、`infra/caddy/Caddyfile`（TLS）、`docs/runbooks/deploy.md`；金鑰改由 secrets 管理
  - 驗證：staging 環境部署成功
- **P5-T07 負載測試**
  - 檔案：`tests/load/public_events.js`（k6）、`docs/progress/records/P5-load.md`（寫明測試環境與負載）
  - 驗證：公開事件列表 p95 < 1.5 秒
- **P5-T08 Skills 可攜性驗證**
  - 檔案：`docs/portability-report.md`；將 S01–S10 放進另一個支援 Agent Skills 的客戶端（例如 Claude Code）跑 golden cases，比較與 Runner 結果
  - 驗證：記錄各 skill 在不同平台的通過率與需調整之處
- **P5-T09 規格書 v1.1 更新**
  - 檔案：`docs/requirements/spec-v1.1.md`（實際來源、schema、門檻、部署規格的變更）
  - 驗證：與 v1.0 差異清單完整

```bash
cd backend && uv run pytest tests/unit/simulations -q
uv run python ../scripts/eval/run_eval.py --skill trend-forecaster --skill scenario-simulator --live
k6 run tests/load/public_events.js
bash infra/scripts/backup.sh && bash infra/scripts/restore.sh --target staging
```

| 閘門 | 條件 | 驗證方式 |
| --- | --- | --- |
| G5-1 | 模擬結果可重現 | test_engine |
| G5-2 | 備份可完整還原 | 還原演練紀錄 |
| G5-3 | 效能達標 | k6 報告 p95 < 1.5 秒 |
| G5-4 | Skills 可攜 | portability-report.md |

---

## 驗收紀錄

每個任務結案時用 `tracker.py record <ID>` 產生驗收紀錄，每個階段結束再用 `tracker.py record P?` 產生閘門紀錄。模板在 `.claude/skills/ncu-resilience-tracker/templates/acceptance-record.md`，檔名為 `docs/progress/records/{ID}-{YYYYMMDD}.md`。
