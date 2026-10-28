# 還原演練紀錄（G5-2）

程序見 `docs/runbooks/restore.md`。每次演練或正式還原都附加一節，最新的在最上面。

## 2026-10-28 migration 後複驗（P5-T05 結案）

- 執行者：AI（Claude Code）；指令同下方首次演練
- 背景：正式資料庫剛由 0012 升到 0014（`make migrate`）並以最新程式碼重建容器（`make up`）、補建 `anonymous-report` 來源（`make sources`）
- 備份：`~/ncu-resilience-backups/ncu-resilience-20261028T063709Z.dump`（608K），服務運行中備份
- 結果：✅ 通過。SHA-256 核對通過；24 張表、共 4573 筆逐表一致；alembic 版本 `0014_anonymous_reports`；演練庫已刪除

## 2026-10-28 首次演練（P5-T05）

- 執行者：AI（Claude Code），使用者本人的本機環境
- 環境：macOS、Docker 29.0.1、`pgvector/pgvector:pg16`（pg_dump 16.15）、pgvector 0.8.7
- 指令：

  ```bash
  bash infra/scripts/backup.sh && bash infra/scripts/restore.sh --target local
  ```

- 備份：`~/ncu-resilience-backups/ncu-resilience-20261028T061409Z.dump`（584K），服務持續運行中備份
- 還原目標：演練用資料庫 `ncu_resilience_restore_drill`（正式資料庫 `ncu_resilience` 未變動）
- 耗時：備份約 1 秒、還原與驗證約 1 秒
- 結果：✅ 通過

| 檢查 | 備份 | 還原後 |
| --- | --- | --- |
| SHA-256（dump、counts） | — | 核對通過 |
| public schema 資料表 | 20 | 20 |
| 總筆數 | 4497 | 4497（逐表一致，見下表） |
| alembic 版本 | 0012_response_actions | 0012_response_actions |
| 索引（另以 `--keep` 查詢） | 60 | 60 |
| 外鍵（另以 `--keep` 查詢） | 26 | 26 |
| 擴充 | vector | vector 0.8.7 |

逐表筆數：ai_analysis_runs 487、alembic_version 1、alert_deliveries 6、alerts 6、audit_logs 939、event_evidence 55、event_relations 9、event_reports 3、events 13、ingestion_dead_letters 283、ingestion_runs 2017、locations 4、response_action_transitions 0、response_actions 0、risk_assessments 54、source_item_versions 304、source_items 303、sources 11、user_sessions 1、users 1。

失敗路徑（以複製到暫存目錄的備份測試，結束碼皆非 0，正式資料庫未變動）：

| 情境 | 結果 |
| --- | --- |
| dump 被修改一個位元組 | SHA-256 不符，未還原 |
| counts.tsv 某表筆數被改（並重算 SHA-256） | 還原後 diff 不一致，以 1 結束並保留演練庫 |
| 未指定 `--target` | 顯示用法，以 2 結束 |
| `--database 'x;drop'`、`--database postgres` | 名稱檢查拒絕 |
| `--database ncu_resilience` 未加 `--replace` | 拒絕 |
| `--database ncu_resilience --replace` 但非終端機 | 拒絕（需互動確認） |
| 自訂資料庫已存在未加 `--replace` | 拒絕；加 `--replace` 後還原並驗證通過 |

未演練：還原到正式資料庫（`--database ncu_resilience --replace`，會停止 api／worker／beat 並取代現有資料）。此路徑與演練共用同一段還原與驗證程式，差別只在互動確認與停止／啟動服務；要演練需使用者本人在終端機執行。
