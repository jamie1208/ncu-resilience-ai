import { defineConfig, devices } from '@playwright/test'

import { API_PORT, DB_NAME, PASSWORD, USERNAME, WEB_PORT } from './env'

/**
 * 附錄 A（A01–A08，個人版）端到端測試（P3-T10）：真實 FastAPI＋PostgreSQL＋前端，不模擬 API。
 *
 * 用法：在 repo 根目錄執行 `make e2e`（啟動資料庫後以 frontend 的 Playwright 執行本設定）。
 * - 後端：先以 seed_appendix_a.py 在獨立的 e2e 資料庫（E2E_DB_NAME，預設 ncu_resilience_e2e）
 *   建立案例資料，再以 uvicorn 啟動 API；不碰開發資料庫，Redis 用不同的 db 編號，
 *   開發用的 worker 不會收到這裡排入的任務。
 * - 前端：Vite 開發伺服器；VITE_DESIGN 照常決定設計系統。/api 請求由 fixtures.ts 轉到 e2e API。
 * - E2E_PASSWORD 只供這個測試資料庫的使用者，未設定時使用固定的測試值。
 */
const backendEnv = {
  NCU_DB_NAME: DB_NAME,
  NCU_REDIS_URL: process.env.E2E_REDIS_URL ?? 'redis://localhost:6379/15',
  E2E_USERNAME: USERNAME,
  E2E_PASSWORD: PASSWORD,
}

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.e2e.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? 'github' : 'list',
  outputDir: '../../frontend/test-results/appendix-a',
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `uv run python ../tests/e2e/seed_appendix_a.py && uv run uvicorn app.main:app --port ${API_PORT}`,
      cwd: '../../backend',
      env: backendEnv,
      url: `http://localhost:${API_PORT}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      cwd: '../../frontend',
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
