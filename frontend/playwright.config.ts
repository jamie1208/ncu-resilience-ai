import { defineConfig, devices } from '@playwright/test'

/**
 * 管理前端的 Playwright 測試（P3-T05）：API 以 page.route 模擬，不需要後端。
 * `VITE_DESIGN=playful npm run e2e` 以 Playful Geometric 跑同一組測試。
 * 跨前後端的附錄 A 端到端測試在根目錄 tests/e2e（P3-T10）。
 */
const PORT = 4317

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    // 不重用既有伺服器，確保 VITE_DESIGN 生效
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
