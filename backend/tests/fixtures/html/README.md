# RPage 頁面快照（P1-T03）

依 2026-10-17 實際抓取的中央大學 RPage 頁面製作：**保留 class 與巢狀結構，標題、內文、人名、單位全部換成虛構文字**，並刪除選單、腳本等無關部分（使用者決定）。網址中的文章編號也是虛構的。

| 檔案 | 對應版型 |
| --- | --- |
| `rpage_list_table.html` | 校園公告列表（`table.listTB`，欄位有 `data-th`）；另含頁首、置頂舊文、外部連結、他站連結等應忽略的雜訊 |
| `rpage_list_oia.html` | 國際處列表（`table.listTB`，無 `data-th`，日期在 `i.mdate`） |
| `rpage_list_bs.html` | 秘書室列表（`div.listBS`，日期在 `i.mdate`） |
| `rpage_list_no_module.html` | 版型改變：找不到列表模組 |
| `rpage_detail.html` | 內頁（`h2.hdline`、`ptinfoproperty_date`、`div.meditor`） |
| `rpage_detail_no_body.html` | 內頁只有附件、沒有內文 |
| `rpage_detail_no_date.html` | 國際處內頁：不顯示發布日期，內文前有圖片輪播與腳本（日期改用列表頁） |
| `rpage_detail_no_title.html` | 版型改變：內頁缺標題 |
