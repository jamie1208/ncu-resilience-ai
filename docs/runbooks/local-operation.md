# 本機常駐運行（P5-T06）

讓整套系統在這台 Mac 上長期運行：開機登入後自動恢復所有服務，所有服務只監聽 127.0.0.1。對外公開走通道（P5-T15），不靠開放本機埠號（ADR-0015）。

## 組成

| 項目 | 做法 |
| --- | --- |
| 服務 | db、redis、api、worker、beat、web、prometheus、grafana（`infra/docker/docker-compose.yml`） |
| 常駐設定 | `infra/docker/docker-compose.local.yml` 疊在上面：`restart: unless-stopped`、埠號以 `!override` 取代並只綁 127.0.0.1、日誌 json-file 每個容器最多 10 MB × 5 檔 |
| 開機恢復 | Docker Desktop 登入時自動啟動 → 依 restart 策略拉起上次在執行的容器 |
| Facebook 擷取 | launchd `tw.ncu-resilience.fb-capture`，每天 08:00（P1-T13，`scripts/facebook/install_schedule.sh`） |

| 服務 | 主機位址 |
| --- | --- |
| 前端（公開網站＋管理端） | http://127.0.0.1:8088 |
| API | http://127.0.0.1:8000 |
| Grafana | http://127.0.0.1:3000 |
| Prometheus | http://127.0.0.1:9090 |
| PostgreSQL／Redis | 127.0.0.1:5432／127.0.0.1:6379 |

埠號可用 `.env` 的 `WEB_PORT`、`API_PORT` 等改變，綁定位址固定是 127.0.0.1。

## 一次性設定

1. **Docker Desktop 登入時自動啟動**：Docker Desktop → Settings → General → 勾選「Start Docker Desktop when you sign in to your computer」→ Apply。
   沒有勾選時，重新開機後要手動打開 Docker Desktop，服務才會恢復。
2. **以常駐模式啟動一次**（在 repo 根目錄）：

   ```bash
   make up-local
   ```

   之後重新開機不需要再執行；改了程式或設定要重建時也用 `make up-local`。
3. **Facebook 擷取排程**（若尚未安裝）：`scripts/facebook/install_schedule.sh install`。

## 開機後檢查

登入後等 Docker Desktop 啟動完成（約 1 分鐘），在 repo 根目錄：

```bash
make ps       # 8 個服務都是 Up (healthy)
make listen   # Docker 監聽的埠全部是 127.0.0.1:…，沒有 *: 或 0.0.0.0:
make smoke    # API health、前端首頁、前端經反向代理呼叫 API
```

若有服務不是 healthy：`make logs S=<服務名>` 查看原因；修正後 `make up-local`。

## P1-T13 排程檢查

```bash
scripts/facebook/install_schedule.sh status
```

- 應顯示已安裝、`last exit code = 0`。
- launchd 只在使用者登入後執行；08:00 時 Mac 在睡眠，會在喚醒後補跑一次；08:00 時關機則當天不會補跑，需要時用 `scripts/facebook/install_schedule.sh run-now`。
- 擷取需要 Chrome 開著、已登入 Facebook，且 Claude in Chrome 擴充功能已連線；日誌在 `data/raw/facebook/logs/`。
- 擷取檔是否被管線匯入、是否產生事件，依 `docs/runbooks/pipeline-health.md` 的「每日檢查」確認。

## 停止與恢復

| 想做的事 | 指令 | 重新開機後 |
| --- | --- | --- |
| 暫停單一服務 | `docker compose -p ncu-resilience stop <服務名>` | 不會自動拉起（unless-stopped） |
| 恢復單一服務 | `docker compose -p ncu-resilience start <服務名>` | 恢復自動重啟 |
| 停止整套並移除容器 | `make down`（保留資料庫資料） | 不會恢復，需再 `make up-local` |
| 重新建置並常駐 | `make up-local` | 自動恢復 |

`make up`（開發模式）建立的容器也會自動重啟，但沒有日誌輪替；長期運行請用 `make up-local`。

## 重新開機驗證

需要重做驗證時（例如換機器或升級 Docker Desktop 後）：

1. `make up-local`，確認 `make ps` 全部 healthy。
2. 重新開機並登入（或只結束再打開 Docker Desktop 模擬）。
3. 不執行任何 make 指令，等 Docker Desktop 啟動完成後跑「開機後檢查」三個指令。
4. 從另一台裝置（或用本機區網 IP，例如 `curl http://$(ipconfig getifaddr en0):8088/`）連線，應該全部連不上。

## 不在本文件範圍

- 備份：手動執行 `infra/scripts/backup.sh`，還原見 `docs/runbooks/restore.md`；目前沒有備份排程。
- 資料庫角色分離（應用程式改用非擁有者角色）：尚未實作，見 `docs/architecture/rbac.md`。
- 通道對外與緊急下線：P5-T15 的 `docs/runbooks/deploy.md`。
