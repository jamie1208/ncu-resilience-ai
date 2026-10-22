import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { ANONYMOUS, ME, mockApi } from './test/mockApi'

function at(path: string) {
  window.history.pushState({}, '', path)
}

afterEach(() => {
  at('/')
  vi.unstubAllGlobals()
})

// P3-T05：系統只有管理前端（ADR-0008），根路徑是登入頁，沒有公開網站
describe('App', () => {
  it('根路徑是登入頁，清楚顯示非官方免責聲明與 110／119 提示，沒有公開網站連結', async () => {
    mockApi({ 'GET /api/v1/auth/me': ANONYMOUS })
    at('/')
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: /登入/ })).toBeInTheDocument()
    expect(screen.getByText(/非校方官方系統/)).toBeVisible()
    expect(screen.getByText(/110/)).toBeVisible()
    expect(screen.getByText(/119/)).toBeVisible()
    expect(screen.queryByText(/公開網站/)).not.toBeInTheDocument()
  })

  it('未登入進入 /admin/ 子路徑時顯示登入頁，登入後留在原頁面', async () => {
    const { calls } = mockApi({
      'GET /api/v1/auth/me': ANONYMOUS,
      'POST /api/v1/auth/login': {
        data: { username: 'jamie', csrf_token: 'tok', expires_at: '2026-10-22T00:00:00Z' },
      },
      'GET /api/v1/admin/skills': { data: [] },
    })
    at('/admin/skills')
    render(<App />)
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('帳號'), 'jamie')
    await user.type(screen.getByLabelText('密碼'), 'secret')
    await user.click(screen.getByRole('button', { name: '登入' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Skills' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/admin/skills')
    expect(calls.some((c) => c.path === '/api/v1/admin/skills')).toBe(true)
  })

  it('帳密錯誤時顯示錯誤訊息且不清空帳號', async () => {
    mockApi({
      'GET /api/v1/auth/me': ANONYMOUS,
      'POST /api/v1/auth/login': {
        status: 401,
        error: { code: 'UNAUTHORIZED', message: '帳號或密碼錯誤' },
      },
    })
    render(<App />)
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('帳號'), 'jamie')
    await user.type(screen.getByLabelText('密碼'), 'wrong')
    await user.click(screen.getByRole('button', { name: '登入' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('帳號或密碼錯誤')
    expect(screen.getByLabelText('帳號')).toHaveValue('jamie')
  })

  it('已登入時根路徑導向事件工作台', async () => {
    mockApi({ 'GET /api/v1/auth/me': ME, 'GET /api/v1/admin/events': { data: [] } })
    at('/')
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: '事件工作台' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/admin/events')
    expect(screen.getByText('jamie')).toBeInTheDocument()
  })

  it('導覽列切換頁面並標示目前頁面', async () => {
    mockApi({
      'GET /api/v1/auth/me': ME,
      'GET /api/v1/admin/events': { data: [] },
      'GET /api/v1/admin/alerts': { data: [] },
    })
    at('/admin/events')
    render(<App />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('link', { name: '通知中心' }))
    expect(await screen.findByRole('heading', { level: 1, name: '通知中心' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/admin/alerts')
    expect(screen.getByRole('link', { name: '通知中心' })).toHaveAttribute('aria-current', 'page')
  })

  it('登出後回到登入頁', async () => {
    const { calls } = mockApi({
      'GET /api/v1/auth/me': ME,
      'GET /api/v1/admin/events': { data: [] },
      'POST /api/v1/auth/logout': { data: { logged_out: true } },
    })
    at('/admin/events')
    render(<App />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '登出' }))
    expect(await screen.findByRole('heading', { level: 1, name: /登入/ })).toBeInTheDocument()
    expect(calls.at(-1)?.method).toBe('POST')
  })

  it('操作中 session 失效（401）時回到登入頁', async () => {
    mockApi({
      'GET /api/v1/auth/me': ME,
      'GET /api/v1/admin/events': ANONYMOUS,
    })
    at('/admin/events')
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: /登入/ })).toBeInTheDocument()
  })

  it('不存在的路徑顯示找不到頁面', async () => {
    mockApi({ 'GET /api/v1/auth/me': ME })
    at('/admin/nope')
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: '找不到頁面' })).toBeInTheDocument()
    await waitFor(() => expect(window.location.pathname).toBe('/admin/nope'))
  })
})
