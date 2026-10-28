/**
 * 公開網站的瀏覽流程（P5-T12，ADR-0015）：真實 API 與附錄 A 案例，不登入。
 *
 * seed_appendix_a.py 建立案例後以 `sync_all` 依公開規則（P5-T10）同步公開狀態：
 * - A01 官方證實 → 公開事件內容與小報告；
 * - A02、A05 疑似指控、A07 未經查證的高風險事件 → 只有中性安全提醒；
 * - A06 已查證為不實、A03／A04／A08 未經證實且非高風險 → 不公開。
 * 這裡只透過公開前端檢查訪客看得到的內容；測試只讀不寫，可平行執行。
 */
import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'

const A01 = '工程五館外牆整修施工'
const GENERAL_SAFETY = '一般安全提醒'
/** 不能出現在任何公開頁面的字串：疑似指控、個資、社群原文、不實事件與未公開事件的標題。 */
const FORBIDDEN = [
  '室友疑似偷走宿舍交誼廳筆電',
  '室友',
  '王小明',
  '性騷擾',
  '0912345678',
  '肉搜',
  '工程四館火災',
  '總圖書館自習室筆電失竊',
  '宵夜街機車失竊',
  '學生餐廳疑似食物中毒',
  'NCU 社團',
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

test('首頁：已查證的 A01 可點進小報告；未經查證的只顯示中性安全提醒', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: '最新動態與安全提醒' })).toBeVisible()
  const feed = page.getByRole('list', { name: '最新動態' })
  await expect(feed.getByRole('link', { name: A01 })).toBeVisible()
  await expect(feed.getByText(GENERAL_SAFETY, { exact: false }).first()).toBeVisible()
  await expect(feed.getByText(/地點：校門口/)).toBeVisible()
  await expectNothingForbidden(page)
  await expectFooter(page)

  await page.getByRole('button', { name: '只看安全提醒' }).click()
  const notices = page.getByRole('list', { name: '安全提醒' })
  await expect(notices.getByRole('listitem').first()).toBeVisible()
  await expect(notices.getByRole('link')).toHaveCount(0)
  await expectNothingForbidden(page)
})

test('A01 公開版小報告：官方來源可開啟，不含原因推測', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: A01 }).click()
  await expect(page.getByRole('heading', { level: 1, name: A01 })).toBeVisible()
  await expect(page.getByText('已查證', { exact: true })).toBeVisible()
  const sources = page.getByRole('region', { name: '官方來源' })
  await expect(sources.getByRole('link', { name: '中央大學公告' })).toBeVisible()
  await expect(page.getByText('原因')).toHaveCount(0)
  await expectNothingForbidden(page)
  await expectFooter(page)
})

test('事件分類查詢只列出已查證事件', async ({ page }) => {
  await page.goto('/events')
  await expect(page.getByRole('heading', { level: 1, name: '事件分類查詢' })).toBeVisible()
  const list = page.getByRole('list', { name: '公開事件' })
  await expect(list.getByRole('link', { name: A01 })).toBeVisible()
  await expect(list.getByRole('listitem')).toHaveCount(1)

  await page.getByRole('group', { name: '事件類別' }).getByRole('button', { name: /^治安/ }).click()
  await expect(page.getByText('這個類別目前沒有已查證的公開事件。')).toBeVisible()
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
