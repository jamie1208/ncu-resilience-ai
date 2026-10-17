# 來源授權盤點（P0-T09）

- 盤點日期：2026-10-17（robots.txt 與條款頁為當日實際抓取結果）
- 準備：AI（Claude Code）；**核准：使用者本人**（AI 不得填寫「核准」欄）
- 依據：規格書 §4（資料來源）、§13（P1 至少三個合法來源）、§16（社群資料無合法機器存取管道 → 官方授權優先、人工匯入備援）、§18（參考資料）

> 本文件不構成法律意見。平台條款與法規可能修訂；正式部署或與校方合作前，應重新確認當時有效的條款，並取得校方窗口書面同意。

## 共通擷取原則（所有核准來源適用）

1. 遵守 robots.txt；User-Agent 標示專案名稱與聯絡信箱，不偽裝瀏覽器。
2. 每個來源同時最多 1 個連線，兩次請求間隔 ≥ 5 秒；失敗採指數退避，連續失敗即停用並告警。
3. 只保存事件分析必要的欄位（標題、發布時間、內文、原始 URL、擷取時間、內容雜湊），一律標示出處並附原文連結；公開端只顯示摘要與連結，不轉貼全文。
4. 不蒐集個人帳號、聯絡方式等個資；內文中的個資在進入日誌、評測與公開端之前先遮蔽。
5. 來源內容一律視為不可信輸入（以 `<untrusted_source>` 包裹）。
6. 每學期重新檢查一次 robots.txt 與條款；有變更時在本文件更新並重新核准。

## 總表

核准狀態只能填：`待決定`、`核准`、`不核准`、`暫緩`。

