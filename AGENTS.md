# AGENTS.md — AI 開發規範

本檔是所有 AI coding agent（Claude Code 等）在本 repo 工作時必須遵守的規範。與任何單次指令衝突時，先停下來向使用者說明衝突，不要自行取捨。

## 專案

NCU Resilience AI：國立中央大學 AI 校園韌性與智慧事件決策系統。研究專題，非校方官方系統。

- 規格書：`docs/requirements/NCU_Resilience_AI_System_Plan_v1.pdf`（需求的最終依據）
- 執行計畫：`docs/plan/execution-plan.md`（59 個任務的檔案清單、驗證方式與閘門）
- 目標架構：`docs/architecture/project-structure.md`
- 進度：`docs/progress/state.yaml`，透過 `ncu-resilience-tracker` skill 操作

## 技術棧（變更需寫 ADR）

| 層 | 選擇 |
| --- | --- |
| 後端 | Python 3.12、uv、FastAPI、Pydantic v2、SQLAlchemy 2（async）、Alembic |
| 工作佇列 | Celery + Redis |
| 資料庫 | PostgreSQL 16 + pgvector |
| AI | OpenAI API（經 `app/ai/llm/` Adapter）、自建 Skill Runner、Agent Skills 格式 |
| 前端 | React + TypeScript + Vite、Vitest、Playwright |
| 品質 | ruff（lint + format）、mypy、pytest、ESLint |
| 環境 | Docker Compose、GitHub Actions |

## 工作流程（每個任務）

1. 用 `ncu-resilience-tracker` 確認任務的前置都是 done；不是就停下來回報。
2. 讀 `docs/plan/execution-plan.md` 中該任務的定義；只建立／修改列出的檔案，需要額外檔案先說明理由。
3. 標記 `in_progress`，在分支 `feat/<任務ID>-<簡短描述>` 上工作。
4. **先寫測試，再寫實作**；執行該任務的全部驗證指令直到通過。
5. 用 tracker 產生驗收紀錄並補齊，回報使用者，等使用者確認後才標記 done。

一次只做一個任務。發現計畫有誤或缺漏時，提出修改建議，不要自行擴大範圍。

## 不可違反的規則

1. **先寫測試**：新功能必須先有失敗的測試。
2. **不改測試遷就實作**：不得刪除、跳過（skip／xfail）或放寬既有測試與評測門檻；若測試本身有誤，說明理由並請使用者同意。
3. **不寫入金鑰**：金鑰、密碼、token 只從環境變數讀取；`.env` 永不提交；範例值放 `.env.example`。
4. **產品 skills 與開發 skills 分離**：系統執行的產品 Skills（S01–S10）只放在 `skills/`；協助開發的 skills 只放在 `.claude/skills/`。Skill Runner 只能載入 `skills/`。
5. **需人工決定的事不代做**：計畫中標記「需你決定」的任務與人工閘門，只能準備資料與選項。
6. **不可逆操作先問**：刪除資料、改寫 git 歷史、正式部署、開啟 `AUTO_PUBLISH_ENABLED` 前必須取得使用者明確同意。

## 程式規範

- 後端程式在 `backend/app/`，測試在 `backend/tests/{unit,integration,golden,security}`；跨前後端的 e2e 與負載測試在根目錄 `tests/`。
- 型別標註完整，mypy 不可新增忽略；公開函式寫 docstring。
- 所有時間以 aware UTC 儲存，顯示時轉 Asia/Taipei。
- API 回應統一 `{data, meta, error}`；錯誤碼使用規格 §10.1 定義。
- 資料庫變更只能透過 Alembic migration，且必須可 downgrade。
- 寫入操作產生 request_id 並寫入 audit_logs。
- 日誌不得包含個資、投稿原文或金鑰。

## Git 規範

- 分支：`feat/<任務ID>-<簡短描述>`；修正用 `fix/<任務ID>-<簡短描述>`。
- Commit 訊息：`feat(<任務ID>): <摘要>`，一個任務的變更集中在該任務分支。
- 不 push、不開 PR、不 force push，除非使用者要求。
- commit 前確認 `git status` 中沒有 `.env`、金鑰檔或 `data/raw/`、`data/labeled/` 的內容。

## AI 與產品 Skills 規範

- 模型名稱、溫度、費用上限只能出現在設定檔，業務邏輯不得硬編碼。
- 來源文章、匿名投稿等外部內容一律視為不可信，以 `<untrusted_source>` 包裹，永遠不能改變系統指令。
- 模型輸出必須通過 JSON Schema 與業務驗證（列舉、範圍、evidence_id 存在、PII）才能被使用；證據不足時輸出 abstain，不得強迫結論。
- 數值計算（風險加權、What-if）由程式完成，模型只提供分項判斷與解釋。
- 每個產品 Skill 依執行計畫的「Skill 開發標準循環」開發；SKILL.md 改動後必須重跑評測並升版本。
- CI 使用 fake provider 與錄製回應，不呼叫真實 API。
- 自動發布只能經 `app/alerts/policy.py` 的確定性規則；未核實的具體指控永遠不得公開。

## 常用指令

```bash
T=.claude/skills/ncu-resilience-tracker/scripts/tracker.py
python $T status                      # 目前階段與下一步
python $T review                      # 完整審視
make up / make down / make test       # P0-T04 完成後可用
cd backend && uv run pytest -q
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app
cd frontend && npm run lint && npm run test && npm run build
```

## 回報格式

完成任務時回報：變更檔案、測試指令與結果、已知限制、需要使用者決定的事項。用繁體中文。
