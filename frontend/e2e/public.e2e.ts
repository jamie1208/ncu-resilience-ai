import { expect, test, type Page } from '@playwright/test'

import {
  PUBLIC_CATEGORIES,
  PUBLIC_EVENT,
  PUBLIC_EVENT_DETAIL,
  PUBLIC_EVENT_ID,
  PUBLIC_NOTICE,
  PUBLIC_PROGRESS,
  PUBLIC_SERIES,
  PUBLIC_SERIES_ID,
  PUBLIC_UNVERIFIED,
} from '../src/test/publicFixtures'

/**
 * 公開網站的瀏覽流程（P5-T12）：以 page.route 模擬公開 API；任何非公開 API 的請求都讓測試失敗。
 * 真實後端與附錄 A 案例的公開瀏覽流程在 tests/e2e/public.e2e.ts（`make e2e`）。
 */
async function mockPublicApi(page: Page) {
  const others: string[] = []
  const routes: Record<string, unknown> = {
    '/feed': [PUBLIC_EVENT, PUBLIC_UNVERIFIED, PUBLIC_NOTICE],
    '/notices': [PUBLIC_NOTICE],
    '/categories': PUBLIC_CATEGORIES,
    '/events': [PUBLIC_EVENT],
    [`/events/${PUBLIC_EVENT_ID}`]: PUBLIC_EVENT_DETAIL,
    '/progress': [PUBLIC_PROGRESS],
    [`/series/${PUBLIC_SERIES_ID}`]: PUBLIC_SERIES,
  }
  await page.route('**/api/v1/**', (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1/public', '')
    const meta = { request_id: 'req-e2e', timestamp: new Date().toISOString(), next_cursor: null }
    if (!url.pathname.startsWith('/api/v1/public/') || !(path in routes)) {
      others.push(url.pathname)
      return route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ data: null, meta, error: { code: 'NOT_FOUND', message: '找不到' } }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: routes[path], meta, error: null }),
    })
  })
  return others
}

async function expectFooter(page: Page) {
  const footer = page.getByRole('contentinfo')
  await expect(footer.getByText('個人專題，非國立中央大學官方系統', { exact: false })).toBeVisible()
  await expect(footer.getByText(/110.*119.*校安中心/)).toBeVisible()
}

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)
}

test('公開瀏覽：首頁 → 快報全文 → 系列分析頁 → 分類查詢 → 改善進度 → 關於本站', async ({ page }) => {
  const others = await mockPublicApi(page)

  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: '最新校園快報' })).toBeVisible()
  await expectFooter(page)
  // 依類別分區的橫向快報卡片（ADR-0020 決策 4）；未查證的標「尚未查證」
  await expect(page.getByRole('list', { name: '設施快報' }).getByRole('listitem')).toHaveCount(1)
  const security = page.getByRole('list', { name: '治安快報' })
  await expect(security.getByText('尚未查證')).toBeVisible()
  await expect(security.getByText('已查證')).toHaveCount(0)
  await expect(page.getByText(/目前無法確認是否屬實/)).toHaveCount(0)
  await expect(page.getByRole('main').getByRole('link', { name: '校園韌性成績單' })).toBeVisible()
  await expect(page.getByRole('main').getByRole('link', { name: '匿名回報' })).toBeVisible()
  await expect(page.getByLabel('密碼')).toHaveCount(0)

  await page.getByRole('link', { name: '工程五館外牆整修施工' }).click()
  await expect(page).toHaveURL(new RegExp(`/events/${PUBLIC_EVENT_ID}$`))
  await expect(page.getByRole('heading', { level: 1, name: '工程五館外牆整修施工' })).toBeVisible()
  // 單一事件小報告停用（P8-T13）：快報全文不顯示影響與建議
  await expect(page.getByRole('region', { name: '可能的影響' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: '建議' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: '官方來源' }).getByRole('link', { name: '中央大學公告' })).toBeVisible()
  await expect(page.getByRole('region', { name: '改善進度' })).toHaveCount(0)
  await expect(page.getByText('原因')).toHaveCount(0)
  await expectFooter(page)

  await page.getByRole('button', { name: '看這系列的分析' }).click()
  await expect(page).toHaveURL(new RegExp(`/series/${PUBLIC_SERIES_ID}\\?from=${PUBLIC_EVENT_ID}$`))
  await expect(page.getByRole('heading', { level: 1, name: '工程五館施工封路' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'AI 看到的規律' }).getByText('推測，尚未查證')).toBeVisible()
  await expect(page.getByRole('region', { name: '可能原因' }).getByText('已確認')).toBeVisible()
  await expect(page.getByRole('region', { name: '改善前後比較' }).getByText(/變少不一定是措施的功勞/)).toBeVisible()
  const current = page.getByRole('region', { name: '這個系列的每一起' }).locator('[aria-current="true"]')
  await expect(current.getByText('你剛剛看的')).toBeVisible()
  await expectFooter(page)
  await current.getByRole('link', { name: '工程五館外牆整修施工' }).click()
  await expect(page.getByRole('heading', { level: 1, name: '工程五館外牆整修施工' })).toBeVisible()

  await page.getByRole('link', { name: '← 回事件查詢' }).click()
  await expect(page.getByRole('heading', { level: 1, name: '事件分類查詢' })).toBeVisible()
  await expect(page.getByRole('group', { name: '事件類別' }).getByRole('button', { name: '設施 1' })).toBeVisible()
  await expectFooter(page)

  const nav = page.getByRole('navigation', { name: '主要頁面' })
  await nav.getByRole('link', { name: '改善進度' }).click()
  await expect(page.getByRole('heading', { level: 1, name: '改善進度' })).toBeVisible()
  await expect(page.getByText('加裝夜間照明')).toBeVisible()
  await expect(page.getByRole('link', { name: '工程五館施工封路' })).toHaveAttribute('href', `/series/${PUBLIC_SERIES_ID}`)
  await expectFooter(page)

  await nav.getByRole('link', { name: '關於本站' }).click()
  await expect(page.getByRole('heading', { level: 1, name: '關於本站' })).toBeVisible()
  await expect(page.getByText(/不是國立中央大學的官方系統/)).toBeVisible()
  await expectFooter(page)

  // 直接開啟深層網址（重新整理）也能顯示
  await page.goto(`/events/${PUBLIC_EVENT_ID}`)
  await expect(page.getByRole('heading', { level: 1, name: '工程五館外牆整修施工' })).toBeVisible()

  expect(others).toEqual([])
  await expect(page.locator('a[href^="/admin"]')).toHaveCount(0)
})

test.describe('手機寬度 375px', () => {
  test.use({ viewport: { width: 375, height: 812 } })

  test('公開各頁沒有水平捲動', async ({ page }) => {
    await mockPublicApi(page)
    for (const path of ['/', '/events', `/events/${PUBLIC_EVENT_ID}`, `/series/${PUBLIC_SERIES_ID}`, '/progress', '/about']) {
      await page.goto(path)
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      await expectFooter(page)
      await expectNoHorizontalScroll(page)
    }
  })
})
