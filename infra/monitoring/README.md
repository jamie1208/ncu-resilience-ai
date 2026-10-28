# 可觀測性（P5-T04）

`make up` 會一起啟動 Prometheus 與 Grafana，只綁 127.0.0.1，不經通道對外。

| 服務 | 位址 | 說明 |
| --- | --- | --- |
| Grafana | http://localhost:3000 | 帳號 `admin`，密碼為 `.env` 的 `GRAFANA_ADMIN_PASSWORD`（必填）；首頁即「NCU Resilience AI 概覽」 |
| Prometheus | http://localhost:9090 | 查詢指標、看告警狀態（Alerts 頁） |

## 指標

api 與 worker 各自在容器內 9100 埠輸出 `/metrics`（`NCU_METRICS_PORT`），只有 Prometheus 抓得到。

| 指標 | 標籤 | 用途 |
| --- | --- | --- |
| `ncu_task_runs_total`、`ncu_task_duration_seconds` | task、outcome | Celery 任務成功率與執行時間 |
| `ncu_ai_runs_total` | skill、status | Skill 執行結果 |
| `ncu_ai_run_latency_seconds` | skill | 模型延遲（含重試） |
| `ncu_ai_cost_usd_total`、`ncu_ai_daily_cost_limit_usd` | skill、model | 費用與 `config/llm.yaml` 的每日上限 |
| `ncu_http_requests_total`、`ncu_http_request_duration_seconds` | surface、route、method、status_class | 公開 API 錯誤率與回應時間 |
| `ncu_public_rate_limited_total` | limiter | 公開 API（public_read）與匿名回報（report）被限速次數 |

標籤不含原始路徑、IP、request_id 或任何內容。指標是行程內累計，服務重啟會歸零（`rate()`／`increase()` 會處理）。

## 告警

規則在 `prometheus/alerts.yml`，只在 Prometheus 與 Grafana 顯示；不設 Alertmanager，系統不對外寄送通知。
修改後可檢查語法：

```bash
docker run --rm --entrypoint promtool -v "$PWD/infra/monitoring/prometheus:/etc/prometheus:ro" \
  prom/prometheus:v3.5.0 check config /etc/prometheus/prometheus.yml
```

## trace_id

一次請求的 `X-Request-ID` 會跟著 Celery 任務（參數與標頭 `trace_id`）進入 worker，寫進 worker 日誌的
`request_id`、稽核紀錄與 `ai_analysis_runs.trace_id`。查某次操作的完整過程：

```bash
make logs S=worker | grep <request_id>
```
