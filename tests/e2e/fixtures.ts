import { test as base, expect, type Page } from '@playwright/test'

import { API_PORT, PASSWORD, USERNAME } from './env'

export const API = `http://localhost:${API_PORT}/api/v1`

/** 頁面發出的 /api 請求轉到 e2e API（Vite 的開發代理固定指向開發用 API，不能拿來測試）。 */
export const test = base.extend({
  page: async ({ page }, use) => {
    await page.route('**/api/v1/**', (route) => {
      const url = new URL(route.request().url())
      url.host = `localhost:${API_PORT}`
      return route.continue({ url: url.toString() })
    })
    await use(page)
  },
})

export { expect }

export async function signIn(page: Page) {
  await page.goto('/admin')
  await page.getByLabel('帳號').fill(USERNAME)
  await page.getByLabel('密碼').fill(PASSWORD)
  await page.getByRole('button', { name: '登入' }).click()
  await expect(page).toHaveURL(/\/admin\/events$/)
}

/** 由事件工作台開啟指定標題的事件。 */
export async function openEvent(page: Page, title: string) {
  await page.goto('/admin/events')
  await page.getByRole('link', { name: new RegExp(title) }).click()
  await expect(page.getByRole('heading', { level: 2, name: title })).toBeVisible()
}

/** 通知中心裡屬於指定事件的通知（以事件連結定位所在的列）。 */
export function alertRows(page: Page, title: string) {
  return page.getByRole('listitem').filter({ has: page.getByRole('link', { name: title, exact: true }) })
}
