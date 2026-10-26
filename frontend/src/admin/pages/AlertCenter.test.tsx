import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { ALERTS } from '../../test/fixtures'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'

function open(routes: Record<string, MockRoute>) {
  window.history.pushState({}, '', '/admin/alerts')
  const api = mockApi({ 'GET /api/v1/auth/me': ME, ...routes })
  render(<App />)
  return { ...api, user: userEvent.setup() }
}

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('AlertCenter', () => {
  it('列出通知：事件、狀態、版本、內容、撤回原因，並連到事件', async () => {
    open({ 'GET /api/v1/admin/alerts': { data: ALERTS } })
    const first = (await screen.findByText('工程一館停電施工至 12 時，請留意。')).closest('li')!
    expect(within(first).getByText('已通知')).toBeInTheDocument()
    expect(within(first).getByText('v2')).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: '工程一館停電' })).toHaveAttribute(
      'href',
      `/admin/events/${ALERTS[0].event_id}`,
    )
    const second = screen.getByText('圖書館網路維護。').closest('li')!
    expect(within(second).getByText('已撤回')).toBeInTheDocument()
    expect(within(second).getByText(/資訊已過時/)).toBeInTheDocument()
    // 只有已通知、已更新的通知可以撤回
    expect(within(second).queryByRole('button', { name: '撤回' })).not.toBeInTheDocument()
  })

  it('疑似指控的通知標記給管理端人工判斷（附錄 A05）', async () => {
    const flagged = { ...ALERTS[0], id: 'al-3', flags: ['possible_allegation'], body: '近期校內有治安相關訊息。' }
    open({ 'GET /api/v1/admin/alerts': { data: [flagged, ALERTS[1]] } })
    const row = (await screen.findByText('近期校內有治安相關訊息。')).closest('li')!
    expect(within(row).getByText('疑似指控，需人工判斷')).toBeInTheDocument()
    const other = screen.getByText('圖書館網路維護。').closest('li')!
    expect(within(other).queryByText('疑似指控，需人工判斷')).not.toBeInTheDocument()
  })

  it('依狀態篩選', async () => {
    const { calls, user } = open({
      'GET /api/v1/admin/alerts': { data: ALERTS },
      'GET /api/v1/admin/alerts?state=retracted': { data: [ALERTS[1]] },
    })
    await user.selectOptions(await screen.findByLabelText('狀態'), 'retracted')
    expect(await screen.findByText('圖書館網路維護。')).toBeInTheDocument()
    expect(calls.map((c) => c.path)).toContain('/api/v1/admin/alerts?state=retracted')
  })

  it('撤回需要原因，成功後更新狀態', async () => {
    const { calls, user } = open({
      'GET /api/v1/admin/alerts': { data: ALERTS },
      'POST /api/v1/admin/alerts/al-1/retract': {
        data: { id: 'al-1', state: 'retracted', previous: 'published' },
      },
    })
    const first = (await screen.findByText('工程一館停電施工至 12 時，請留意。')).closest('li')!
    await user.click(within(first).getByRole('button', { name: '撤回' }))
    const form = within(first).getByRole('form', { name: '撤回通知' })
    expect(within(form).getByRole('button', { name: '確認撤回' })).toBeDisabled()
    await user.type(within(form).getByLabelText('撤回原因'), '停電已取消')
    await user.click(within(form).getByRole('button', { name: '確認撤回' }))

    expect(await within(first).findByText('已撤回')).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ reason: '停電已取消' })
  })

  it('撤回原因含個資時顯示伺服器訊息', async () => {
    const { user } = open({
      'GET /api/v1/admin/alerts': { data: ALERTS },
      'POST /api/v1/admin/alerts/al-1/retract': {
        status: 400,
        error: { code: 'VALIDATION_ERROR', message: '撤回原因不得含個資' },
      },
    })
    const first = (await screen.findByText('工程一館停電施工至 12 時，請留意。')).closest('li')!
    await user.click(within(first).getByRole('button', { name: '撤回' }))
    await user.type(within(first).getByLabelText('撤回原因'), '0912345678')
    await user.click(within(first).getByRole('button', { name: '確認撤回' }))
    expect(await within(first).findByRole('alert')).toHaveTextContent('撤回原因不得含個資')
  })

  it('沒有通知時顯示空狀態', async () => {
    open({ 'GET /api/v1/admin/alerts': { data: [] } })
    expect(await screen.findByText('目前沒有通知')).toBeInTheDocument()
  })
})
