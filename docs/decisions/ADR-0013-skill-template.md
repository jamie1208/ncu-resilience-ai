# ADR-0013：Skill 模板的命名、示範任務與驗證順序

- 狀態：已採納
- 日期：2026-10-19
- 決定者：使用者本人（2026-10-19 在 P2-T01 開工前的對話中選擇）

## 背景

P2-T01 開工時發現計畫有兩處矛盾，另有兩項設計需要先決定：

1. **驗證順序卡住**：P2-T01 的驗證是「模板本身通過 validate_skill」，但 `validate_skill.py` 要到 P2-T02 才建立，而 P2-T02 又依賴 P2-T01。
2. **模板名稱不合規**：計畫與 `skills/README.md` 用的是 `skills/_template/`。但 Agent Skills 規格與 P2-T02 的規則都要求名稱是小寫連字號、與資料夾同名，底線開頭不合規。
3. **模板要做什麼**：P2-T05 的驗證是「用模板 skill 跑出一份完整報告」，所以模板必須是可以實際執行的示範任務，不能只是佔位骨架。
4. **案例數**：CI 會對 `skills/` 底下每個資料夾執行 validate_skill，規則是至少 20 筆案例，而且必須有對抗案例。

## 決策

1. **驗證移到 P2-T02**：
   - P2-T01 結案時，先逐項核對 7 件套，並用 jsonschema 確認兩份 schema 合法、每筆 case 的輸入與錄製輸出都通過 schema；
   - 「模板通過 validate_skill」改列為 P2-T02 的驗證項目。
2. **保留 `skills/_template/`**：
   - frontmatter 寫 `name: template`；
   - 規則：底線開頭的資料夾是模板，名稱比對時去掉開頭底線，其他檢查照常套用；
   - Skill Runner（P2-T04）不載入底線開頭的資料夾。
3. **示範任務是「公告類型分類」**：
   - 類型有停課停班、維修停機、活動、其他；
   - 輸出 `evidence_ids`，可以 abstain；
   - 選分類任務是為了讓 P2-T05 可以算 Macro-F1。
4. **模板附 20 筆 golden cases**：
   - 一般 8 筆、邊界 6 筆、對抗 6 筆（提示注入、個資、無證據、引用不存在的證據）；
   - dev 與 test 各 10 筆；
   - 每筆都附手寫的錄製回應，所以模板不需要例外規則就能通過 validate_skill 與評測。

## 考慮過的替代方案

- **P2-T01 先寫一個只檢查模板的測試**：P2-T02 會重寫同樣的檢查，造成重複。
- **合併 P2-T01 與 P2-T02**：違反「一次只做一個任務」。
- **改名為 `skill-template`**：完全符合規格，但 Runner 需要另外排除它，計畫與 README 也要跟著改名。
- **純佔位模板**：P2-T05 要另外準備示範 skill。
- **模板只放幾筆案例並豁免**：validate_skill 需要額外的例外規則。

## 影響

- `docs/plan/execution-plan.md` 修改 P2-T01、P2-T02 的驗證；`docs/progress/phases.yaml` 的檔案清單不變。
- P2-T02 的 validate_skill 要實作「底線開頭的資料夾是模板，名稱比對時去掉開頭底線」；P2-T04 的 loader 要略過底線開頭的資料夾。
- `docs/architecture/skill-standard.md` 規定的 case、`must_not` 與錄製回應格式，是 P2-T03（fake provider）與 P2-T05（評測）要遵守的介面。
