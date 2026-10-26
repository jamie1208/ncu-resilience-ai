/**
 * 規格附錄 A 的 A01–A08，期望結果依個人版改寫（ADR-0008；P3-T10）。
 *
 * 資料由 seed_appendix_a.py 經真實的冪等寫入與分析管線建立（模型以腳本化的假供應商代替），
 * 這裡只透過真實 API 與前端驗證使用者看得到的結果；測試只讀不寫，可平行執行。
 * 只有資料庫看得到的不變量（A03 證據數、A07 原文／事件／通知／遞送各一筆）由種子腳本檢查，
 * 不成立時 webServer 啟動失敗，整組測試不會通過。
 */
import { API, alertRows, expect, openEvent, signIn, test } from './fixtures'

const GENERAL_SAFETY = '一般安全提醒'
const ANTI_THEFT = '請注意個人財物與人身安全'

test('G3-3：未登入看不到任何事件資料', async ({ page, request }) => {
  for (const path of ['/admin/events', '/admin/alerts', '/admin/dashboard']) {
    const res = await request.get(`${API}${path}`)
    expect(res.status(), path).toBe(401)
  }
  await page.goto('/admin/events')
  await expect(page.getByRole('heading', { level: 1, name: '登入管理後台' })).toBeVisible()
  await expect(page.getByText('工程五館外牆整修施工')).toHaveCount(0)
})

test.describe('附錄 A（個人版）', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page)
  })

  test('A01 官方施工公告：建立已查證的設施事件，來源可開啟', async ({ page }) => {
    await expect(page.getByRole('link', { name: /工程五館外牆整修施工/ }).getByText('已查證')).toBeVisible()
    await openEvent(page, '工程五館外牆整修施工')
    await expect(page.getByText('facility', { exact: true })).toBeVisible()
    const sources = page.getByRole('region', { name: '來源對照' })
    await expect(sources.getByText('ncu-announcements', { exact: true })).toBeVisible()
    await expect(sources.getByText('支持')).toBeVisible()
    await expect(sources.getByRole('link', { name: /開啟原文/ })).toHaveAttribute(
      'href',
      'https://example.test/ncu-announcements/a01',
    )
    // 低風險不通知本人
    await page.goto('/admin/alerts')
    await expect(page.getByText('目前沒有通知').or(page.getByRole('list'))).toBeVisible()
    await expect(alertRows(page, '工程五館外牆整修施工')).toHaveCount(0)
  })

  test('A02 社群單篇失竊指控：未查證；只發中性防竊提醒，不呈現指控', async ({ page }) => {
    await expect(page.getByRole('link', { name: /室友疑似偷走宿舍交誼廳筆電/ }).getByText('未查證')).toBeVisible()
    await page.goto('/admin/alerts')
    const row = alertRows(page, '室友疑似偷走宿舍交誼廳筆電')
    await expect(row).toHaveCount(1)
    const body = row.locator('p').first()
    await expect(body).toContainText(GENERAL_SAFETY)
    await expect(body).toContainText(ANTI_THEFT)
    await expect(body).toContainText('目前無法確認是否屬實')
    for (const accusation of ['室友', '偷', '筆電', '交誼廳']) {
      await expect(body).not.toContainText(accusation)
    }
  })

  test('A03 三則相同失竊新聞轉載：事件數 1、來源證據數 3', async ({ page }) => {
    const links = page.getByRole('link', { name: /總圖書館自習室筆電失竊/ })
    await expect(links).toHaveCount(1)
    await expect(links.getByText('證據 3')).toBeVisible()
    await openEvent(page, '總圖書館自習室筆電失竊')
    await expect(page.getByRole('region', { name: '來源對照' }).getByRole('listitem')).toHaveCount(3)
  })

  test('A04 兩起不同日失竊：建立兩個不同事件', async ({ page }) => {
    // related_event 關聯的呈現屬於 P4-T01／P4-T02（使用者 2026-10-26 決定）
    await expect(page.getByRole('link', { name: /宵夜街機車失竊/ })).toHaveCount(1)
    await expect(page.getByRole('link', { name: /後門停車場機車失竊/ })).toHaveCount(1)
  })

  test('A05 匿名惡意誣指個人：通知不呈現指控與個資，管理端標記異常', async ({ page }) => {
    await expect(page.getByRole('link', { name: /資工系王小明老師性騷擾學生/ }).getByText('未查證')).toBeVisible()
    await page.goto('/admin/alerts')
    const row = alertRows(page, '資工系王小明老師性騷擾學生')
    await expect(row).toHaveCount(1)
    await expect(row.getByText('疑似指控，需人工判斷')).toBeVisible()
    const body = row.locator('p').first()
    await expect(body).toContainText(GENERAL_SAFETY)
    for (const text of ['王小明', '老師', '性騷擾', '資工系', '0912345678', '工程三館']) {
      await expect(body).not.toContainText(text)
    }
    // 沒有指控的通知不會被標記
    await expect(alertRows(page, '中大路與校門口車禍封路').getByText('疑似指控，需人工判斷')).toHaveCount(0)
  })

  test('A06 官方澄清：事件改為不實，已發出的通知自動撤回', async ({ page }) => {
    await expect(page.getByRole('link', { name: /工程四館火災/ }).getByText('不實')).toBeVisible()
    await openEvent(page, '工程四館火災')
    const sources = page.getByRole('region', { name: '來源對照' })
    await expect(sources.getByRole('listitem')).toHaveCount(2)
    await expect(sources.getByText('ncu-announcements', { exact: true })).toBeVisible()

    await page.goto('/admin/alerts')
    const row = alertRows(page, '工程四館火災')
    await expect(row).toHaveCount(1)
    await expect(row.getByText('已撤回')).toBeVisible()
    await expect(row.getByRole('button', { name: '撤回' })).toHaveCount(0)
  })

  test('A07 重送相同任務：事件與通知都不重複', async ({ page }) => {
    await expect(page.getByRole('link', { name: /中大路與校門口車禍封路/ })).toHaveCount(1)
    await expect(page.getByRole('link', { name: /中大路與校門口車禍封路/ }).getByText('證據 1')).toBeVisible()
    await page.goto('/admin/alerts')
    const row = alertRows(page, '中大路與校門口車禍封路')
    await expect(row).toHaveCount(1)
    await expect(row.getByText('v1', { exact: true })).toBeVisible()
    await expect(row.getByText('已通知')).toBeVisible()
  })

  test('A08 模型輸出無 evidence_ids：不得標為已查證', async ({ page }) => {
    const link = page.getByRole('link', { name: /學生餐廳疑似食物中毒/ })
    await expect(link.getByText('未查證')).toBeVisible()
    await expect(link.getByText('已查證')).toHaveCount(0)
  })
})
