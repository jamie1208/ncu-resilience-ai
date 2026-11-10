/**
 * 公開網站的瀏覽流程（P5-T12，ADR-0015；P8-T06 起依 ADR-0018、ADR-0020）：真實 API 與附錄 A 案例，
 * 不登入。
 *
 * seed_appendix_a.py 建立案例、把每個事件歸入 1 件的系列後，以 `sync_all` 依公開規則同步：
 * - A01 官方證實 → 快報標「已查證」；
 * - A02、A05 疑似指控、A03／A04／A07／A08 未經證實 → 公開 AI 摘要並標「尚未查證」（ADR-0018 2-1、2-2）；
 * - A06 已查證為不實 → 不公開；
 * - 社群貼文原文、個資、Facebook 來源名稱永不公開；每則快報都有系列分析頁（ADR-0020 決策 1）。
 * 這裡只透過公開前端檢查訪客看得到的內容；測試只讀不寫，可平行執行。
 */
import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'

const A01 = '工程五館外牆整修施工'
const A02 = '室友疑似偷走宿舍交誼廳筆電'
/** 不能出現在任何公開頁面的字串：社群貼文原文、個資、不實事件、Facebook 來源名稱。 */
const FORBIDDEN = [
  '大家小心他',
  '同層的室友偷的',
  '0912345678',
  '0912-345-678',
  '肉搜',
  '工程四館火災',
  'NCU 社團',
  'Facebook',
  'facebook',
]
const PAGES = ['/', '/events', '/progress', '/about']

async function expectFooter(page: Page) {
  const footer = page.getByRole('contentinfo')
  await expect(footer.getByText('個人專題，非國立中央大學官方系統', { exact: false })).toBeVisible()
  await expect(footer.getByText(/110.*119.*校安中心/)).toBeVisible()
}

async function expectNothingForbidden(page: Page) {
  const text = await page.locator('body').innerText()
  for (const word of FORBIDDEN) expect(text, `公開頁面出現「${word}」`).not.toContain(word)
}

test('首頁：依類別分區的快報卡片；A01 標已查證，未經查證與疑似指控的標尚未查證', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: '最新校園快報' })).toBeVisible()
  const facility = page.getByRole('list', { name: '設施快報' })
  const a01 = facility.getByRole('listitem').filter({ hasText: A01 })
  await expect(a01.getByText('已查證', { exact: true })).toBeVisible()
  await expect(a01.getByText('單一事件')).toBeVisible()

  const security = page.getByRole('list', { name: '治安快報' })
  const a02 = security.getByRole('listitem').filter({ hasText: A02 })
  await expect(a02.getByText('尚未查證', { exact: true })).toBeVisible()
  await expect(a02.getByText('已查證', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('list', { name: '火災快報' })).toHaveCount(0) // A06 不實
  await expect(page.getByRole('main').getByRole('link', { name: '校園韌性成績單' })).toBeVisible()
  await expect(page.getByRole('main').getByRole('link', { name: '匿名回報' })).toBeVisible()
  await expectNothingForbidden(page)
  await expectFooter(page)
})

test('A01 快報全文 → 系列分析頁：官方來源可開啟，單一事件不找規律，標出剛剛看的那一則', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: A01 }).click()
  await expect(page.getByRole('heading', { level: 1, name: A01 })).toBeVisible()
  await expect(page.getByText('已查證', { exact: true })).toBeVisible()
  const sources = page.getByRole('region', { name: '官方來源' })
  await expect(sources.getByRole('link', { name: '中央大學公告' })).toBeVisible()
  await expect(page.getByText('原因')).toHaveCount(0)
  await expectNothingForbidden(page)
  await expectFooter(page)

  await page.getByRole('button', { name: '看這系列的分析' }).click()
  await expect(page).toHaveURL(/\/series\/[^/?]+\?from=/)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByText('要至少 2 起才會找規律')).toBeVisible()
  const current = page.getByRole('region', { name: '這個系列的每一起' }).locator('[aria-current="true"]')
  await expect(current.getByRole('link', { name: A01 })).toBeVisible()
  await expectNothingForbidden(page)
  await expectFooter(page)
})

test('疑似指控的快報全文只有 AI 摘要與「尚未查證」，沒有社群原文與來源', async ({ page }) => {
  await page.goto('/events')
  await page.getByRole('group', { name: '事件類別' }).getByRole('button', { name: /^治安/ }).click()
  await page.getByRole('link', { name: A02 }).click()
  await expect(page.getByRole('heading', { level: 1, name: A02 })).toBeVisible()
  await expect(page.getByText('尚未查證', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '官方來源' })).toHaveCount(0)
  await expectNothingForbidden(page)
})

test('事件分類查詢列出公開快報並帶查證標籤，不含不實事件', async ({ page }) => {
  await page.goto('/events')
  await expect(page.getByRole('heading', { level: 1, name: '事件分類查詢' })).toBeVisible()
  const list = page.getByRole('list', { name: '公開事件' })
  await expect(list.getByRole('link', { name: A01 })).toBeVisible()
  await expect(list.getByRole('link', { name: A02 })).toBeVisible()

  await page.getByRole('group', { name: '事件類別' }).getByRole('button', { name: /^火災/ }).click()
  await expect(page.getByText('這個類別目前沒有公開的快報。')).toBeVisible()
  await expectNothingForbidden(page)
})

test('每個公開頁面都有非官方聲明與緊急聯絡方式，沒有管理端連結與禁止內容', async ({ page }) => {
  for (const path of PAGES) {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await expectFooter(page)
    await expectNothingForbidden(page)
    await expect(page.locator('a[href^="/admin"]')).toHaveCount(0)
  }
})

test('公開網站不需要登入，管理端仍要求登入', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByLabel('密碼')).toHaveCount(0)
  await page.goto('/admin/events')
  await expect(page.getByRole('heading', { level: 1, name: '登入管理後台' })).toBeVisible()
})
