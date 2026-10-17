import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiGet } from './client'

const meta = { request_id: 'req-12345678', timestamp: '2026-10-17T03:53:32+00:00' }

function mockFetch(response: Response | Error) {
  const fn = vi.fn(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
  )
  vi.stubGlobal('fetch', fn)
  return fn
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiGet', () => {
  it('成功時回傳 data 與 meta', async () => {
    const fetchFn = mockFetch(json({ data: { status: 'ok' }, meta, error: null }))
    const res = await apiGet<{ status: string }>('/api/v1/health')
    expect(res.data.status).toBe('ok')
    expect(res.meta.request_id).toBe('req-12345678')
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/v1/health')
    expect(new Headers(init.headers).get('Accept')).toBe('application/json')
    expect(init.credentials).toBe('same-origin')
  })

  it('錯誤 envelope 轉成 ApiError，保留錯誤碼、狀態碼與 request_id', async () => {
    mockFetch(
      json({ data: null, meta, error: { code: 'NOT_FOUND', message: '找不到事件' } }, 404),
    )
    const err = await apiGet('/api/v1/events/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({
      code: 'NOT_FOUND',
      status: 404,
      message: '找不到事件',
      requestId: 'req-12345678',
    })
  })

  it('非 envelope 的錯誤依 HTTP 狀態碼對應錯誤碼', async () => {
    mockFetch(json({ detail: 'Unprocessable' }, 422))
    await expect(apiGet('/api/v1/x')).rejects.toMatchObject({
      code: 'VALIDATION_ERROR',
      status: 422,
    })
  })

  it('5xx 且無 envelope 時為 SERVER_ERROR', async () => {
    mockFetch(new Response('Bad Gateway', { status: 502 }))
    await expect(apiGet('/api/v1/x')).rejects.toMatchObject({ code: 'SERVER_ERROR', status: 502 })
  })

  it('2xx 但不是 envelope 時為 INVALID_RESPONSE', async () => {
    mockFetch(json({ status: 'ok' }))
    await expect(apiGet('/api/v1/x')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('網路錯誤為 NETWORK_ERROR', async () => {
    mockFetch(new TypeError('Failed to fetch'))
    await expect(apiGet('/api/v1/x')).rejects.toMatchObject({ code: 'NETWORK_ERROR', status: 0 })
  })
})
