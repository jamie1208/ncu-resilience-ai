# ADR-0007：中央大學新聞網為主要新聞來源（新增 P1-T12）

- 狀態：已採納
- 日期：2026-10-18

## 背景

1. 外部新聞媒體沒有可用的自動擷取管道：中央社沒有公開的再利用授權，robots 拒絕 AI 爬蟲；Google News 的 robots 預設 `Disallow: /`。其他媒體（公視等）的條款也要求事先同意。
2. `ncu-news`（中央大學新聞網）在 P0-T09 已核准，但列為「次要」，`config/sources.yaml` 也沒有登記，目前沒有任何 Adapter 擷取它。
3. 2026-10-18 查看網站結構：
   - 新聞網有三個列表：中大新聞 `headlines.php`、焦點新聞 `focus-news.php`、學術新聞 `academic-news.php`。
   - 三個列表的文章都連到同一個詳細頁 `headlines_content.php?H_ID=<id>`，文章編號共用。每篇在列表上有分類標籤（焦點新聞、學術新聞）與日期（`YYYY-MM-DD`）。
   - 中大新聞列表涵蓋全部新聞（約 2,387 筆），焦點與學術是子分類。
   - 版型是 Bootstrap 卡片（`release-list-card`、`box-card`），不是 RPage，現有 `rpage` Adapter 不適用。
   - 分頁參數為 `s`（起始筆數）、`np`、`num_records`、`years`。
   - `ncusec.ncu.edu.tw/robots.txt` 回傳一般 HTML 頁面，沒有規則；頁面 `meta robots = index,follow`。

## 決策

1. **`ncu-news` 改為主要新聞來源**，中大新聞、焦點新聞、學術新聞三個列表都要擷取（使用者決定，2026-10-18）。
2. **其他新聞來源不納入**：`cna`、`google-news` 核准狀態改為「不核准」；不再評估報導者、公視等外部媒體（使用者決定，2026-10-18）。外部新聞若有個別需要，仍可經 P1-T04 人工匯入只保存標題與連結。
3. 新增任務 **P1-T12「中央大學新聞網 Adapter（ncu-news）」**，依賴 P1-T03（沿用 `app/ingestion/http.py`、selectolax、`LayoutChangedError` 與 User-Agent 規則），列在 P1。
4. 範圍：
   - 一個來源 `ncu-news`、一個 Adapter，`config/sources.yaml` 的 options 列出三個列表網址。
   - 以 `H_ID` 作為 `external_id` 去重；同一篇出現在多個列表時只產出一筆，分類標籤保留供後續使用。
   - 首次擷取只取各列表第一頁，不回溯全部歷史新聞。
   - 遵守既有速率限制；擷取頻率維持每 6 小時。
5. 計畫變更：`docs/progress/phases.yaml` 新增任務並執行 `sync`；執行計畫加入任務定義；任務總數 62 → 63（同步更新 AGENTS.md、`docs/architecture/project-structure.md`）。

## 考慮過的替代方案

- **擴充 `rpage` Adapter 支援新聞網**：兩者版型與分頁方式不同，混在一起會讓 RPage 解析器變複雜，版型變動時也難以判斷是哪一邊出錯。
- **只擷取中大新聞列表**：它已涵蓋全部文章，但使用者要求三個分類都納入；分類標籤也只在列表上可以穩定取得。
- **核准外部新聞媒體**：沒有公開的再利用授權（見 `docs/governance/source-inventory.md`）。

## 影響

- P1 任務數 11 → 12；P1-T10（一晚穩定運行報告）應涵蓋 `ncu-news`。
- 新聞網內容以校方新聞稿為主，事件性低於公告；它是新聞來源，不取代校園公告與 `cwa-alerts` 的示警功能。
- 校方網站都沒有明示再利用授權。正式導入前，應取得秘書室的書面同意（新聞網由秘書室維護）。
