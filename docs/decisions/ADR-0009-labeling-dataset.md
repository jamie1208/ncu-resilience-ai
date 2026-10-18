# ADR-0009：P1-T09 標記資料集的做法調整；migration 檔名改用實際編號

- 狀態：已採納
- 日期：2026-10-18

## 背景

1. P1-T09 計畫列出 `scripts/labeling/ai_prelabel.py`，但 LLM Adapter（P2-T03）與 Skill Runner（P2-T04）都還沒建立。AGENTS.md 規定模型只能經 `app/ai/llm/` 呼叫，現在無法合規地用 AI 預標。
2. 計畫要求標記資料「含個資時加密」。ADR-0008 之後會匯入 Facebook 貼文，內文可能出現姓名。
3. 使用者選擇用試算表（CSV）標記，因此需要「匯入並驗證標記」與「切分 dev／test、統計筆數」兩個步驟。原計畫沒有列出這些檔案，G1-5 的「data/labeled 統計腳本」也沒有指定檔名。
4. 實際的 migration 是 `0001_core`、`0002_audit_append_only`、`0003_ingestion_dead_letters`，但計畫中 P1-T07 寫 `0002_ingestion_dead_letters`、P2-T04 寫 `0003_ai_runs`、P3-T01 寫 `0004_users_roles`，照計畫建立檔案會撞名。

## 決策（使用者決定，2026-10-18，全部依 AI 建議）

1. **分類法**：單層 9 類，分別是 `facility`、`weather`、`security`、`traffic`、`health`、`fire`、`academic`、`infosec`、`none`。細節放在 `tags`。
2. **標記欄位**：`is_campus_relevant`、`category`、`claim_type`、`verification`、`event_group`、`related_groups`、`high_risk`、`mentions_person`、`tags`。真偽依目前蒐集到的全部證據判斷。
3. **同事件判準**：同類別、同一建築或區域，而且符合以下任一條件：官方公告的影響期間重疊；其他來源的發生時間相差 48 小時以內，並且描述同一件事。只是相近的事件標為 `related_groups`。
4. **預標**：本任務只做規則式預標，在匯出時預填。AI 預標延到 S01–S03（P2-T08）完成後再補，屆時另立任務。
5. **不加密**：`data/labeled/` 只存文章 ID、內容雜湊與標記，不存內文。含內文的匯出工作檔放在 `data/raw/labeling/`，不進 git。
6. **標記方式**：匯出 CSV 給使用者在試算表填寫，匯入時驗證；只要有一列不合格，整個檔案都不匯入。
7. **資料來源**：先用已上線的官方來源完成工具與流程；G1-5 的 100 筆可以分批累積，P1-T13 上線後再補 Facebook 樣本。
8. **檔案調整**：
   - 核心邏輯放在 `backend/app/labeling/`，納入 mypy、ruff 與 CI；
   - `scripts/labeling/` 只放命令列入口：`export_for_labeling.py`、`import_labels.py`、`dataset_stats.py`；
   - `data/labeling/label.schema.json` 由 Pydantic 模型產生，由測試確保兩者一致；
   - G1-5 以 `dataset_stats.py --check-min 100` 統計，**仍維持人工閘門**。
9. **migration 檔名**（使用者同意）：一律用實際的下一個可用編號。計畫中改寫成 `<下一個編號>_<名稱>.py`，P1-T07 更正為 `0003_ingestion_dead_letters.py`。

## 考慮過的替代方案

- **先做 P2-T03 再做 P1-T09**：可以使用 AI 預標，但會在 P1 閘門通過前就開始 P2 的任務。
- **由 Claude Code 在對話中預標**：不在產品流程內，內容會送到另一個供應商，也無法重現。
- **標記檔保存內文並加密**：無法直接用試算表標記，而且金鑰管理會增加負擔。

## 影響

- 沒有 AI 預標時，標記比較費時；規則式預標主要能省下官方來源的 `claim_type`、`verification`，以及氣象類別的填寫。
- 標記檔不含內文。評測時要用文章 ID 與內容雜湊回到資料庫取內文；內容版本改變時，雜湊不一致的標記會被列為過期。
- 樣本以官方公告為主時，`none` 與 `facility` 會占多數，類別分布不平衡。補上 Facebook 樣本前，Macro-F1 的評估代表性有限。
