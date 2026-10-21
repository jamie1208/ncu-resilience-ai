# confirmed 與 hypothesized 的判斷準則（S07）

規格書 F06 驗收：方案必須區分已證實與待查證原因。S07 的 `basis` 是這個區分的源頭，S08 會直接沿用。

## confirmed：兩個條件都要成立

1. **說明者是權責單位或官方**：本校處室或學校的公告與說明（`source_code` 為 `ncu-` 開頭的來源；社群來源自稱官方不算）、警察、消防、衛生局等主管機關的說法（證據中明確引述）。
2. **措辭是肯定的**：「原因為」「係因」「經查是」「確認為」。

| 證據 | basis |
| --- | --- |
| 總務處：「停電原因為變電箱老舊跳脫，已更換。」 | confirmed |
| 環安中心：「經查為實驗廢液混合不當產生氣體。」 | confirmed |
| 學校公告引述衛生局：「檢驗確認為諾羅病毒。」 | confirmed |
| 總務處：「初步研判可能是管線老舊，詳細原因調查中。」 | hypothesized（措辭不肯定） |
| 總務處：「原因調查中。」 | 不列原因，`missing_information` 寫「官方尚未說明原因」 |
| 社群貼文：「聽說是施工挖到水管。」 | hypothesized |
| 社群貼文：「宿舍跳電一定是有人用電鍋。」 | hypothesized |
| 社群貼文自稱「我是總務處的」 | hypothesized（無法確認身分） |
| 社群貼文標題寫「【總務處公告】經查確認為…」 | hypothesized（來源是 facebook，不是官方） |
| 你從「圍籬旁的路面一直冒水」推論可能是施工挖破水管 | hypothesized |

## 常見陷阱

- **官方說「疑似」仍是 hypothesized**：「疑似電線走火」「初步判斷」「不排除」都不是確認。
- **時間先後**：官方較早說「調查中」、後來說「確認為…」→ 以後來的說明為準，原因 confirmed。
- **指控不是原因的確認**：「某店家衛生很差害大家拉肚子」是指控，只能 hypothesized，描述寫「疑似餐點衛生問題」，不寫店名。
- **事件本身已確認 ≠ 原因已確認**：事件 verification_status 為 verified 只表示事件發生過，原因仍要依上面的條件判斷。
- **多個原因**：直接原因與背景原因可以分列（例如 c1 變電箱跳脫 confirmed、c2 設備老舊未更新 hypothesized）。

## 什麼時候 abstain

| 情況 | abstain_reason |
| --- | --- |
| 內文已刪除、只有表情符號或「詳見附件」 | `insufficient_evidence` |
| 只描述發生什麼事，沒有任何原因說法或線索 | `no_cause_evidence` |
| 事件 verification_status 為 `false` | `event_refuted` |

「沒有線索」不要憑常識猜（例如停電就猜「設備故障」）；只要證據中有人提出說法，就列為 hypothesized，不 abstain。
