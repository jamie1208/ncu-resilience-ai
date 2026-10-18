# 人工匯入格式（P1-T04）

沒有合法機器存取管道的來源（Facebook 社團、Dcard，或使用者自己看到想補進系統的貼文），先整理成本文件的格式，放進來源專屬的資料夾，再由 `manual_import` Adapter 匯入。之後的標準化、去重、版本保存與分析沿用一般來源的流程。

- 實作：`backend/app/ingestion/adapters/manual_import.py`（來源類型 `manual_import`）
- 依據：`docs/governance/source-inventory.md`、ADR-0008（個人使用、Facebook 核准條件）
- P1-T13 的 Facebook 每日擷取直接輸出這個格式

## 放在哪裡

每個來源一個資料夾，位於 repo 的 `data/` 底下，例如 `data/raw/facebook/`。`data/raw/` 不進 git。

在 `config/sources.yaml` 登記來源（來源必須先在來源盤點中核准）：

```yaml
sources:
  facebook:
    type: manual_import
    name: Facebook NCU 社團
    poll_interval: 3600
    options:
      directory: raw/facebook   # 相對於 data/，不可用絕對路徑或 ..
      max_files: 31             # 每輪最多讀幾個新檔案（1–366，預設 31）
```

## 檔案規則

1. 副檔名只能是 `.json` 或 `.csv`，編碼 UTF-8（CSV 可以有 Excel 加上的 BOM）。其他檔案、隱藏檔、子資料夾與符號連結一律略過。
2. **檔名要能依時間排序**，建議用 `YYYY-MM-DD.json`。Adapter 依檔名排序，只讀比上次處理過的檔名「更後面」的檔案。
3. 已經匯入過的檔案，之後再修改不會被重讀。要重新匯入，請另存成新檔名（內容沒變的貼文不會產生重複資料）。
4. 寫入檔案時，先寫成其他副檔名（例如 `.json.tmp`），寫完再改名，避免 Adapter 讀到寫一半的檔案。
5. 單一檔案最大 5 MB、最多 1,000 筆。

## JSON 格式

```json
{
  "format_version": 1,
  "source_code": "facebook",
  "items": [
    {
      "url": "https://www.facebook.com/groups/example/posts/1234567890",
      "published_at": "2026-10-18T07:42:00+08:00",
      "fetched_at": "2026-10-19T08:03:00+08:00",
      "content": "工程五館今天下午兩點到四點停電，請大家提早存檔。"
    },
    {
      "url": "https://www.facebook.com/groups/example/posts/1234567891",
      "title": "宵夜街停車",
      "published_at": "2026-10-18 21:15",
      "fetched_at": "2026-10-19 08:03",
      "content": "後門機車格今天被拖吊了好幾台。"
    }
  ]
}
```

| 欄位 | 必填 | 說明 |
| --- | --- | --- |
| `format_version` | ✅ | 目前只有 `1` |
| `source_code` | ✅ | 必須等於 `config/sources.yaml` 中這個資料夾所屬的來源代碼 |
| `items` | ✅ | 可以是空陣列 |

每筆項目（`items` 的元素）：

| 欄位 | 必填 | 說明 |
| --- | --- | --- |
| `url` | ✅ | 原文網址，必須是 http(s) 絕對網址。請用不含追蹤參數的固定網址（例如 Facebook 貼文的 permalink） |
| `content` | ✅ | 內文純文字，不可空白，最多 50,000 字 |
| `published_at` | ✅ | 原文發布時間 |
| `fetched_at` | ✅ | 擷取時間 |
| `title` | | 標題；社群貼文通常沒有 |
| `external_id` | | 來源內的唯一識別碼；省略時以 `url` 代替 |

## CSV 格式

第一列是欄位名稱，欄位與 JSON 的項目相同，另外每一列都要有 `source_code`。CSV 沒有 `format_version`，視為版本 1。

```csv
source_code,url,published_at,fetched_at,title,content
facebook,https://www.facebook.com/groups/example/posts/1234567890,2026-10-18T07:42:00+08:00,2026-10-19T08:03:00+08:00,,工程五館今天下午停電
```

## 時間

- 使用 ISO 8601，例如 `2026-10-18T07:42:00+08:00`；也接受以空白分隔的 `2026-10-18 07:42`。
- 沒有時區的時間視為台北時間，存入資料庫時一律轉成 UTC。
- `fetched_at` 不可晚於匯入當下超過 5 分鐘；`published_at` 不可晚於 `fetched_at` 超過 5 分鐘。

## 不收的欄位（個資）

只接受上面列出的欄位。**出現任何其他欄位，整個檔案都會被拒絕**，不會只略過該欄位。特別是下列與人有關的欄位，依 ADR-0008 的核准條件一律不得出現：

- 發文者：`author`、`author_name`、`author_id`、`author_url`、`poster`、`user`、`user_id`、`username`、`name`、`nickname`、`account`、`profile`、`profile_url`、`avatar`、`avatar_url`
- 互動者：`comments`、`commenters`、`reactions`、`likes`
- 聯絡方式：`email`、`phone`

內文中出現的個資不會在匯入時擋下，由後續的 PII 遮蔽處理（AGENTS.md）。

## 錯誤處理

- 檔案內任何一筆不合格（缺必填欄位、多出欄位、時間無效、同一檔案內 `external_id` 重複），**整個檔案都不匯入**，這一輪擷取以不可重試的錯誤結束，並記錄在擷取紀錄與死信表（P1-T07）。
- 錯誤訊息只包含檔名、第幾筆與欄位名稱，**不包含內文或欄位值**。
- 在不合格的檔案修好或移走之前，排在它後面的檔案也不會匯入。修好後下一輪會從這個檔案繼續。
- 同一篇貼文出現在不同檔案時（例如連續兩天的擷取範圍重疊），以檔名較後面的那一份為準。
