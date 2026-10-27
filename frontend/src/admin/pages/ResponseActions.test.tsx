import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { ACTION_DETAIL, ACTIONS, EVENT_ID } from '../../test/fixtures'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'

function open(routes: Record<string, MockRoute>) {
  window.history.pushState({}, '', '/admin/actions')
  const api = mockApi({ 'GET /api/v1/auth/me': ME, ...routes })
  render(<App />)
  return { ...api, user: userEvent.setup() }
}

async function row(title: string) {
  return (await screen.findByText(title)).closest('li')!
}

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('ResponseActions', () => {
  it('列出改善措施：狀態、逾期、負責人、期限、所屬事件、推測原因標示與佐證', async () => {
    open({ 'GET /api/v1/admin/actions': { data: ACTIONS } })
    const first = await row('盤點同年份的變電箱並排定汰換。')
    expect(screen.getByRole('link', { name: '改善追蹤' })).toHaveAttribute('aria-current', 'page')
    expect(within(first).getByText('處理中')).toBeInTheDocument()
    expect(within(first).getByText('已逾期')).toBeInTheDocument()
    expect(within(first).getByText('負責：總務處')).toBeInTheDocument()
    expect(within(first).getByText('期限：2026-10-25')).toBeInTheDocument()
    expect(within(first).getByText('來自事件小報告')).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: '工程一館停電' })).toHaveAttribute(
      'href',
      `/admin/events/${EVENT_ID}`,
    )

    const second = await row('確認附近施工是否曾影響供電線路。')
    expect(within(second).getByText('已恢復')).toBeInTheDocument()
    expect(within(second).getByText('針對推測原因')).toBeInTheDocument()
    expect(within(second).getByText('負責：未指定')).toBeInTheDocument()
    expect(within(second).getByText('佐證：總務處公告：已恢復供電')).toBeInTheDocument()
    expect(within(second).queryByText('已逾期')).not.toBeInTheDocument()
  })

  it('依狀態篩選', async () => {
    const { calls, user } = open({
      'GET /api/v1/admin/actions': { data: ACTIONS },
      'GET /api/v1/admin/actions?state=recovered': { data: [ACTIONS[1]] },
    })
    await user.selectOptions(await screen.findByLabelText('狀態'), 'recovered')
    expect(calls.map((c) => c.path)).toContain('/api/v1/admin/actions?state=recovered')
    expect(screen.queryByText('盤點同年份的變電箱並排定汰換。')).not.toBeInTheDocument()
  })

  it('只顯示合法的下一步；恢復需要佐證，送出後更新狀態', async () => {
    const recovered = { ...ACTION_DETAIL, state: 'recovered' as const, proof: '已換新變電箱', overdue: false }
    const { calls, user } = open({
      'GET /api/v1/admin/actions': { data: ACTIONS },
      'POST /api/v1/admin/actions/ra-1/transitions': { data: recovered },
    })
    const first = await row('盤點同年份的變電箱並排定汰換。')
    expect(within(first).queryByRole('button', { name: '改為已改進' })).not.toBeInTheDocument()
    await user.click(within(first).getByRole('button', { name: '改為已恢復' }))

    const form = within(first).getByRole('form', { name: '變更狀態' })
    const submit = within(form).getByRole('button', { name: '確認' })
    expect(submit).toBeDisabled()
    await user.type(within(form).getByLabelText('佐證'), '已換新變電箱')
    await user.click(submit)

    expect(await within(first).findByText('已恢復')).toBeInTheDocument()
    expect(within(first).getByText('佐證：已換新變電箱')).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ to: 'recovered', proof: '已換新變電箱' })
  })

  it('轉移被拒時顯示伺服器訊息', async () => {
    const { user } = open({
      'GET /api/v1/admin/actions': { data: ACTIONS },
      'POST /api/v1/admin/actions/ra-1/transitions': {
        status: 422,
        error: { code: 'VALIDATION_ERROR', message: '疑似含個資' },
      },
    })
    const first = await row('盤點同年份的變電箱並排定汰換。')
    await user.click(within(first).getByRole('button', { name: '改為不追蹤' }))
    await user.type(within(first).getByLabelText('備註'), '0912345678')
    await user.click(within(first).getByRole('button', { name: '確認' }))
    expect(await within(first).findByRole('alert')).toHaveTextContent('疑似含個資')
  })

  it('查看狀態變更歷史', async () => {
    const { user } = open({
      'GET /api/v1/admin/actions': { data: ACTIONS },
      'GET /api/v1/admin/actions/ra-1': { data: ACTION_DETAIL },
    })
    const first = await row('盤點同年份的變電箱並排定汰換。')
    await user.click(within(first).getByRole('button', { name: '查看歷史' }))
    const history = await within(first).findByRole('list', { name: '狀態變更歷史' })
    const items = within(history).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('已發現')
    expect(items[1]).toHaveTextContent('已發現 → 處理中')
    expect(items[1]).toHaveTextContent('已向總務處反映')
  })

  it('修改負責人與期限（期限以台北當日結束為準）', async () => {
    const updated = { ...ACTION_DETAIL, owner: '營繕組', due_at: '2026-12-31T15:59:59Z' }
    const { calls, user } = open({
      'GET /api/v1/admin/actions': { data: ACTIONS },
      'PATCH /api/v1/admin/actions/ra-1': { data: updated },
    })
    const first = await row('盤點同年份的變電箱並排定汰換。')
    await user.click(within(first).getByRole('button', { name: '修改負責人與期限' }))
    const owner = within(first).getByLabelText('負責人')
    await user.clear(owner)
    await user.type(owner, '營繕組')
    const due = within(first).getByLabelText('期限')
    await user.clear(due)
    await user.type(due, '2026-12-31')
    await user.click(within(first).getByRole('button', { name: '儲存' }))

    expect(await within(first).findByText('負責：營繕組')).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({
      owner: '營繕組',
      due_at: '2026-12-31T23:59:59+08:00',
    })
  })

  it('沒有改善措施時顯示空狀態', async () => {
    open({ 'GET /api/v1/admin/actions': { data: [] } })
    expect(await screen.findByText(/目前沒有追蹤中的改善措施/)).toBeInTheDocument()
  })
})
