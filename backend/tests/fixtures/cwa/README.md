# 氣象署開放資料回應（P1-T11）

依 2026-10-17 實際回應的欄位格式製作，數值與文字為虛構，不含授權碼。CI 只用這些檔案，不呼叫真實 API。

| 檔案 | 內容 |
| --- | --- |
| `warnings_taoyuan.json` | W-C0033-001，桃園市有兩項警特報 |
| `warnings_none.json` | W-C0033-001，桃園市目前沒有警特報 |
| `earthquakes.json` | E-A0015-001，三筆地震：桃園市有單獨震度、只出現在分組震度、桃園市沒有震度 |
| `unexpected.json` | `success` 為 true 但缺少 `records`（格式改變） |
