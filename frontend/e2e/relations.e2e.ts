import { expect, test, type Page } from '@playwright/test'

import { EVENT_ID } from '../src/test/fixtures'
import { mockBackend } from './mockApi'

async function signIn(page: Page) {
  await page.getByLabel('帳號').fill('jamie')
  await page.getByLabel('密碼').fill('correct horse')
  await page.getByRole('button', { name: '登入' }).click()
}

const PAIR = /工程一館停電 ↔ 圖書館網路維護/

test('關聯圖：確認 → 回滾 → 駁回（預設隱藏，可包含已駁回並回滾），決策都送 CSRF', async ({ page }) => {
  const { calls } = await mockBackend(page)
  await page.goto('/admin/events')
  await signIn(page)
  await page.getByRole('navigation', { name: '主要頁面' }).getByRole('link', { name: '事件關聯' }).click()
  await expect(page).toHaveURL(/\/admin\/relations$/)
  await expect(page.getByRole('heading', { level: 1, name: '事件關聯' })).toBeVisible()

  // Cytoscape 在圖區塊內畫出 canvas
  await expect(page.getByRole('region', { name: '關聯圖' }).locator('canvas').first()).toBeAttached()

  const list = page.getByRole('region', { name: '關聯清單' })
  const item = list.getByRole('button', { name: PAIR })
  await expect(item.getByText('待確認')).toBeVisible()
  await expect(list.getByRole('button', { name: /工程一館旁道路施工 → 工程一館停電/ }).getByText('推測原因')).toBeVisible()

  // 確認
  await item.click()
  const detail = page.getByRole('region', { name: '關聯詳情' })
  const form = detail.getByRole('form', { name: '記錄關聯判斷' })
  await form.getByLabel('備註（選填）').fill('同一棟大樓同時段')
  await form.getByRole('button', { name: '確認' }).click()
  await expect(detail.getByRole('status')).toHaveText('已確認這條關聯')
  await expect(item.getByText('已確認')).toBeVisible()
  const confirm = calls.find((c) => c.method === 'POST' && c.path.endsWith('/rel-1/decision'))
  expect(confirm?.csrf).toBe('csrf-e2e')
  expect(confirm?.body).toEqual({ decision: 'confirmed', note: '同一棟大樓同時段' })

  // 已確認不能直接駁回，要先回滾
  await expect(detail.getByRole('button', { name: '駁回' })).toHaveCount(0)
  await detail.getByRole('button', { name: '回滾到待確認' }).click()
  await expect(item.getByText('待確認')).toBeVisible()

  // 駁回：重新整理後預設不顯示
  await detail.getByRole('form', { name: '記錄關聯判斷' }).getByRole('button', { name: '駁回' }).click()
  await expect(item.getByText('已駁回')).toBeVisible()
  await page.reload()
  await expect(list.getByRole('button', { name: /工程一館旁道路施工/ })).toBeVisible()
  await expect(list.getByRole('button', { name: PAIR })).toHaveCount(0)

  // 包含已駁回 → 回滾
  await page.getByLabel('包含已駁回').check()
  await list.getByRole('button', { name: PAIR }).click()
  await expect(list.getByRole('button', { name: PAIR }).getByText('已駁回')).toBeVisible()
  await detail.getByRole('button', { name: '回滾到待確認' }).click()
  await expect(list.getByRole('button', { name: PAIR }).getByText('待確認')).toBeVisible()
  expect(
    calls.filter((c) => c.method === 'POST' && c.path.endsWith('/rel-1/decision')).map((c) => c.body),
  ).toEqual([
    { decision: 'confirmed', note: '同一棟大樓同時段' },
    { decision: 'pending' },
    { decision: 'rejected' },
    { decision: 'pending' },
  ])

  // 詳情連回事件
  await detail.getByRole('link', { name: '工程一館停電' }).click()
  await expect(page).toHaveURL(new RegExp(`/admin/events/${EVENT_ID}$`))
})

test.describe('手機寬度 375px', () => {
  test.use({ viewport: { width: 375, height: 812 } })

  test('關聯圖頁沒有水平捲動', async ({ page }) => {
    await mockBackend(page)
    await page.goto('/admin/relations')
    await signIn(page)
    await page.getByRole('region', { name: '關聯清單' }).getByRole('button', { name: PAIR }).click()
    await expect(page.getByRole('region', { name: '關聯詳情' })).toBeVisible()
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
