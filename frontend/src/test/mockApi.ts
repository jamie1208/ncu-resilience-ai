import { vi } from 'vitest'

/** 一個假路由的回應：預設 200，body 為 `data`（自動包成 `{data, meta, error}`）。 */
export interface MockRoute {
  status?: number
  data?: unknown
  meta?: Record<string, unknown>
  error?: { code: string; message: string }
}

export interface MockCall {
  method: string
  path: string
  headers: Headers
  body: unknown
}

type Handler = MockRoute | ((call: MockCall) => MockRoute)

/**
 * 以 `"METHOD /path?query"` 為鍵替換全域 fetch（找不到完整鍵時用不含 query 的鍵）。
 * 沒有定義的路由回 404，並記錄所有呼叫供斷言。
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: MockCall[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'https://admin.test')
    const method = (init.method ?? 'GET').toUpperCase()
    const call: MockCall = {
      method,
      path: url.pathname + url.search,
      headers: new Headers(init.headers),
      body: typeof init.body === 'string' ? JSON.parse(init.body) : null,
    }
    calls.push(call)
    const handler = routes[`${method} ${call.path}`] ?? routes[`${method} ${url.pathname}`]
    const route: MockRoute = handler
      ? typeof handler === 'function'
        ? handler(call)
        : handler
      : { status: 404, error: { code: 'NOT_FOUND', message: '找不到' } }
    const body = {
      data: route.error ? null : (route.data ?? null),
      meta: { request_id: 'req-test', timestamp: '2026-10-21T00:00:00Z', ...route.meta },
      error: route.error ?? null,
    }
    return new Response(JSON.stringify(body), {
      status: route.status ?? (route.error ? 400 : 200),
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

export const ME = { status: 200, data: { username: 'jamie', role: 'admin' } }
export const ANONYMOUS = { status: 401, error: { code: 'UNAUTHORIZED', message: '需要登入' } }
