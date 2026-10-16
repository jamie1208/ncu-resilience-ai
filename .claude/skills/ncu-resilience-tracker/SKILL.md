---
name: ncu-resilience-tracker
description: 追蹤與審視 NCU Resilience AI 專案進度。當使用者要求審視進度、檢查目前階段、決定下一個任務、驗證閘門、盤點產品 AI Skills、更新任務狀態或產生驗收紀錄時使用；在開始或結束任何 P0–P5 任務前也應使用。
metadata:
  version: "1.0.0"
  project: NCU Resilience AI
---

# NCU Resilience AI 進度追蹤

這個 skill 讓你在專案任何時間點重新審視進度：判斷目前階段、比對 repo 與紀錄是否一致、執行閘門測試、盤點產品 Skills（S01–S10）成熟度，並給出下一步。

所有判斷以**實際證據**為準（檔案、測試結果、評測報告），不以 state.yaml 的標記或對話記憶為準。

## 檔案

- `scripts/tracker.py`：所有操作的入口（需要 PyYAML）。
- `references/phases.yaml`：59 個任務、26 道閘門的定義（ID、前置、應存在的檔案、閘門指令）。repo 若有 `docs/progress/phases.yaml` 則優先使用它。
- `references/review-checklist.md`：審視報告「風險與偏離規格」的判斷清單。
- `templates/acceptance-record.md`：驗收紀錄模板。
- repo 內狀態：`docs/progress/state.yaml`（權威狀態）、`records/`（驗收紀錄）、`reviews/`（審視報告）。

以下指令中 `T=<本 skill 目錄>/scripts/tracker.py`，在 repo 根目錄執行。

## 情境一：審視進度（使用者說「審視」「檢查進度」「我現在在哪」）

1. 若 `docs/progress/state.yaml` 不存在，執行 `python $T init` 並告知使用者。
2. 執行 `python $T review`。若使用者要求驗證，或目前階段的任務已全部 done，改用 `python $T review --run-gates`（會執行測試，可能較久）。
3. 讀 `references/review-checklist.md`，逐項檢查並收集證據；需要時讀相關檔案、`git log`、評測報告。
4. 編輯剛產生的 `docs/progress/reviews/review-*.md`，填寫「風險與偏離規格」與「下一步」兩段。
5. 回覆使用者：目前階段與完成度、最重要的 1–3 個問題、建議的下一個任務與理由、需要他決定的事項。不要貼整份報告。

## 情境二：開始一個任務

1. `python $T status`，確認任務在「下一個可做的任務」中；若前置未完成，說明原因並建議替代任務。
2. 若任務標記 `human: true`，先列出需要使用者決定的事項，取得決定後才開始。
3. `python $T set <ID> in_progress`。
4. 依執行計畫文件中該任務的檔案清單與驗證方式實作；先寫測試再實作。

## 情境三：結束一個任務

1. 執行該任務的驗證指令，全部通過才能繼續；不可修改測試來遷就實作。
2. `python $T record <ID>` 產生驗收紀錄草稿，補齊所有段落（測試結果寫實際指令與輸出摘要）。
3. `python $T set <ID> done --evidence docs/progress/records/<檔名>`。若被拒絕（前置未完成或缺檔案），修正問題；只有使用者明確同意才可加 `--force`。
4. 若該階段任務已全部完成，執行 `python $T check --run-gates`，再對人工閘門逐一向使用者確認，用 `python $T set G?-? pass` 記錄，最後 `python $T record P?` 產生階段閘門紀錄。

## 情境四：盤點產品 AI Skills

執行 `python $T skills`。對每個「開發中」的 Skill，依問題欄給出具體修正步驟。Skill 完成定義：frontmatter 正確、有 `metadata.version`、output schema 存在、golden cases ≥ 20 且含 `"adversarial"` 標記、有評測報告且版本一致、達到執行計畫中的門檻。

## 規則

- **不可**自行把 `human: true` 的任務或人工閘門標為完成；必須有使用者本人的決定。
- **不可**為了讓閘門通過而刪除測試、加 skip、或調低門檻；遇到這種壓力時停下來告訴使用者。
- 狀態與檔案不一致時，以檔案與測試為準，並在回覆中指出差異，不要默默修改 state.yaml。
- 計畫需要變更（新增任務、改門檻）時：把 `references/phases.yaml` 複製到 `docs/progress/phases.yaml` 後修改，執行 `python $T sync`，並在 `docs/decisions/` 寫一份 ADR 說明原因。
- 回覆使用者時說重點與證據，用繁體中文。

## 指令速查

```bash
python $T init                       # 建立追蹤檔
python $T status [--json]            # 目前階段與下一步
python $T check [--phase P2] [--run-gates]
python $T skills                     # 產品 Skills 成熟度
python $T review [--run-gates]       # 完整審視並寫入報告
python $T set P1-T03 in_progress|done|blocked|skipped [--note ...] [--evidence ...]
python $T set G1-2 pass|fail
python $T record P1-T03 | P1         # 任務或階段驗收紀錄
python $T sync                       # 計畫變更後同步
```
