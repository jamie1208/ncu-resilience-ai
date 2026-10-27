# 真實資料管線健康檢查（P4-T07）

確認真實來源每天都有產生事件，並在沒有產生時找出是哪一段出問題。G4-7（真實資料連續 3 天產生事件）用本文件的「每日檢查」判定。

## 管線流程

```
來源擷取（beat 每分鐘 dispatch-due-sources → ingest_source）
  └─ 標準化、寫入 source_items（新文章／內容改變 → analysis_status = pending）
       └─ 分析掃描（beat 每分鐘 dispatch-pending-items）
            ├─ 發布（或擷取）時間超過 14 天 → skipped
            ├─ 台北日已花費達 config/llm.yaml 的 daily_usd → 這一輪不排
            └─ 新的先排，排隊中最多 3 篇 → queued → pipeline.analyze_item
                 S01 → S02 → S03 → S04 → 建立／併入事件 → S05 → S06 → 通知
```

`analysis_status`：

| 狀態 | 意義 |
| --- | --- |
| `pending` | 等待掃描排入；失敗後未達 3 次也回到這裡 |
| `queued` | 已排入 Celery；超過 60 分鐘仍是 queued 會重新排入 |
| `done` | 管線已跑完（含 S01 判定無關、不是事件、模型 abstain） |
| `skipped` | 超過 14 天的分析時間窗，不分析 |
| `failed` | 失敗 3 次（費用上限造成的中止不計） |

Facebook 每天 08:00 由 launchd 經 `scripts/facebook/run_capture.sh` 執行 `/ncu-fb-capture`：等網路可連線才啟動，`API Error` 時隔 10 分鐘重試，最多 3 次；需要使用者處理（Chrome 擴充功能未連線）時不重試。

## 每日檢查

以下 SQL 在 repo 根目錄執行：

```bash
Q() { docker exec ncu-resilience-db-1 sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"$1\""; }
```

1. **每天新事件數**（G4-7 的判定依據：連續 3 個台北日都 ≥ 1）

   ```bash
   Q "select (created_at at time zone 'Asia/Taipei')::date as day, count(*) from events group by 1 order by 1 desc limit 7;"
   ```

2. **分析狀態**：`pending` 應該持續減少、`failed` 應該很少

   ```bash
   Q "select analysis_status, count(*) from source_items group by 1;"
   ```

3. **今日 AI 執行與費用**

   ```bash
   Q "select skill_name, status, error_type, count(*), round(sum(cost_usd),4) usd from ai_analysis_runs where created_at >= date_trunc('day', now() at time zone 'Asia/Taipei') at time zone 'Asia/Taipei' group by 1,2,3 order by 1;"
   ```

4. **管線結果分布**（今天每篇文章停在哪裡）

   ```bash
   Q "select metadata->>'outcome' outcome, metadata->>'stopped_at' stopped_at, count(*) from audit_logs where action = 'pipeline.analyze_item' and created_at >= now() - interval '1 day' group by 1,2 order by 3 desc;"
   ```

5. **擷取健康**：`make health`（7 天成功率、連續失敗、死信）

6. **Facebook**：`data/raw/facebook/` 今天是否有新檔案；`data/raw/facebook/logs/capture.log` 最後幾行。

## 常見故障與處理

| 症狀 | 可能原因 | 處理 |
| --- | --- | --- |
| `source_items` 有新文章但都停在 `pending` | worker 或 beat 沒在跑；今日費用已達上限 | `make ps` 確認 worker、beat healthy；看第 3 項今日費用是否達 `daily_usd`，是的話隔天自動恢復 |
| 很多 `queued` 長時間不動 | worker 卡在長任務或當機 | `make logs S=worker`；重啟 worker 後 60 分鐘內會自動重排 |
| `failed` 增加 | 模型 API 錯誤（金鑰、網路）、輸出驗證一直失敗 | 第 3 項看 `error_type`；修好後把該批改回 pending：`Q "update source_items set analysis_status='pending', analysis_attempts=0 where analysis_status='failed';"` |
| 分析都跑完但事件數為 0 | S01／S02／S03 把文章判為無關或不是事件 | 第 4 項看停在哪個步驟；屬於模型判斷品質，記下案例另開任務，不要調低評測門檻 |
| 某來源連續失敗、死信增加 | 來源網站連不上（常見於電腦睡眠、斷網） | `make health`；斷網造成的短暫失敗會自動重試，連續數天失敗才需要檢查 adapter |
| Facebook 沒有今天的檔案 | 08:00 網路未就緒且重試 3 次都失敗；Chrome 未開或擴充功能未連線；電腦 08:00 關機 | 看 `capture.log`；處理後執行 `scripts/facebook/install_schedule.sh run-now` |
| 修改 `run_capture.sh` 或 plist 範本後沒生效 | launchd 仍用舊的 plist | `scripts/facebook/install_schedule.sh install` 重新安裝 |

## 手動操作

- 立刻跑一次所有來源的擷取：`docker compose -f infra/docker/docker-compose.yml --env-file .env exec worker python -m app.worker.tasks.ingest --source all --once`
- 重新分析某篇文章：`Q "update source_items set analysis_status='pending', analysis_attempts=0 where id='<id>';"`，下一輪掃描會排入
- 重跑某事件的查證與風險：後台事件頁，或 `POST /api/v1/admin/events/{id}/analyze`
