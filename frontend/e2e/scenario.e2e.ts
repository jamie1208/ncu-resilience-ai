import { expect, test, type Page } from '@playwright/test'

import { mockBackend } from './mockApi'

async function signIn(page: Page) {
  await page.getByLabel('帳號').fill('jamie')
  await page.getByLabel('密碼').fill('correct horse')
  await page.getByRole('button', { name: '登入' }).click()
}

test('What-if：填參數 → 執行 → 結果、龍捲風圖與 AI 解釋，寫入送 CSRF；重新整理後可從紀錄開啟', async ({ page }) => {
  const { calls } = await mockBackend(page)
  await page.goto('/admin/events')
  await signIn(page)
  await page.getByRole('navigation', { name: '主要頁面' }).getByRole('link', { name: 'What-if 模擬' }).click()
  await expect(page).toHaveURL(/\/admin\/scenarios$/)
  await expect(page.getByRole('heading', { level: 1, name: 'What-if 模擬' })).toBeVisible()
  await expect(page.getByRole('note', { name: '假設情境，非成效預測' })).toBeVisible()

  const form = page.getByRole('form', { name: '模擬參數' })
  await form.getByLabel('基準件數').fill('12')
  await form.getByLabel('期間（天）').fill('90')
  const m1 = form.getByRole('group', { name: '措施 1' })
  await m1.getByLabel('名稱').fill('加裝路燈')
  await m1.getByLabel('效果下限（%）').fill('20')
  await m1.getByLabel('效果上限（%）').fill('40')
  await form.getByRole('button', { name: '新增措施' }).click()
  const m2 = form.getByRole('group', { name: '措施 2' })
  await m2.getByLabel('名稱').fill('增加夜間巡邏')
  await m2.getByLabel('效果下限（%）').fill('10')
  await m2.getByLabel('效果上限（%）').fill('20')
  await form.getByLabel('隨機種子（選填）').fill('42')
  await form.getByRole('button', { name: '執行模擬' }).click()

  const result = page.getByRole('region', { name: '模擬結果' })
  await expect(result.getByText('假設情境，非成效預測')).toBeVisible()
  await expect(result.getByText('7.1', { exact: true })).toBeVisible()
  const chart = page.getByRole('figure', { name: /敏感度（龍捲風圖）/ })
  await expect(chart.getByRole('listitem').first()).toContainText('加裝路燈')
  await expect(page.getByRole('region', { name: 'AI 解釋' }).getByText(/最影響結果的假設：加裝路燈/)).toBeVisible()

  const post = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/admin/scenarios')
  expect(post?.csrf).toBe('csrf-e2e')
  expect(post?.body).toEqual({
    baseline: 12,
    window_days: 90,
    seed: 42,
    measures: [
      { name: '加裝路燈', effect_low: 0.2, effect_high: 0.4, cost: null, rationale: null },
      { name: '增加夜間巡邏', effect_low: 0.1, effect_high: 0.2, cost: null, rationale: null },
    ],
  })

  // 重新整理後從最近的模擬開啟
  await page.reload()
  await expect(page.getByRole('region', { name: '模擬結果' })).toHaveCount(0)
  await page.getByRole('region', { name: '最近的模擬' }).getByRole('button', { name: /加裝路燈/ }).click()
  await expect(page.getByRole('region', { name: '模擬結果' }).getByText('7.1', { exact: true })).toBeVisible()
})

test.describe('手機寬度 375px', () => {
  test.use({ viewport: { width: 375, height: 812 } })

  test('What-if 結果頁沒有水平捲動', async ({ page }) => {
    await mockBackend(page, { scenarios: 'seeded' })
    await page.goto('/admin/scenarios')
    await signIn(page)
    await page.getByRole('region', { name: '最近的模擬' }).getByRole('button', { name: /加裝路燈/ }).click()
    await expect(page.getByRole('figure', { name: /敏感度（龍捲風圖）/ })).toBeVisible()
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
