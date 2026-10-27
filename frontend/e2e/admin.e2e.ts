import { expect, test, type Page } from '@playwright/test'

import { EVENT_ID, EVENT_REPORT } from '../src/test/fixtures'
import { mockBackend } from './mockApi'

async function signIn(page: Page) {
  await page.getByLabel('帳號').fill('jamie')
  await page.getByLabel('密碼').fill('correct horse')
  await page.getByRole('button', { name: '登入' }).click()
}

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)
}

test('管理流程：登入 → 事件查證 → 撤回通知 → Skills → 運維 → 登出', async ({ page }) => {
  const { calls } = await mockBackend(page)

  // 根路徑是登入頁，沒有公開網站
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: '登入管理後台' })).toBeVisible()
  await expect(page.getByText(/非校方官方系統/)).toBeVisible()
  await expect(page.getByText(/110/)).toBeVisible()

  await page.getByLabel('帳號').fill('jamie')
  await page.getByLabel('密碼').fill('wrong')
  await page.getByRole('button', { name: '登入' }).click()
  await expect(page.getByRole('alert')).toHaveText('帳號或密碼錯誤')

  await signIn(page)
  await expect(page).toHaveURL(/\/admin\/events$/)
  await expect(page.getByRole('heading', { level: 1, name: '事件工作台' })).toBeVisible()

  // 事件工作台：來源對照、風險分項、查證
  await page.getByRole('link', { name: /工程一館停電/ }).click()
  await expect(page).toHaveURL(new RegExp(`/admin/events/${EVENT_ID}$`))
  const sources = page.getByRole('region', { name: '來源對照' })
  await expect(sources.getByText('ncu_announcements', { exact: true })).toBeVisible()
  await expect(sources.getByText('<b>聽說</b>停電延到明天')).toBeVisible()
  const risk = page.getByRole('region', { name: '風險分項' })
  await expect(risk.getByRole('meter', { name: '營運' })).toHaveAttribute('aria-valuenow', '85')

  const verify = page.getByRole('form', { name: '記錄查證結果' })
  await verify.getByLabel('查證結果').selectOption('verified')
  await verify.getByLabel('備註（選填）').fill('已比對公告')
  await verify.getByRole('button', { name: '儲存' }).click()
  await expect(page.getByRole('status')).toHaveText('已更新為「已查證」')
  const post = calls.find((c) => c.method === 'POST' && c.path.endsWith('/verify'))
  expect(post?.csrf).toBe('csrf-e2e')
  expect(post?.body).toEqual({ status: 'verified', note: '已比對公告' })

  // 重新整理後仍為登入狀態，狀態已更新
  await page.reload()
  await expect(page.getByRole('heading', { level: 2, name: '工程一館停電' })).toBeVisible()
  await expect(page.getByRole('link', { name: /工程一館停電/ }).getByText('已查證')).toBeVisible()

  // 通知中心：撤回
  await page.getByRole('navigation', { name: '主要頁面' }).getByRole('link', { name: '通知中心' }).click()
  const alert = page.getByRole('listitem').filter({ hasText: '工程一館停電施工至 12 時' })
  await alert.getByRole('button', { name: '撤回' }).click()
  await alert.getByLabel('撤回原因').fill('停電已取消')
  await alert.getByRole('button', { name: '確認撤回' }).click()
  await expect(alert.getByText('已撤回', { exact: true })).toBeVisible()
  await expect(alert.getByRole('button', { name: '撤回' })).toHaveCount(0)

  // Skills 儀表板
  await page.getByRole('link', { name: 'Skills' }).click()
  const classifier = page.getByRole('article').filter({ hasText: 'event-classifier' })
  await expect(classifier.getByText('評測通過')).toBeVisible()
  await expect(classifier.getByText('8.3%')).toBeVisible()
  await expect(page.getByRole('article').filter({ hasText: 'risk-assessor' }).getByText('尚無此版本的評測報告')).toBeVisible()

  // 運維
  await page.getByRole('link', { name: '運維' }).click()
  const health = page.getByRole('region', { name: '來源健康' })
  await expect(health.getByRole('row').filter({ hasText: '氣象署警特報' }).getByText('失敗')).toBeVisible()
  await expect(page.getByRole('region', { name: '稽核紀錄' }).getByText('event.verify')).toBeVisible()

  // 登出後回登入頁，管理頁面也只顯示登入頁
  await page.getByRole('button', { name: '登出' }).click()
  await expect(page.getByRole('heading', { level: 1, name: '登入管理後台' })).toBeVisible()
  await page.goto('/admin/ops')
  await expect(page.getByRole('heading', { level: 1, name: '登入管理後台' })).toBeVisible()
  await expect(page.getByText('氣象署警特報')).toHaveCount(0)
})