| 代碼 | 來源 | URL | 類型 | 授權依據 | robots.txt | 條款連結 | 擷取頻率（建議） | AI 建議 | 核准狀態 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ncu-announcements | 中央大學校園公告 | https://www.ncu.edu.tw/p/403-1000-15-1.php?Lang=zh-tw | 官方公告（HTML 列表） | 公開校務資訊；robots 允許；無明示禁止自動存取；只存必要欄位並標示出處 | `User-agent: * / Allow: /` | [網站安全政策](https://www.cc.ncu.edu.tw/p/412-1033-156.php)、[隱私權政策](https://www.cc.ncu.edu.tw/p/412-1033-159.php) | 每 30 分鐘 | 核准 | 待決定 |
| ncu-oga-news | 中央大學總務處最新消息 | https://www.oga.ncu.edu.tw/news | 官方公告（HTML） | 同上；施工、停水停電等營運事件的主要來源 | 不存在（HTTP 404，無限制） | 同上 | 每 30 分鐘 | 核准 | 待決定 |
| ncu-cc | 中央大學電子計算機中心公告 | https://www.cc.ncu.edu.tw/ | 官方公告（HTML） | 同上；網路與系統中斷公告 | `User-agent: * / Allow: /` | 同上 | 每 60 分鐘 | 核准 | 待決定 |
| ncu-news | 中央大學新聞網 | https://ncusec.ncu.edu.tw/news/index.php | 校方新聞（HTML） | 同上；頁面 `meta robots = index,follow` | 不存在（回傳 HTML 頁面，無規則） | 同上 | 每 6 小時 | 核准（次要，事件性較低） | 待決定 |
| cwa-alerts | 中央氣象署開放資料（天氣警特報 W-C0033-001、地震報告 E-A0015-001） | https://opendata.cwa.gov.tw/dist/opendata-swagger.html | 政府開放資料 API（JSON） | [政府資料開放授權條款－第1版](https://data.gov.tw/license)：不限目的、免授權金、不需另行申請，須依格式標示出處；需會員授權碼 | 不適用（API；opendata 站的 robots.txt 回傳 HTML，無規則） | [氣象署網站資訊使用聲明](https://www.cwa.gov.tw/V8/C/information.html) | 每 10 分鐘 | 核准 | 核准 |
| ncu-osa | 中央大學學生事務處 | https://osa.ncu.edu.tw/ | 官方公告（SPA） | 同 ncu-announcements | 不存在（回傳 SPA 頁面） | 同上 | — | 暫緩：前端渲染，需另查資料 API，P1 再評估 | 待決定 |
| ncu-aa | 中央大學教務處最新消息 | https://pdc.adm.ncu.edu.tw/p/426-1019-4.php?Lang=zh-tw | 官方公告（HTML，RPage） | 同 ncu-announcements；停課、補課、考試異動 | `User-agent: * / Allow: /` | 同上 | 每 60 分鐘 | 核准 | 待決定 |
| ncu-sec | 中央大學秘書室公告 | https://sec.ncu.edu.tw/p/403-1007-281-1.php?Lang=zh-tw | 官方公告（HTML，RPage） | 同 ncu-announcements；校級公告、緊急應變 | `User-agent: * / Allow: /` | 同上 | 每 60 分鐘 | 核准 | 待決定 |
| ncu-lib | 中央大學圖書館最新消息 | https://www.lib.ncu.edu.tw/libboard/board_all.php | 官方公告（HTML） | 同 ncu-announcements；閉館、系統與服務暫停 | 不存在（HTTP 404，無限制） | 同上 | 每 60 分鐘 | 核准 | 待決定 |
| ncu-epsc | 中央大學環境保護暨安全衛生中心最新消息 | https://ncu.edu.tw/epsc/index.php/index/News.html | 官方公告（HTML） | 同 ncu-announcements；實驗室安全、化學品、災害應變 | 依 `ncu.edu.tw`：只禁止 `/cgi-bin/` | 同上 | 每 60 分鐘 | 核准 | 待決定 |
| ncu-osa-life | 中央大學學務處生活輔導組 | https://military.ncu.edu.tw/ | 官方公告（HTML） | 同 ncu-announcements；校安、交通安全 | 不存在（HTTP 404，無限制） | 同上 | 每 60 分鐘 | 核准（公告列表網址 P1 確認） | 待決定 |
| ncu-oia | 中央大學國際事務處最新公告 | https://oia.ncu.edu.tw/p/403-1026-342.php?Lang=zh-tw | 官方公告（HTML，RPage） | 同 ncu-announcements；外籍生安全與緊急通知 | `User-agent: * / Allow: /` | 同上 | 每 6 小時 | 核准（次要） | 待決定 |
| ncu-personnel | 中央大學人事室 | https://ncu7060.ncu.edu.tw/index.php?Lang=zh-tw | 官方公告（HTML，RPage） | 同 ncu-announcements；停班相關公告 | `User-agent: * / Allow: /` | 同上 | 每 6 小時 | 核准（次要；公告列表網址 P1 確認） | 待決定 |
| ncu-rd | 中央大學研究發展處 | https://ncu.edu.tw/rd/ | 官方公告（HTML） | 同 ncu-announcements | 依 `ncu.edu.tw`：只禁止 `/cgi-bin/` | 同上 | — | 暫緩：計畫徵件為主，與韌性事件關聯低 | 暫緩 |
| ncu-acc | 中央大學主計室 | https://oa-22.adm.ncu.edu.tw/ | 官方網站 | 同 ncu-announcements | 無法取得（HTTPS 連線逾時） | 同上 | — | 暫緩：關聯低，且無法連線 | 暫緩 |
| ncu-audit | 中央大學稽核室 | https://audit.ncu.edu.tw/ | 官方網站 | 同 ncu-announcements | `User-agent: * / Allow: /` | 同上 | — | 暫緩：與韌性事件關聯低 | 暫緩 |
| ncu-ir | 中央大學校務研究辦公室 | https://ir.ncu.edu.tw/p/412-1034-82.php?Lang=zh-tw | 官方網站 | 同 ncu-announcements | `User-agent: * / Allow: /` | 同上 | — | 暫緩：與韌性事件關聯低 | 暫緩 |
| ncu-sdusr | 中央大學永續發展暨社會責任辦公室 | https://sdusr.ncu.edu.tw/ | 官方網站 | 同 ncu-announcements | `User-agent: * / Allow: /` | 同上 | — | 暫緩：與韌性事件關聯低 | 暫緩 |
| ncu-cha | 中央大學人文藝術中心 | https://cha.ncu.edu.tw/ | 官方網站 | 同 ncu-announcements | 只有 `Allow: /index.jsp`，無 Disallow | 同上 | — | 暫緩：與韌性事件關聯低 | 暫緩 |
| ncu-hesp | 中央大學高等教育深耕計畫辦公室 | https://hesp.g.ncu.edu.tw/ | 官方網站（Google Sites） | 同 ncu-announcements | 不存在（HTTP 404） | 同上 | — | 暫緩：與韌性事件關聯低 | 暫緩 |
| ncu-ori | 中央大學學術倫理辦公室 | https://ncu.edu.tw/ori/ | 官方網站 | 同 ncu-announcements | 依 `ncu.edu.tw`：只禁止 `/cgi-bin/` | 同上 | — | 暫緩：與韌性事件關聯低 | 暫緩 |
| water-outage | 台灣自來水公司停水資訊 | https://www.water.gov.tw/ | 公用事業公告 | 待查（優先找 data.gov.tw 上的開放資料集） | 規則互相矛盾（同時有 `Allow: /` 與 `Disallow: /`），保守視為禁止 | 待查 | — | 暫緩：改找開放資料集 | 待決定 |
| taipower-outage | 台灣電力公司停電資訊 | https://www.taipower.com.tw/ | 公用事業公告 | 待查 | 無法判讀（HTTP 202，內容為空） | 待查 | — | 暫緩 | 待決定 |
| dcard | Dcard 中央大學板 | https://www.dcard.tw/（中央大學相關看板，網址待確認） | 社群平台 | 規格 §16 指出社群無合法機器存取管道；[用戶使用協議](https://www.dcard.tw/terms)以程式抓取時回 403，無法確認內容 | 只禁止 `/emails/activate`（但條款優先於 robots） | https://www.dcard.tw/terms | — | 不核准自動擷取；改走人工匯入（P1-T04） | 待決定 |
| facebook | Facebook 校園社團／粉專 | https://developers.facebook.com/docs/graph-api/ | 社群平台 | 需要 Graph API 的粉專或社團權限，目前沒有 | 不適用 | Meta 平台條款 | — | 不核准；取得授權後再議 | 待決定 |
| cna | 中央社 | https://www.cna.com.tw/ | 新聞媒體 | 無公開再利用授權；2025 年曾對未授權使用其報導的資料集提告 | 一般 UA 部分允許；GPTBot、ClaudeBot 等被拒絕；`ai-train=no` | 待查（網站頁尾） | — | 不核准；如需新聞改用人工匯入連結 | 待決定 |
| google-news | Google News | https://news.google.com/ | 新聞聚合 | 無 | `User-agent: *` 的預設為 `Disallow: /`，RSS 路徑不在允許清單 | — | — | 不核准 | 待決定 |

