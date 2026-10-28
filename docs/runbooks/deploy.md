# 通道對外上線（P5-T15）

公開網站經 Cloudflare Quick Tunnel 對外（ADR-0015，使用者決定 2026-10-28：Quick Tunnel、不使用網域）。管理端（`/admin`、`/api/v1/admin/*`）與登入（`/api/v1/auth/*`）不經通道，只能在本機 http://127.0.0.1:8088 使用。

## 組成

```text
任何人 → https://<隨機>.trycloudflare.com → cloudflared（tunnel 容器）
       → public-gateway（nginx，白名單）→ web（公開網站頁面、/assets/）
                                        → api（只有 /api/v1/public/*）
本機  → 127.0.0.1:8088（web，含管理端）、127.0.0.1:8090（公開閘道，上線前驗證用）
```

| 檔案 | 用途 |
| --- | --- |
| `infra/tunnel/nginx-public.conf` | 公開閘道白名單：`/`、`/events`、`/events/<id>`、`/progress`、`/about`、`/report`、`/assets/*`、`/api/v1/public/*`；其他一律 404 |
| `infra/tunnel/docker-compose.tunnel.yml` | `public-gateway`、`tunnel`（profile `tunnel`），以及讓 api 信任閘道轉送的用戶端位址 |
| `infra/tunnel/tunnel.sh` | `gateway`／`check`／`up`／`url`／`down`／`status` |

閘道的其他保護：

- 不記存取日誌；往內轉送時清掉 Cookie、Authorization、User-Agent、Referer，回應不帶 Set-Cookie。
- 用戶端位址取自 Cloudflare 的 `CF-Connecting-IP`，覆寫（不附加）`X-Forwarded-For` 後交給 api，限速依每個用戶端計算；使用者自帶的 `X-Forwarded-For` 無效。
- 閘道層另有限速（每用戶端每秒 10 次、突發 40），超過回 429；api 本身的每 60 秒 60 次照常生效。
- 含 `%2e`、`%2f`、`%5c`、`..`、`//` 的路徑直接 404；公開頁面只允許 GET，公開 API 只允許 GET 與 POST。
- 公開 API 回應帶 `Cloudflare-CDN-Cache-Control: no-store`，Cloudflare 邊緣不快取，下架立即生效（瀏覽器最多殘留 30 秒）。

Quick Tunnel 的特性（選用時已知）：

- 不需要 Cloudflare 帳號、網域或憑證；**網址每次啟動 tunnel 容器都會換**，舊網址立即失效。
- 沒有可用性保證，Cloudflare 可能限制流量；電腦關機或睡眠時公開網站停止服務（ADR-0015 已接受）。
- 若日後要固定網址，改用具名通道（需帳號與網域），屬於新的決定。

## 上線前檢查（G5-8）

上線前全部完成才執行「上線」：

1. 使用者本人審閱 `docs/architecture/threat-model-public.md` 與公開端點清單並同意上線（G5-8）。
2. P5 驗證全過（在 repo 根目錄）：

   ```bash
   cd backend && uv run pytest tests/unit/publishing tests/security tests/integration/test_public_api.py tests/integration/test_takedown.py tests/unit/test_tunnel_config.py -q && cd ..
   make e2e
   ```

3. 系統以常駐模式運行（`docs/runbooks/local-operation.md`），並啟動公開閘道、從本機檢查：

   ```bash
   bash infra/tunnel/tunnel.sh gateway
   bash infra/tunnel/tunnel.sh check
   ```

   最後一行必須是「結果：只有公開路徑可存取」。

## 上線

```bash
bash infra/tunnel/tunnel.sh up
```

會先再跑一次本機檢查，通過才啟動通道，最後印出 `https://<隨機>.trycloudflare.com`。

接著**從外部網路**驗證（手機關 Wi‑Fi 用行動網路開熱點給另一台電腦，或請別人執行）：

```bash
bash infra/tunnel/tunnel.sh check https://<隨機>.trycloudflare.com
```

另外用手機瀏覽器手動確認：首頁可開、`/admin` 顯示 404、送出一筆測試回報後能在後台收件匣看到（看完可在後台處理掉）。把網址、時間與檢查結果記到驗收紀錄。

之後隨時可用 `bash infra/tunnel/tunnel.sh url` 查目前網址。

## 暫停

```bash
bash infra/tunnel/tunnel.sh down
```

停止並移除 tunnel 容器：網址立即失效，公開網站不再對外；閘道、管理端、擷取與 AI 管線照常在本機運作。恢復時重新 `up`，會拿到新網址。

## 緊急下線

發現不該公開的內容、管理端疑似可從外部觸及、或遭大量請求時：

1. **立刻切斷**：`bash infra/tunnel/tunnel.sh down`（幾秒內生效；Quick Tunnel 網址隨即失效，沒有邊緣快取要清）。
   Docker 無法操作時，直接結束 Docker Desktop，或關閉電腦網路。
2. 確認已下線：`bash infra/tunnel/tunnel.sh status` 不再列出 tunnel；舊網址開啟失敗。
3. 個別內容問題：在後台「公開內容」頁（`/admin/publications`）下架，留下稽核（P5-T14）。
4. 查原因：`docker compose ... logs public-gateway api`（閘道不記存取日誌，只有錯誤日誌；api 只有方法、路徑、狀態碼）；Grafana 的公開 API 錯誤率與限速面板。
5. 修正後重新走「上線前檢查」再 `up`。

## 注意事項

- **重開機**：tunnel 容器是 `restart: unless-stopped`，上線中重開機會自動恢復對外，但網址會換。不想自動恢復，關機前先 `down`。
- **重建服務**：`make up-local` 不包含本覆寫檔，會把 api 改回不信任閘道標頭（所有通道請求被算成同一個用戶端，容易被限速）。通道在用時，`make up-local` 後再執行一次 `bash infra/tunnel/tunnel.sh gateway`。
- **不要調高 cloudflared 的日誌等級到 debug**：debug 會逐筆記錄請求。
- 新增公開頁面路徑（`frontend/src/public/routes.ts`）時，要同步更新 `nginx-public.conf` 的白名單與 `tunnel.sh check` 的清單，否則新頁面從外部會是 404。
