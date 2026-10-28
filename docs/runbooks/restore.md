# 備份與還原（P5-T05）

本機 docker compose 的 PostgreSQL（`db` 容器）用 `infra/scripts/backup.sh` 備份、`infra/scripts/restore.sh` 還原。G5-2（備份可完整還原）以本文件的「還原演練」判定，紀錄寫在 `docs/progress/records/P5-restore-drill.md`。

## 範圍

| 項目 | 是否備份 | 說明 |
| --- | --- | --- |
| PostgreSQL `POSTGRES_DB`（事件、證據、來源、稽核、使用者、公開內容、匿名投稿收件匣…） | ✅ | `pg_dump -Fc`，含 schema、索引、外鍵、pgvector 擴充與 `alembic_version` |
| Redis | ❌ | 只有 Celery 佇列與限速計數；遺失時 beat 會重新排程、`queued` 超過 60 分鐘會重新排入（見 `pipeline-health.md`） |
| `data/raw/facebook/` | ❌ | 擷取原始檔，已匯入的內容在資料庫中；不想遺失請另外複製 |
| `.env` | ❌ | 含密碼與金鑰，自行保管；還原到新機器時需要同一份 `.env`（至少 `POSTGRES_*` 要相同，金鑰如 `OPENAI_API_KEY` 另行設定） |
| `ncu_resilience_test`、`ncu_resilience_e2e` | ❌ | 測試用資料庫，測試會自行重建 |

## 備份

```bash
bash infra/scripts/backup.sh
```

- 需要 `db` 容器在執行中（`make up`）。服務不必停止：腳本先以 `REPEATABLE READ` 交易匯出快照，`pg_dump --snapshot` 與各表筆數統計都在同一個快照上，備份期間 worker 繼續寫入也不影響一致性。
- 輸出到 `NCU_BACKUP_DIR`（預設 `~/ncu-resilience-backups`，在 repo 外，避免誤提交）：

  | 檔案 | 內容 |
  | --- | --- |
  | `ncu-resilience-<UTC 時間>.dump` | pg_dump 自訂格式 |
  | `ncu-resilience-<UTC 時間>.counts.tsv` | 備份快照中 public schema 每張表的筆數 |
  | `ncu-resilience-<UTC 時間>.sha256` | 上面兩個檔案的 SHA-256 |

- 目錄權限 700、檔案權限 600。**備份含個資、匿名投稿內容與稽核紀錄**，不要放進 repo、雲端共享資料夾或傳給他人。
- 腳本不刪除舊備份。需要清理時自行刪除，至少保留最近一份通過還原演練的備份。
- 建議頻率：每天一次，以及執行 migration、`make reset` 或大量資料修正之前。

## 還原演練（不影響正式資料庫）

```bash
bash infra/scripts/backup.sh && bash infra/scripts/restore.sh --target local
```

`restore.sh --target local` 預設：

1. 取 `NCU_BACKUP_DIR` 最新一份備份（`--file` 可指定），核對 SHA-256；不符就停止。
2. 重建演練用資料庫 `<POSTGRES_DB>_restore_drill`，以 `pg_restore --single-transaction --exit-on-error` 還原；任何物件失敗整個還原回滾。
3. 逐表比對 `counts.tsv`：資料表集合或任一表筆數不同就以非 0 結束，並保留演練用資料庫供檢查。
4. 通過後印出表數、總筆數與 alembic 版本，刪除演練用資料庫（`--keep` 保留）。

建議每月一次，以及每次 migration 之後演練，結果附加到 `docs/progress/records/P5-restore-drill.md`。

## 正式還原（取代目前資料）

只有在正式資料庫損毀、誤刪或需要回到某個時間點時使用。**還原點之後的資料會全部遺失**。

1. 先備份目前狀態（即使已損毀，也保留現場）：`bash infra/scripts/backup.sh`；若備份失敗，記下錯誤後繼續。
2. 選定要還原的備份，先用演練確認它可完整還原：

   ```bash
   bash infra/scripts/restore.sh --target local --file ~/ncu-resilience-backups/<名稱>.dump
   ```

3. 還原到正式資料庫（需要在終端機輸入資料庫名稱確認）：

   ```bash
   bash infra/scripts/restore.sh --target local --file ~/ncu-resilience-backups/<名稱>.dump \
     --database "$(grep ^POSTGRES_DB= .env | cut -d= -f2)" --replace
   ```

   腳本會：停止 `api`、`worker`、`beat` → 刪除並重建資料庫 → 還原 → 驗證筆數 → 重新啟動三個服務（驗證失敗也會重新啟動，請依錯誤訊息處理）。

4. 若備份的 alembic 版本比程式碼舊，套用 migration：`make migrate`。
5. 確認服務：`make ps`、`make smoke`、`make health`，並依 `pipeline-health.md` 的「每日檢查」確認管線恢復。
6. Redis 不需要還原。若還原點較舊，已發布的公開內容會回到還原點的狀態；檢查後台「公開內容」頁，需要下架的再下架一次。
7. 在 `P5-restore-drill.md` 記錄這次正式還原（原因、使用的備份、遺失的時間範圍）。

## 還原到新機器

1. 複製 repo、放回原本的 `.env`、`make up`（會建立空資料庫並跑到最新 migration）。
2. 把備份三個檔案複製到新機器的 `NCU_BACKUP_DIR`。
3. 依「正式還原」第 3 步起執行（`--replace` 會先刪掉 `make up` 建立的空資料庫）。

## 常見問題

| 症狀 | 原因與處理 |
| --- | --- |
| `db 容器未就緒` | `make up` 或 `make ps` 確認 db 健康 |
| `SHA-256 不符` | 備份檔損毀或被修改；改用前一份備份 |
| `缺少 *.counts.tsv 或 *.sha256` | 三個檔案需放在同一目錄、同一名稱前綴 |
| `資料表或筆數與備份不一致` | 檢查保留下來的資料庫與 diff 輸出；不要用這份備份做正式還原 |
| `pg_restore 失敗` | 通常是 PostgreSQL 主版本或 pgvector 版本不符；確認 `db` 映像仍是 `pgvector/pgvector:pg16` |
| `資料庫 … 已存在` | 自訂 `--database` 時需加 `--replace` 才會覆蓋 |
