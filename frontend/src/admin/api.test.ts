import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi } from '../test/mockApi'
import {
  clearCsrfToken,
  getEvent,
  listEvents,
  login,
  retractAlert,
  verifyEvent,
} from './api'

afterEach(() => {
  clearCsrfToken()
  document.cookie = 'ncu_csrf=; max-age=0; path=/'
  vi.unstubAllGlobals()
})

describe('admin api', () => {
  it('登入後的寫入請求帶上回應中的 CSRF token 與 JSON body', async () => {
    const { calls } = mockApi({
      'POST /api/v1/auth/login': {
        data: { username: 'jamie', csrf_token: 'tok-1', expires_at: '2026-10-22T00:00:00Z' },
      },
      'POST /api/v1/admin/events/e1/verify': { data: { id: 'e1', status: 'verified' } },
    })
    await login('jamie', 'pw')
    await verifyEvent('e1', 'verified', '  已查證  ')

    expect(calls[0].body).toEqual({ username: 'jamie', password: 'pw' })
    const verify = calls[1]
    expect(verify.headers.get('X-CSRF-Token')).toBe('tok-1')
    expect(verify.headers.get('Content-Type')).toBe('application/json')
    expect(verify.body).toEqual({ status: 'verified', note: '已查證' })
  })

  it('重新整理後從 ncu_csrf cookie 取得 CSRF token；空白備註不送出', async () => {
    document.cookie = 'ncu_csrf=from-cookie; path=/'
    const { calls } = mockApi({
      'POST /api/v1/admin/alerts/a1/retract': { data: { id: 'a1', state: 'retracted' } },
      'POST /api/v1/admin/events/e1/verify': { data: { id: 'e1', status: 'monitoring' } },
    })
    await retractAlert('a1', '內容有誤')
    await verifyEvent('e1', 'monitoring', '   ')
    expect(calls[0].headers.get('X-CSRF-Token')).toBe('from-cookie')
    expect(calls[0].body).toEqual({ reason: '內容有誤' })
    expect(calls[1].body).toEqual({ status: 'monitoring' })
  })

  it('列表帶篩選與 cursor，並回傳下一頁 cursor', async () => {
    const { calls } = mockApi({
      'GET /api/v1/admin/events': { data: [], meta: { next_cursor: 'c2', limit: 50 } },
    })
    const page = await listEvents({ status: 'unverified', cursor: 'c1' })
    expect(calls[0].path).toBe('/api/v1/admin/events?status=unverified&cursor=c1')
    expect(page).toEqual({ items: [], nextCursor: 'c2' })
  })

  it('路徑參數經過編碼，錯誤轉成 ApiError', async () => {
    mockApi({})
    await expect(getEvent('a/b')).rejects.toMatchObject({ code: 'NOT_FOUND', status: 404 })
  })
})