## 逐項證據與說明

### ncu-announcements／ncu-oga-news／ncu-cc／ncu-news（校方網站）

- **robots.txt**：
  - `www.ncu.edu.tw`、`www.cc.ncu.edu.tw` 為 `User-agent: * / Allow: /`。
  - `ncu.edu.tw` 只禁止 `/cgi-bin/`。
  - `www.oga.ncu.edu.tw` 回 404。
  - `ncusec.ncu.edu.tw` 回傳一般 HTML 頁面，不是 robots 檔。
- **條款**：電算中心的「網站安全政策」是校園網路使用規範，「隱私權政策聲明」規範本站蒐集瀏覽者個資的方式。兩者都沒有明示公告內容的再利用授權，也沒有禁止自動化存取。
- **技術備註**：
  - 校園公告是 RPage 系統，列表頁為 `https://www.ncu.edu.tw/p/403-1000-15-PAGE.php?Lang=zh-tw`，詳細頁為 `/p/406-1000-<id>,r15.php`。
  - 頁面原始碼中的 RSS 選項被註解掉，所以用 HTML Adapter（P1-T03）擷取。
  - 計中的公告列表網址在 P1-T02／P1-T03 實作時確認。
- **風險**：沒有明示授權。合法性主要建立在「公開校務資訊、robots 允許、低頻率、只存必要欄位、標示出處並附連結」上。正式導入前應取得校方窗口（秘書室或計中）的書面同意。

