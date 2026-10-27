import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { EVENT_DETAIL, EVENT_ID, EVENT_REPORT, EVENTS } from '../../test/fixtures'
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

  it('事件說明：顯示 AI 摘要與出處來源，摘要只當純文字', async () => {
    const summary = {
      text: '工程一館 10/20 上午停電，<b>電梯</b>暫停。',
      evidence_ids: ['si1hex'],
      source_item_id: 'si-1',
      ai_run_id: 'run-1',
      updated_at: '2026-10-20T02:20:00Z',
    }
    open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: { data: { ...EVENT_DETAIL, summary } },
    })
    const region = await screen.findByRole('region', { name: '事件說明' })
    expect(within(region).getByText('工程一館 10/20 上午停電，<b>電梯</b>暫停。')).toBeInTheDocument()
    expect(within(region).getByText(/AI 依來源整理/)).toBeInTheDocument()
    expect(within(region).getByText(/ncu_announcements/)).toBeInTheDocument()
    expect(within(region).getByText(/2026-10-20 10:20/)).toBeInTheDocument()
  })

  it('沒有事件說明時顯示尚未產生', async () => {
    open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: { data: EVENT_DETAIL },
    })
    const region = await screen.findByRole('region', { name: '事件說明' })
    expect(within(region).getByText('尚未產生事件說明。')).toBeInTheDocument()
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

  it('事件小報告：影響、推測與已確認原因分開、措施依對象與時程分組、建議單位只供參考', async () => {
    open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: { data: { ...EVENT_DETAIL, report: EVENT_REPORT } },
    })
    const report = await screen.findByRole('region', { name: '事件小報告' })

    const impacts = within(report).getByRole('region', { name: '可能的影響' })
    expect(within(impacts).getByText('營運')).toBeInTheDocument()
    expect(within(impacts).getByText('停電影響上課與實驗。')).toBeInTheDocument()

    const hypotheses = within(report).getByRole('region', { name: '值得查查看' })
    expect(within(hypotheses).getByText('疑似附近施工挖到電纜。')).toBeInTheDocument()
    expect(within(hypotheses).getByText('推測，尚未查證')).toBeInTheDocument()
    expect(within(hypotheses).getByText(/向總務處確認施工是否影響供電/)).toBeInTheDocument()
    expect(within(hypotheses).queryByText('變電箱老舊跳脫。')).not.toBeInTheDocument()

    const confirmed = within(report).getByRole('region', { name: '已確認原因' })
    expect(within(confirmed).getByText('變電箱老舊跳脫。')).toBeInTheDocument()
    expect(within(confirmed).getByText('停電原因為變電箱老舊跳脫')).toBeInTheDocument()
    expect(within(confirmed).getByText(/ncu_announcements/)).toBeInTheDocument()
    expect(within(confirmed).queryByText('推測，尚未查證')).not.toBeInTheDocument()

    const measures = within(report).getByRole('region', { name: '可以怎麼做' })
    const students = within(measures).getByRole('group', { name: '同學可以做' })
    expect(within(students).getByText('停電期間改到其他大樓自習。')).toBeInTheDocument()
    const school = within(measures).getByRole('group', { name: '學校可以做' })
    expect(within(within(school).getByRole('list', { name: '中期' })).getByText(/盤點同年份的變電箱/)).toBeInTheDocument()
    const check = within(within(school).getByRole('list', { name: '短期' })).getByText(/確認附近施工/).closest('li')!
    expect(within(check).getByText('針對推測原因')).toBeInTheDocument()

    const referrals = within(report).getByRole('region', { name: '建議轉給哪個單位' })
    expect(within(referrals).getByText('總務處')).toBeInTheDocument()
    expect(within(referrals).getByText(/系統不會寄信/)).toBeInTheDocument()
  })

  it('事件小報告：S07／S08 abstain 時顯示證據不足，不顯示任何項目', async () => {
    const abstained = { status: 'abstained' as const, message: '證據不足，暫不分析', items: [] }
    open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: {
        data: {
          ...EVENT_DETAIL,
          report: { ...EVENT_REPORT, causes: abstained, measures: abstained, referrals: [] },
        },
      },
    })
    const report = await screen.findByRole('region', { name: '事件小報告' })
    expect(within(report).getAllByText('證據不足，暫不分析')).toHaveLength(2)
    expect(within(report).queryByRole('region', { name: '值得查查看' })).not.toBeInTheDocument()
    expect(within(report).queryByRole('region', { name: '建議轉給哪個單位' })).not.toBeInTheDocument()
  })

  it('事件小報告：事件被查證為不實時標示報告已失效', async () => {
    open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: {
        data: {
          ...EVENT_DETAIL,
          status: 'false',
          report: { ...EVENT_REPORT, valid: false, invalidated_at: '2026-10-21T00:00:00Z', invalidated_reason: 'event_false' },
        },
      },
    })
    const report = await screen.findByRole('region', { name: '事件小報告' })
    expect(within(report).getByText(/事件已被查證為不實，這份報告已失效/)).toBeInTheDocument()
    expect(within(report).queryByRole('button', { name: '產生小報告' })).not.toBeInTheDocument()
  })

  it('沒有小報告時可以手動產生', async () => {
    const { calls, user } = open(`/admin/events/${EVENT_ID}`, {
      'GET /api/v1/admin/events': { data: EVENTS },
      [DETAIL]: { data: EVENT_DETAIL },
      [`POST /api/v1/admin/events/${EVENT_ID}/report`]: {
        status: 202,
        data: { event_id: EVENT_ID, status: 'queued' },
      },
    })
    const report = await screen.findByRole('region', { name: '事件小報告' })
    expect(within(report).getByText('尚未產生事件小報告。')).toBeInTheDocument()
    await user.click(within(report).getByRole('button', { name: '產生小報告' }))
    expect(await within(report).findByRole('status')).toHaveTextContent('已排入產生小報告')
    expect(calls.some((c) => c.method === 'POST' && c.path.endsWith('/report'))).toBe(true)
  })

  it('事件不存在時顯示錯誤', async () => {
    open('/admin/events/missing', { 'GET /api/v1/admin/events': { data: EVENTS } })
    expect(await screen.findByText('找不到')).toBeInTheDocument()
  })
})