test('事件小報告：推測與已確認分開、措施分組、建議單位；查證為不實後報告失效', async ({ page }) => {
  const { calls } = await mockBackend(page, { report: EVENT_REPORT })
  await page.goto(`/admin/events/${EVENT_ID}`)
  await signIn(page)

  const report = page.getByRole('region', { name: '事件小報告' })
  await expect(report.getByRole('region', { name: '可能的影響' }).getByText('停電影響上課與實驗。')).toBeVisible()
  const hypotheses = report.getByRole('region', { name: '值得查查看' })
  await expect(hypotheses.getByText('推測，尚未查證')).toBeVisible()
  await expect(hypotheses.getByText('疑似附近施工挖到電纜。')).toBeVisible()
  const confirmed = report.getByRole('region', { name: '已確認原因' })
  await expect(confirmed.getByText('停電原因為變電箱老舊跳脫')).toBeVisible()
  await expect(confirmed.getByText('推測，尚未查證')).toHaveCount(0)
  const measures = report.getByRole('region', { name: '可以怎麼做' })
  await expect(measures.getByRole('group', { name: '同學可以做' }).getByText('停電期間改到其他大樓自習。')).toBeVisible()
  await expect(measures.getByRole('group', { name: '學校可以做' }).getByRole('list', { name: '中期' })).toContainText('盤點同年份的變電箱')
  await expect(report.getByRole('region', { name: '建議轉給哪個單位' }).getByText('總務處')).toBeVisible()

  await report.getByRole('button', { name: '重新產生小報告' }).click()
  await expect(report.getByRole('status')).toHaveText(/已排入產生小報告/)
  const post = calls.find((c) => c.method === 'POST' && c.path.endsWith('/report'))
  expect(post?.csrf).toBe('csrf-e2e')

  const verify = page.getByRole('form', { name: '記錄查證結果' })
  await verify.getByLabel('查證結果').selectOption('false')
  await verify.getByRole('button', { name: '儲存' }).click()
  await page.reload()
  await expect(report.getByText('事件已被查證為不實，這份報告已失效。')).toBeVisible()
  await expect(report.getByRole('button', { name: /產生小報告/ })).toHaveCount(0)
})

test('未登入直接開啟事件頁：登入後留在該事件', async ({ page }) => {
  await mockBackend(page)
  await page.goto(`/admin/events/${EVENT_ID}`)
  await signIn(page)
  await expect(page).toHaveURL(new RegExp(`/admin/events/${EVENT_ID}$`))
  await expect(page.getByRole('heading', { level: 2, name: '工程一館停電' })).toBeVisible()
})

test.describe('手機寬度 375px', () => {
  test.use({ viewport: { width: 375, height: 812 } })

  test('各頁沒有水平捲動，事件詳細可回到列表', async ({ page }) => {
    await mockBackend(page)
    await page.goto('/')
    await expectNoHorizontalScroll(page)
    await signIn(page)
    await expect(page.getByRole('heading', { level: 1, name: '事件工作台' })).toBeVisible()
    await expectNoHorizontalScroll(page)

    await page.getByRole('link', { name: /工程一館停電/ }).click()
    await expect(page.getByRole('region', { name: '風險分項' })).toBeVisible()
    await expectNoHorizontalScroll(page)
    await page.getByRole('link', { name: '← 回事件列表' }).click()
    await expect(page.getByRole('link', { name: /圖書館網路維護/ })).toBeVisible()

    for (const name of ['通知中心', 'Skills', '運維']) {
      await page.getByRole('navigation', { name: '主要頁面' }).getByRole('link', { name }).click()
      await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
      await expectNoHorizontalScroll(page)
    }
  })
})
