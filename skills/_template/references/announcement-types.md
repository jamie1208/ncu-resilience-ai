# 公告類型定義（template 示範用）

本檔是模板的 references 範例：長篇的分類定義、判斷準則放在這裡，SKILL.md 只放流程與指引。

## 類型

| 值 | 名稱 | 包含 | 不包含 |
| --- | --- | --- | --- |
| `class_suspension` | 停課停班 | 停止上課、停止上班、調整上課方式（改線上）、颱風或地震後的上課公告，以及「照常上班上課」的正式通知 | 單一活動取消（歸 `event`） |
| `maintenance` | 維修停機 | 停水、停電、網路或系統暫停服務、施工封閉、電梯停用 | 因停機而停課（歸 `class_suspension`） |
| `event` | 活動 | 講座、課程報名、比賽、徵才、展覽、活動異動或取消 | 例行行政通知 |
| `other` | 其他 | 獎學金、行政作業、開放時間調整、規章修訂，以及不是公告的一般文章 | — |

## 同時符合多種類型時

依影響範圍取最優先的一種：`class_suspension` ＞ `maintenance` ＞ `event` ＞ `other`。

例：「因停電，工程一館今天下午停課」→ `class_suspension`。

## 證據優先序

1. 官方來源（校內各單位網站，`ncu-*`、`cwa-alerts`）優先於社群來源（`facebook`）。
2. 同等級的證據指向不同類型、又無法依上一條決定時 → `abstain`，`abstain_reason` 為 `conflicting_evidence`。
3. 只引用實際支持判斷的證據；與判斷無關的證據不放進 `evidence_ids`。

## 證據不足

以下情況 → `abstain`，`abstain_reason` 為 `insufficient_evidence`：

- 內文只有「詳見附件」「如連結」之類的文字，無法判斷內容；
- 內文已刪除或只剩標點；
- 標題與內文都沒有可判斷類型的資訊。
