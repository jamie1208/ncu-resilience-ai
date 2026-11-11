/**
 * 附錄 A 的 A10 通知失敗（P8-T07，v1.0 A10）。
 *
 * seed_appendix_a.py 以兩個替身外部管道模擬失敗：一個重試 1 次後成功、一個重試 3 次仍失敗就停止，
 * 並檢查只有資料庫看得到的不變量（每個管道一列遞送、嘗試次數、停止後不再排定、同版本不重複遞送），
 * 不成立時 webServer 啟動失敗。這裡只驗證使用者看得到的結果：通知中心裡這則通知只出現一次、
 * 版本不變、狀態為已通知。
 */
import { alertRows, expect, signIn, test } from './fixtures'

const A10_TITLE = '環校道路坍方封閉'

test('A10 通知遞送失敗後重試：通知中心只有一則、版本不變', async ({ page }) => {
  await signIn(page)
  await page.goto('/admin/alerts')
  const row = alertRows(page, A10_TITLE)
  await expect(row).toHaveCount(1)
  await expect(row.getByText('v1', { exact: true })).toBeVisible()
  await expect(row.getByText('已通知')).toBeVisible()
})
