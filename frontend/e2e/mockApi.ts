import type { Page, Request } from '@playwright/test'

import type { EventReport, RelationEdge } from '../src/admin/api'
import {
  ALERTS,
  AUDIT_LOGS,
  DASHBOARD,
  EVENT_DETAIL,
  EVENTS,
  INGESTION_RUNS,
  RECURRENCES,
  RELATION_GRAPH,
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
export async function mockBackend(page: Page, options: { report?: EventReport | null } = {}) {
  const state = {
    signedIn: false,
    eventStatus: EVENT_DETAIL.status,
    alertState: ALERTS[0].state,
    report: options.report ?? null,
    relations: RELATION_GRAPH.edges.map((e) => ({ ...e })) as RelationEdge[],
  }
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
      return reply(200, { ...EVENT_DETAIL, status: state.eventStatus, report: state.report })
    }
    if (method === 'POST' && path === `/admin/events/${EVENT_DETAIL.id}/report`) {
      return reply(202, { event_id: EVENT_DETAIL.id, status: 'queued' })
    }
    if (method === 'POST' && path === `/admin/events/${EVENT_DETAIL.id}/verify`) {
      const previous = state.eventStatus
      state.eventStatus = body.status
      // 與後端相同：事件改為不實時小報告失效
      if (body.status === 'false' && state.report) {
        state.report = { ...state.report, valid: false, invalidated_reason: 'event_false' }
      }
      return reply(200, { id: EVENT_DETAIL.id, status: body.status, previous })
    }
    if (method === 'GET' && path === '/admin/alerts') {
      return reply(200, ALERTS.map((a, i) => (i === 0 ? { ...a, state: state.alertState } : a)))
    }
    if (method === 'POST' && path === `/admin/alerts/${ALERTS[0].id}/retract`) {
      state.alertState = 'retracted'
      return reply(200, { id: ALERTS[0].id, state: 'retracted', previous: 'published' })
    }
    if (method === 'GET' && path === '/admin/relations') {
      // 與後端相同：預設不含駁回；type、decision 篩選
      const type = url.searchParams.get('type')
      const decision = url.searchParams.get('decision')
      const withRejected = url.searchParams.get('include_rejected') === 'true'
      const edges = state.relations.filter(
        (e) =>
          (!type || e.type === type) &&
          (decision ? e.decision === decision : withRejected || e.decision !== 'rejected'),
      )
      const ids = new Set(edges.flatMap((e) => [e.source, e.target]))
      return reply(200, { nodes: RELATION_GRAPH.nodes.filter((n) => ids.has(n.id)), edges })
    }
    const decide = /^\/admin\/relations\/([^/]+)\/decision$/.exec(path)
    if (method === 'POST' && decide) {
      const edge = state.relations.find((e) => e.id === decide[1])
      if (!edge) return reply(404, null, { code: 'NOT_FOUND', message: '找不到關聯' })
      // 與後端相同：只能 pending → confirmed／rejected，已決定的只能回滾
      if ((edge.decision === 'pending') === (body.decision === 'pending')) {
        return reply(409, null, { code: 'CONFLICT', message: '關聯已被決定，請先回滾' })
      }
      const pending = body.decision === 'pending'
      Object.assign(edge, {
        decision: body.decision,
        decided_by: pending ? null : 'admin:u-1',
        decided_at: pending ? null : new Date().toISOString(),
        decision_note: pending ? null : (body.note ?? null),
      })
      const nodes = RELATION_GRAPH.nodes.filter((n) => n.id === edge.source || n.id === edge.target)
      return reply(200, { edge, nodes })
    }
    if (method === 'GET' && path === '/admin/recurrences') return reply(200, RECURRENCES)
    if (method === 'GET' && path === '/admin/skills') return reply(200, SKILLS)
    if (method === 'GET' && path === '/admin/dashboard') return reply(200, DASHBOARD)
    if (method === 'GET' && path === '/admin/ingestion-runs/health') return reply(200, SOURCE_HEALTH)
    if (method === 'GET' && path === '/admin/ingestion-runs') return reply(200, INGESTION_RUNS)
    if (method === 'GET' && path === '/admin/audit-logs') return reply(200, AUDIT_LOGS)
    return reply(404, null, { code: 'NOT_FOUND', message: '找不到' })
  })

  return { calls }
}
