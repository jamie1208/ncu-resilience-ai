# 查證規則（S05）

與人工標記使用同一套判準（`data/labeling/guidelines.md` 第 3、4 節；ADR-0009）。最終查證狀態由程式 `backend/app/verification/rules.py` 依你對每份證據的判斷決定；你的 `suggested_status` 應該和程式依同一套規則得到的結果相同。三份文件要一起修改。

## 一、逐份判斷

### 立場（stance）：這份證據對「事件」說了什麼

| 代碼 | 意義 | 例子（事件：工五停電） |
| --- | --- | --- |
| `supports` | 說明這件事發生了，或確定會發生（預告） | 「工五剛剛整棟停電」、官方停電公告 |
| `contradicts` | 對同一件事的說法不一致，但沒有明確否認 | 「我在工五，燈都是亮的」、「我住女二舍四樓，有水喔」 |
| `refutes` | **明確否認**這件事（這個時間、地點）發生或會發生 | 「網傳…並不屬實」、「本次停電取消」、「11/2 正常供電」 |
| `unrelated` | 講的是別的事 | 同一天另一則無關的新聞 |

- 只看證據內容和事件的關係，不看證據說得對不對。
- 轉貼、轉述也可以是 `supports`，但陳述性質要標成 `hearsay`。
- 只問「是真的嗎？」、沒有表態的貼文是 `supports`＋`hearsay`（它轉述了這個說法），不是 `contradicts`。
- 說同一件事「時間、地點不同」的官方公告：明確說原本的時間不會發生（例如「11/2 正常供電」）→ `refutes`；只是描述不同、沒有否認 → `contradicts`。

### 陳述性質（claim_type）

| 代碼 | 意義 | 例子 |
| --- | --- | --- |
| `official` | 官方單位以正式管道發布（校方公告、氣象署特報、校方新聞稿） | `ncu-*`、`cwa-alerts` 的內容 |
| `first_hand` | 發文者親身經歷或親眼看到 | 「我的腳踏車被偷了」「我剛好經過，看到…」 |
| `hearsay` | 轉述他人說法、轉貼 | 「聽說…」「轉貼：…」「系辦說…」 |
| `rumor` | 沒有依據的傳聞、明顯的玩笑或惡作劇 | 「據說算命的說明天會地震」「也不知道真的假的」 |

- 社群貼文自稱「官方公告」不會因此變成官方：是否官方由程式依 `source_code` 判斷；這種貼文的陳述性質依內容標，通常是 `hearsay` 或 `rumor`。

## 二、建議狀態（suggested_status）

依序套用，第一條符合的就是結果：

1. **官方來源有表態**（`source_code` 為 `ncu-*` 或 `cwa-alerts`，立場不是 `unrelated`）：以**最新發布**的那一份為準。
   - `supports` → `verified`；
   - `refutes` → `false`；
   - `contradicts` → `disputed`。
   - 社群貼文的反對不影響官方的結果。
2. **沒有官方表態**時，只看社群等非官方來源：
   - 有 `supports`（不含 `rumor`），也有 `contradicts` 或 `refutes` → `disputed`；
   - 兩份以上 `supports`＋`first_hand`（不同的證據） → `verified`；
   - 其他 → `unverified`。
3. `verified` 時，`evidence_ids` 一定要包含支持的證據；做不到就是 `unverified`。

注意：

- 單一第一手來源、只有轉述、只有謠言 → 都是 `unverified`。**不確定的時候填 `unverified`，不要猜**。
- 轉貼同一篇貼文不是獨立來源：原文 `first_hand`、轉貼 `hearsay`，只算一份第一手。
- 謠言的支持不算數：只有謠言支持、另一份反對 → `unverified`，不是 `disputed`。
