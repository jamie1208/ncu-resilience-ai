# 已停用（P8-T13，ADR-0020 決策 3-2）

S07 root-cause-analyst 自 2026-11-09 起停用：管線不再呼叫，單一事件小報告不再產生。原因、措施與改善建議改由
S12 series-analyst 的系列分析產生（承接 `to_verify`、`audience`、`horizon`、`priority` 欄位）。

- 檔案保留作為歷史；`SKILL.md`、schema、golden cases、錄製回應與評測報告都不再修改、不再升版。
- 既有的 `event_reports` 保留唯讀，後台事件頁顯示為「舊版事件小報告（已停用）」。
