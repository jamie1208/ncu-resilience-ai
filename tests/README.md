# tests（跨前後端）

- `e2e/`：Playwright，涵蓋規格附錄 A 的 A01–A08（個人版，P3-T10）。真實 API＋PostgreSQL＋前端；在 repo 根目錄執行 `make e2e`（使用獨立的 `ncu_resilience_e2e` 資料庫，不碰開發資料）
- `load/`：k6 負載測試（P5-T07）

後端單元與整合測試在 `backend/tests/`。
