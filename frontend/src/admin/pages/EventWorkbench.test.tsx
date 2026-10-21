import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { EVENT_DETAIL, EVENT_ID, EVENTS } from '../../test/fixtures'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'

const DETAIL = `GET /api/v1/admin/events/${EVENT_ID}`

function open(path: string, routes: Record<string, MockRoute | ((c: never) => MockRoute)>) {
  window.history.pushState({}, '', path)
  const api = mockApi({ 'GET /api/v1/auth/me': ME, ...routes })
  render(<App />)
  return { ...api, user: userEvent.setup() }
}

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('EventWorkbench', () => {
  it('列出事件：狀態、風險等級（文字＋分數）、證據數，時間以台北時間顯示', async () => {
    open('/admin/events', { 'GET /api/v1/admin/events': { data: EVENTS } })
    const row = (await screen.findByText('工程一館停電')).closest('li')!
    expect(within(row).getByText('未查證')).toBeInTheDocument()
    expect(within(row).getByText(/高 · 64\.5/)).toBeInTheDocument()
    expect(within(row).getByText(/證據 2/)).toBeInTheDocument()
    expect(within(row).getByText(/2026-10-20 10:10/)).toBeInTheDocument()
    const other = screen.getByText('圖書館網路維護').closest('li')!
    expect(within(other).getByText('未評估')).toBeInTheDocument()
  })

  it('依狀態篩選，並以 cursor 載入下一頁', async () => {
    const { calls, user } = open('/admin/events', {
      'GET /api/v1/admin/events': { data: [EVENTS[0]], meta: { next_cursor: 'c2' } },
      'GET /api/v1/admin/events?cursor=c2': { data: [EVENTS[1]], meta: { next_cursor: null } },
      'GET /api/v1/admin/events?status=verified': { data: [EVENTS[1]] },
    })
    await user.click(await screen.findByRole('button', { name: '載入更多' }))
    expect(await screen.findByText('圖書館網路維護')).toBeInTheDocument()
    expect(screen.getByText('工程一館停電')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '載入更多' })).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('狀態'), 'verified')
    await waitFor(() => expect(screen.queryByText('工程一館停電')).not.toBeInTheDocument())
    expect(calls.map((c) => c.path)).toContain('/api/v1/admin/events?status=verified')
  })

  it('事件詳細：來源對照、風險分項與歷史；外部連結不帶 referrer，摘錄不當作 HTML', async () => {
    const { user } = open('/admin/events', {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: { data: EVENT_DETAIL },
    })
    await user.click(await screen.findByRole('link', { name: /工程一館停電/ }))
    expect(window.location.pathname).toBe(`/admin/events/${EVENT_ID}`)
    expect(await screen.findByRole('heading', { level: 2, name: '工程一館停電' })).toBeInTheDocument()

    const sources = screen.getByRole('region', { name: '來源對照' })
    expect(within(sources).getByText('ncu_announcements')).toBeInTheDocument()
    expect(within(sources).getByText('支持')).toBeInTheDocument()
    expect(within(sources).getByText('反駁')).toBeInTheDocument()
    expect(within(sources).getByText('<b>聽說</b>停電延到明天')).toBeInTheDocument()
    const link = within(sources).getAllByRole('link', { name: /開啟原文/ })[0]
    expect(link).toHaveAttribute('href', 'https://www.ncu.edu.tw/announcements/1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')

    const risk = screen.getByRole('region', { name: '風險分項' })
    expect(within(risk).getByText('高')).toBeInTheDocument()
    expect(within(risk).getByText('64.5')).toBeInTheDocument()
    expect(within(risk).getByText('營運')).toBeInTheDocument()
    expect(within(risk).getByText('85')).toBeInTheDocument()
    expect(within(risk).getByRole('meter', { name: '營運' })).toHaveAttribute('aria-valuenow', '85')
    expect(within(risk).getByText(/floor/)).toBeInTheDocument()
    expect(within(risk).getByText(/2026-10-21\.1/)).toBeInTheDocument()
    expect(within(risk).getAllByRole('listitem', { name: /評估/ })).toHaveLength(2)
  })

  it('記錄查證結果後更新狀態並送出備註', async () => {
    const { calls, user } = open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: { data: EVENT_DETAIL },
      [`POST /api/v1/admin/events/${EVENT_ID}/verify`]: {
        data: { id: EVENT_ID, status: 'verified', previous: 'unverified' },
      },
    })
    const form = await screen.findByRole('form', { name: '記錄查證結果' })
    await user.selectOptions(within(form).getByLabelText('查證結果'), 'verified')
    await user.type(within(form).getByLabelText('備註（選填）'), '已比對公告')
    await user.click(within(form).getByRole('button', { name: '儲存' }))

    expect(await screen.findByRole('status')).toHaveTextContent('已更新為「已查證」')
    const post = calls.find((c) => c.method === 'POST')!
    expect(post.body).toEqual({ status: 'verified', note: '已比對公告' })
  })

  it('不合法的狀態轉移顯示伺服器訊息', async () => {
    const { user } = open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: { data: EVENT_DETAIL },
      [`POST /api/v1/admin/events/${EVENT_ID}/verify`]: {
        status: 409,
        error: { code: 'CONFLICT', message: '事件狀態不能從 unverified 改為 archived' },
      },
    })
    const form = await screen.findByRole('form', { name: '記錄查證結果' })
    await user.selectOptions(within(form).getByLabelText('查證結果'), 'archived')
    await user.click(within(form).getByRole('button', { name: '儲存' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('不能從 unverified 改為 archived')
  })

  it('重新分析排入佇列', async () => {
    const { calls, user } = open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: { data: EVENT_DETAIL },
      [`POST /api/v1/admin/events/${EVENT_ID}/analyze`]: {
        status: 202,
        data: { event_id: EVENT_ID, status: 'queued' },
      },
    })
    await user.click(await screen.findByRole('button', { name: '重新分析' }))
    expect(await screen.findByRole('status')).toHaveTextContent('已排入分析佇列')
    expect(calls.some((c) => c.path.endsWith('/analyze'))).toBe(true)
  })

  it('事件不存在時顯示錯誤', async () => {
    open('/admin/events/missing', { 'GET /api/v1/admin/events': { data: EVENTS } })
    expect(await screen.findByText('找不到')).toBeInTheDocument()
  })
})
