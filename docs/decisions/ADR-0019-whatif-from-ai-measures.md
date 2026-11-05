# ADR-0019：What-if 改為使用者選擇改善建議、AI 評估效益

- 狀態：提議
- 日期：2026-11-05
- 決定者：使用者本人（由使用者選擇要採用的改善建議、AI 依現況與選擇評估改善百分比、呈現改善前後情境比較、文案有趣、只在後台）；實作細節由 AI 決定並記錄於此
- 部分取代：AGENTS.md「數值計算（風險加權、What-if）由程式完成，模型只提供分項判斷與解釋」對 What-if 的部分（見決策 2）；規格 v1.1 §8.3「介面不選關聯事件、基準件數手動填寫」
- 不變：ADR-0015 管理端不對外；P7-T02 引擎、S10 與 G7-1 測試；「假設情境，非成效預測」標示；S08 的輸出 schema

## 背景

P7-T02／P7-T03 的 What-if 要使用者自己輸入措施名稱、效果區間與成本，跟事件小報告的 AI 改善建議（S08）沒有連結，使用者要輸入的數字太多。使用者要的流程是：AI 給出改善建議 → 使用者決定採用哪些 → AI 依事件現況與使用者的選擇評估效益。

## 決策

### 1. 流程（使用者決定）

1. 事件詳細頁的改善建議區塊加「評估效益」，進入 `/admin/scenarios?event=<id>`，列出該事件最新一份有效小報告的 S08 措施。
2. 使用者只勾選要採用的措施，不輸入任何數字。
3. 按下評估後，新的產品 Skill **S14 impact-evaluator** 依「現況」與「選擇」產生評估；同一組選擇的結果保存，可回看、可比較不同選擇。
4. 只在 `/admin/*` 與 `/api/v1/admin/*`；評估結果不進入公開快照。
5. 原本 P7-T03 的自訂措施模式移到同頁的另一個分頁，G7-1 測試不修改。

### 2. S14 的輸入與輸出（使用者決定 AI 給改善百分比，AGENTS.md 例外）

1. **輸入（程式組裝）**：事件摘要與類別、小報告的原因（含 basis）、全部 S08 措施並標示哪些被選、P8-T09 的歷史基準（時間窗內件數、代表地點）、改善追蹤的目前進度。措施文字與事件內容以 `<untrusted_source>` 包裹。
2. **輸出**：
   - `improvement`：預估改善百分比區間 `{low, high}`（整數 0–100），或 `null`（證據不足，abstain）。
   - `before`：改善前情境，一段話描述現況。
   - `after`：改善後情境，一段話描述採用這些策略後可能的樣子。
   - `covered_causes`／`uncovered_causes`：選擇涵蓋與沒有涵蓋的原因 id。
   - `overlaps`：效果可能重疊的措施組。
   - `risks`：限制與副作用（至少 1 項）。
   - `suggestion`：可考慮補上的措施（只能引用未被選擇的 S08 措施）。
   - `evidence_ids`：引用的證據，必須存在於輸入。
3. **業務驗證（程式）**：`0 ≤ low ≤ high ≤ improvement_cap`（設定檔，預設 80）；只選了查證類措施時 `improvement` 必須為 `null`；`covered_causes`、`suggestion` 引用的 id 必須存在；`before`／`after` 經 PII 檢查；推測原因（hypothesized）在文中不得寫成已確定。驗證失敗依 Skill Runner 規則重試，仍失敗則顯示「這次評估不出來」。
4. **例外範圍**：模型只產生改善百分比區間與文字；改善前後的件數換算（基準 ×（1 − 百分比））若要顯示，一律由程式計算。
5. 文案語氣依 ADR-0018 的播報員語氣與 `docs/governance/tone-guide.md`（P8-T10）：有趣、不用表情符號；「AI 估計，非實證」與「假設情境，非成效預測」標籤維持平實。

### 3. 介面

1. 頁首：「假設情境，非成效預測」。
2. 結果卡：大字的改善百分比區間（標示「AI 估計，非實證」）、改善前／改善後兩欄情境對照（含程式換算的件數，基準不足時不顯示件數）、涵蓋與未涵蓋的原因、重疊提醒、風險、補強建議（可一鍵加入勾選再評估）。
3. 雙設計系統各實作一次（ADR-0014）。

### 4. 計畫變更

1. 新增 **P8-T13「依改善建議評估效益（S14）」**，前置 P8-T09（歷史基準）、P8-T10（語氣指南）；排在 P8-T11 之後、P8-T12（規格書 v1.2）之前。
2. 檔案（暫定，寫入 `docs/plan/execution-plan.md` 後才開始）：`skills/impact-evaluator/`（依「Skill 開發標準循環」：SKILL.md、schema、golden cases、錄製回應、評測報告）、`app/simulations/impact.py`（輸入組裝、驗證、保存）、`infra/migrations/versions/<下一個編號>_impact_evaluations.py`（或擴充 `scenario_runs`，實作時決定並記錄）、`app/api/v1/admin/scenarios.py`（新增 `POST /api/v1/admin/scenarios/impact`，`scenarios:create`，稽核 `scenario.impact`；`GET` 查詢）、`frontend/src/admin/pages/Scenario.tsx` 與事件詳細頁入口、設定檔 `improvement_cap`。
3. 驗證（暫定）：S14 評測指標 `improvement_valid`（= 1.0）、`cause_coverage`（accuracy ≥ 0.90）、`abstain_when_insufficient`、`hypothesized_not_asserted`（= 1.0）；`tests/integration/test_impact_evaluation.py`（fake provider；只選查證類措施為 null；引用不存在的 id 被拒；件數由程式換算）；`tests/security/` 確認公開快照不含評估結果；Playwright 勾選到結果的流程；G7-1 測試不修改且通過。
4. 同步修改：AGENTS.md「AI 與產品 Skills 規範」中數值計算的規則加註本 ADR 例外、產品 Skills 範圍改為 S01–S14；`skills/README.md`；`docs/progress/phases.yaml`；規格 v1.2 §8.3。

## 考慮過的替代方案

- **AI 預估每項措施的成效，由程式組合並列出成本效益最佳的組合**：數字較可控，但畫面與概念太複雜，而且使用者希望自己決定採用哪些策略。
- **AI 給分級、程式依設定換算**：符合既有規則，但使用者希望直接看到百分比。
- **只給文字與效益等級**：最不容易被誤讀，但使用者希望看到改善百分比。
- **擴充 S10**：S10 的輸入是引擎結果，與本流程不同；改 S10 會動到 G7-1 評測，因此另建 S14。

## 影響與風險

- **AI 數字被誤讀為實證**：改善百分比沒有校準依據，可能過度樂觀。以 `improvement_cap`、「AI 估計，非實證」與「假設情境」標示、只在後台控制；評測只能檢查格式、引用與合理範圍，不能證明數字正確。
- **不可重現**：同一組選擇再評估可能得到不同百分比；保存每次結果與 ai_run_id，介面顯示評估時間。
- **有趣文案被讀成輕忽**：標籤、風險與限制維持平實語氣（tone-guide 的例外）。
- **費用**：每次評估呼叫一次 S14，受每日費用上限限制；同一事件、同一份報告、同一組選擇在報告未更新前沿用既有結果。
- **工作量**：新增一個產品 Skill（S14）與一個前端頁面的雙設計系統實作。