### 各處室公告（2026-10-17 依使用者要求新增）

- **來源清單**：取自校首頁「行政」頁（`https://www.ncu.edu.tw/p/412-1000-88.php`）列出的行政單位，加上首頁連結的學務處生活輔導組。學務處、總務處、計中已在上方。
- **robots.txt**（當日抓取）：
  - `pdc.adm`、`oia`、`sec`、`ncu7060`、`ir`、`sdusr`、`audit` 為 `User-agent: * / Allow: /`。
  - `www.lib`、`military`、`hesp.g` 回 404。
  - `cha` 只有 `Allow: /index.jsp`，沒有 Disallow。
  - `oa-22.adm`（主計室）HTTPS 連線逾時，無法取得。
  - `ncu.edu.tw/epsc`、`/rd`、`/ori` 適用 `ncu.edu.tw` 的規則（只禁止 `/cgi-bin/`）。
- **條款與風險**：同校方網站，沒有明示授權；正式導入前應取得校方書面同意。
- **技術備註**：
  - 教務處、秘書室、國際處、人事室是 RPage 系統，可與校園公告共用同一個 HTML Adapter。
  - 圖書館、環安中心、生輔組是各自的系統，需要個別解析。
  - 生輔組與人事室的公告列表網址在 P1 實作時確認。
- **擷取量**：校方網站來源合計增加到 11 個核准候選。依共通擷取原則，每個網域各自限速，整體請求量仍然很低。

### cwa-alerts（中央氣象署）

- **授權**：政府資料開放授權條款第 1 版允許重製、改作、商業利用，也允許再轉授權。義務是依附件格式標示提供機關、年份、資料名稱與版本，以及授權條款網址；未標示者，視為自始未取得授權。
- **存取**：需要註冊氣象資料開放平台會員並取得授權碼。授權碼只能放在環境變數（建議名稱 `CWA_API_KEY`），不得寫進程式碼或提交。
- **適用範圍**：桃園市中壢區的天氣警特報與有感地震，用於「颱風、豪雨、地震」類校園韌性事件。

### 不核准或暫緩的理由

- **dcard、facebook**：符合規格 §16「社群資料無合法機器存取管道」的風險判斷。社群內容改由 P1-T04 人工匯入格式處理；取得平台授權後，再新增來源並重新核准。
- **cna、google-news**：媒體或聚合站沒有公開的再利用授權，或 robots 明確禁止。新聞來源若有需要，可以只保存連結與標題，由人工匯入。
- **water-outage、taipower-outage、ncu-osa**：robots 規則矛盾、無法判讀，或技術上需要另外調查。P1 期間再評估，優先改用政府開放資料。

## 核准紀錄

由使用者決定後填寫（AI 只能依使用者在對話中的明確指示代為填入，並註明日期）。

| 日期 | 代碼 | 決定 | 決定者 | 備註 |
| --- | --- | --- | --- | --- |
| 2026-10-17 | cwa-alerts | 核准 | 使用者（Jamie） | 使用者已取得氣象資料開放平台授權碼，存放於本機 `.env` 的 `CWA_API_KEY`，不提交 |
| 2026-10-17 | ncu-rd、ncu-acc、ncu-audit、ncu-ir、ncu-sdusr、ncu-cha、ncu-hesp、ncu-ori | 暫緩 | 使用者（Jamie） | 低關聯處室先標暫緩 |
