/** e2e 環境設定（Playwright 設定與測試共用）。E2E_PASSWORD 只供 e2e 測試資料庫的使用者。 */
export const API_PORT = Number(process.env.E2E_API_PORT ?? 8001)
export const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 4318)
export const USERNAME = process.env.E2E_USERNAME ?? 'owner'
export const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-only-password'
export const DB_NAME = process.env.E2E_DB_NAME ?? 'ncu_resilience_e2e'
