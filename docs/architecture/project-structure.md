# 專案目標架構

這是全部 62 個任務完成後的 repo 結構。目前骨架只建立了頂層資料夾與規範文件，其餘檔案由各任務建立；註解中的任務 ID 標示誰負責建立。

## 系統分層

| 層級 | 元件 | 位置 |
| --- | --- | --- |
| 呈現層 | 個人管理介面（唯一前端，ADR-0008） | `frontend/src/admin` |
| API 層 | FastAPI、RBAC、限流 | `backend/app/api` |
| 收集層 | Source Adapters、排程 | `backend/app/ingestion`、`backend/app/worker` |
| 資料層 | PostgreSQL + pgvector、物件儲存 | `backend/app/models`、`infra/migrations` |
| AI 層 | LLM Adapter、Skill Runner、產品 Skills | `backend/app/ai`、`skills/` |
| 決策層 | 查證規則、風險引擎、發布規則 | `backend/app/verification`、`app/risk`、`app/alerts` |
| 維運層 | Docker、監控、備份、CI | `infra/`、`.github/` |

資料流：來源擷取 → 原文版本保存 → 標準化 → S01 相關性 → S02 抽取 → S03 分類 → S04 合併／關聯 → S05 查證 → S06 風險 → 通知規則 → 個人通知 → 改善追蹤。（ADR-0008：不對外發布）

## 目錄樹

```text
ncu-resilience-ai/
├── AGENTS.md                      # AI 開發規範（P0-T01，骨架已建）
├── CLAUDE.md                      # Claude Code 入口，匯入 AGENTS.md
├── README.md
├── Makefile                       # P0-T04
├── .env.example                   # P0-T04
├── .github/workflows/ci.yml       # P0-T08
│
├── .claude/                       # 開發輔助與資料擷取作業（不會被 Skill Runner 載入）
│   ├── commands/                  # /task /finish /review
│   ├── skills/ncu-resilience-tracker/
│   ├── skills/ncu-fb-capture/     # P1-T13 Facebook 社團每日擷取
│   └── settings.json
│
├── backend/
│   ├── pyproject.toml             # P0-T02（uv）
│   ├── alembic.ini                # P0-T05
│   ├── Dockerfile                 # P0-T04
│   ├── app/
│   │   ├── main.py                # P0-T02
│   │   ├── core/                  # config, logging, security(P0-T07/P3-T01), metrics(P5-T04)
│   │   ├── db/session.py          # P0-T05
│   │   ├── models/                # P0-T05 起，各階段增表
│   │   ├── domain/states.py       # P0-T06 狀態機
│   │   ├── services/audit.py      # P0-T07
│   │   ├── api/
│   │   │   ├── deps.py, errors.py, pagination.py      # P3-T01/T03
│   │   │   └── v1/
│   │   │       ├── health.py                          # P0-T02
│   │   │       └── admin/   events, alerts, dashboard, audit_logs, ingestion_runs, relations
│   │   ├── ingestion/             # P1：base, registry, http, source_settings, normalize, store, health, adapters/
│   │   ├── worker/                # P1-T07：celery_app, tasks/
│   │   ├── ai/
│   │   │   ├── llm/               # P2-T03：base, openai_provider, fake_provider, cost
│   │   │   ├── runner/            # P2-T04：loader, prompt, validate, run
│   │   │   ├── eval/metrics.py    # P2-T05
│   │   │   └── embeddings.py      # P2-T09
│   │   ├── verification/rules.py  # P2-T10
│   │   ├── risk/engine.py         # P2-T11
│   │   ├── pipeline/analyze_item.py   # P2-T13
│   │   ├── alerts/                # P3-T07/T08：policy, pii, service, delivery, templates/
│   │   ├── relations/             # P4-T01
│   │   ├── analytics/             # P4-T03
│   │   ├── resilience/            # P4-T05/T06：actions, cri
│   │   └── simulations/engine.py  # P5-T02
│   └── tests/
│       ├── unit/  integration/  golden/  security/  fixtures/
│
├── frontend/                      # P0-T03（Vite react-ts）
│   └── src/
│       ├── api/client.ts
│       ├── components/
│       └── admin/pages/           # P3-T05、P4-T02、P5-T03
│
├── skills/                        # 產品 AI Skills（Agent Skills 格式）
│   ├── _template/                 # P2-T01
│   ├── relevance-filter/          # S01  P2-T06
│   ├── event-extractor/           # S02  P2-T07
│   ├── event-classifier/          # S03  P2-T08
│   ├── event-linker/              # S04  P2-T09
│   ├── evidence-verifier/         # S05  P2-T10
│   ├── risk-assessor/             # S06  P2-T11
│   ├── root-cause-analyst/        # S07  P2-T12
│   ├── solution-planner/          # S08  P2-T12
│   ├── trend-forecaster/          # S09  P5-T01
│   └── scenario-simulator/        # S10  P5-T02
│
├── config/                        # sources.yaml(P1-T03)、risk-policy.yaml(P2-T11)、notification-policy.yaml(P3-T07)
├── scripts/                       # skills/validate_skill.py、eval/、labeling/、reports/、admin/、facebook/（P1-T13）
├── eval/reports/                  # 評測報告（依 skill/版本）
├── data/                          # labeling/ 規範進 git；labeled/、raw/（含 raw/facebook/，P1-T13）不進 git
├── tests/                         # e2e/（Playwright）
│
├── infra/
│   ├── docker/                    # docker-compose.yml、docker-compose.local.yml（P5-T06）
│   ├── migrations/versions/       # 0001_core … 0008_scenario_runs
│   ├── monitoring/                # P5-T04
│   ├── launchd/                   # P1-T13 Facebook 擷取排程範本
│   └── scripts/                   # backup.sh、restore.sh（P5-T05）
│
└── docs/
    ├── requirements/              # 規格書 v1.0 PDF、v1.1（P5-T09）
    ├── plan/execution-plan.md     # 62 個任務的定義
    ├── architecture/              # 本檔、state-machines、rbac、skill-standard
    ├── decisions/                 # ADR
    ├── governance/                # source-inventory、manual-import-format
    ├── runbooks/                  # deploy、restore
    └── progress/                  # state.yaml、records/、reviews/
```

## 兩種 Skills 的位置

| | Claude Code Skills | 產品 Skills |
| --- | --- | --- |
| 位置 | `.claude/skills/` | `skills/` |
| 誰執行 | Claude Code（協助你開發、執行資料擷取作業） | 後端 Skill Runner（系統執行時呼叫 OpenAI API） |
| 範例 | ncu-resilience-tracker、ncu-fb-capture | S01–S10 |
