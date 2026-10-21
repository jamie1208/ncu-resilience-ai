import type { Page, Request } from '@playwright/test'

import {
  ALERTS,
  AUDIT_LOGS,
  DASHBOARD,
  EVENT_DETAIL,
  EVENTS,
  INGESTION_RUNS,
  SKILLS,
  SOURCE_HEALTH,
} from '../src/test/fixtures'

const CSRF = 'csrf-e2e'

export interface Recorded {
  method: string
  path: string
  csrf: string | null
  body: unknown
}

/**
 * 以 page.route 模擬後端：登入前 /auth/me 回 401；登入後所有管理 API 可用。
 * 寫入請求沒有正確 CSRF token 時回 403（與後端行為一致）。
 */
export async function mockBackend(page: Page) {
  const state = { signedIn: false, eventStatus: EVENT_DETAIL.status, alertState: ALERTS[0].state }
  const calls: Recorded[] = []

  await page.route('**/api/v1/**', async (route) => {
    const req: Request = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1', '')
    const method = req.method()
    const body = req.postData() ? JSON.parse(req.postData()!) : null
    calls.push({ method, path: url.pathname + url.search, csrf: req.headers()['x-csrf-token'] ?? null, body })

    const reply = (
      status: number,
      data: unknown,
      error: { code: string; message: string } | null = null,
      headers: Record<string, string> = {},
    ) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers,
        body: JSON.stringify({
          data: error ? null : data,
          meta: { request_id: 'req-e2e', timestamp: new Date().toISOString(), next_cursor: null },
          error,
        }),
      })

    if (method === 'POST' && path === '/auth/login') {
      if (body?.password !== 'correct horse') {
        return reply(401, null, { code: 'UNAUTHORIZED', message: '帳號或密碼錯誤' })
      }
      state.signedIn = true
      // 與後端相同：CSRF cookie 不是 HttpOnly，重新整理後前端從這裡取回 token
      return reply(
        200,
        { username: body.username, csrf_token: CSRF, expires_at: '2026-10-22T00:00:00Z' },
        null,
        { 'Set-Cookie': `ncu_csrf=${CSRF}; Path=/; SameSite=Strict` },
      )
    }
    if (!state.signedIn) return reply(401, null, { code: 'UNAUTHORIZED', message: '需要登入' })
    if (method === 'POST' && req.headers()['x-csrf-token'] !== CSRF) {
      return reply(403, null, { code: 'FORBIDDEN', message: 'CSRF 驗證失敗' })
    }

    if (method === 'GET' && path === '/auth/me') return reply(200, { username: 'jamie', role: 'admin' })
    if (method === 'POST' && path === '/auth/logout') {
      state.signedIn = false
      return reply(200, { logged_out: true })
    }
    if (method === 'GET' && path === '/admin/events') {
      return reply(200, EVENTS.map((e) => (e.id === EVENT_DETAIL.id ? { ...e, status: state.eventStatus } : e)))
    }
    if (method === 'GET' && path === `/admin/events/${EVENT_DETAIL.id}`) {
      return reply(200, { ...EVENT_DETAIL, status: state.eventStatus })
    }
    if (method === 'POST' && path === `/admin/events/${EVENT_DETAIL.id}/verify`) {
      const previous = state.eventStatus
      state.eventStatus = body.status
      return reply(200, { id: EVENT_DETAIL.id, status: body.status, previous })
    }
    if (method === 'GET' && path === '/admin/alerts') {
      return reply(200, ALERTS.map((a, i) => (i === 0 ? { ...a, state: state.alertState } : a)))
    }
    if (method === 'POST' && path === `/admin/alerts/${ALERTS[0].id}/retract`) {
      state.alertState = 'retracted'
      return reply(200, { id: ALERTS[0].id, state: 'retracted', previous: 'published' })
    }
    if (method === 'GET' && path === '/admin/skills') return reply(200, SKILLS)
    if (method === 'GET' && path === '/admin/dashboard') return reply(200, DASHBOARD)
    if (method === 'GET' && path === '/admin/ingestion-runs/health') return reply(200, SOURCE_HEALTH)
    if (method === 'GET' && path === '/admin/ingestion-runs') return reply(200, INGESTION_RUNS)
    if (method === 'GET' && path === '/admin/audit-logs') return reply(200, AUDIT_LOGS)
    return reply(404, null, { code: 'NOT_FOUND', message: '找不到' })
  })

  return { calls }
}
