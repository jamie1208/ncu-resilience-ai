# 中央大學新聞網頁面快照（P1-T12）

依 2026-10-18 實際抓取的 `https://ncusec.ncu.edu.tw/news/` 頁面製作：**保留 class 與巢狀結構，標題、內文、人名、單位全部換成虛構文字**，並刪除選單以外的腳本、圖片輪播等無關部分（沿用 P1-T03 的做法）。文章編號 `H_ID` 也是虛構的。

| 檔案 | 對應頁面 |
| --- | --- |
| `headlines.html` | 中大新聞列表（`div.release-list-card`）：9003 焦點、9002 學術、9001 焦點；另含選單、他站連結、沒有標題的連結等應忽略的雜訊 |
| `focus_news.html` | 焦點新聞列表（`a.box-card`）：9003、9001 |
| `academic_news.html` | 學術新聞列表（`a.box-card`）：9002，以及不在中大新聞第一頁的較舊文章 8001 |
| `list_no_cards.html` | 版型改變：找不到任何新聞卡片 |
| `detail.html` | 內頁（`section.main-detail-box`：`h3.main-title`、「發布日期：」、`div.main-detail-content-box`） |
| `detail_no_body.html` | 內頁只有圖片、沒有文字內文 |
| `detail_no_date.html` | 內頁不顯示發布日期（日期改用列表頁） |
| `detail_no_title.html` | 版型改變：內頁缺標題 |
| `robots_html.html` | 網站的 `robots.txt` 實際回傳一般 HTML 頁面（沒有規則） |
