import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { EVENT_ID, PUBLICATIONS } from '../../test/fixtures'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'

function open(routes: Record<string, MockRoute>) {
  window.history.pushState({}, '', '/admin/publications')
  const api = mockApi({ 'GET /api/v1/auth/me': ME, ...routes })
  render(<App />)
  return { ...api, user: userEvent.setup() }
}

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('Publications', () => {
  it('列出公開內容：狀態、公開程度、預覽、下架原因，並連到事件', async () => {
    open({ 'GET /api/v1/admin/publications': { data: PUBLICATIONS } })
    const first = (await screen.findByRole('link', { name: '工程一館停電' })).closest('li')!
    expect(within(first).getByText('公開中')).toBeInTheDocument()
    expect(within(first).getByText('事件內容')).toBeInTheDocument()
    expect(within(first).getByText('v2')).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: '工程一館停電' })).toHaveAttribute(
      'href',
      `/admin/events/${EVENT_ID}`,
    )
    const second = screen.getByRole('link', { name: '圖書館網路維護' }).closest('li')!
    expect(within(second).getByText('已下架')).toBeInTheDocument()
    expect(within(second).getByText('安全提醒')).toBeInTheDocument()
    expect(within(second).getByText('近期請留意圖書館網路服務。')).toBeInTheDocument()
    expect(within(second).getByText(/內容待確認/)).toBeInTheDocument()
    expect(within(second).getByText('疑似指控，需人工判斷')).toBeInTheDocument()
    expect(within(second).queryByRole('button', { name: '下架' })).not.toBeInTheDocument()
  })

  it('依狀態篩選', async () => {
    const { calls, user } = open({
      'GET /api/v1/admin/publications': { data: PUBLICATIONS },
      'GET /api/v1/admin/publications?state=taken_down': { data: [PUBLICATIONS[1]] },
    })
    await user.selectOptions(await screen.findByLabelText('狀態'), 'taken_down')
    expect(calls.map((c) => c.path)).toContain('/api/v1/admin/publications?state=taken_down')
    expect(await screen.findByText('圖書館網路維護')).toBeInTheDocument()
  })

  it('一鍵下架：原因可省略，成功後更新狀態', async () => {
    const { calls, user } = open({
      'GET /api/v1/admin/publications': { data: PUBLICATIONS },
      'POST /api/v1/admin/publications/pub-1/takedown': {
        data: { id: 'pub-1', state: 'taken_down', previous: 'published' },
      },
    })
    const first = (await screen.findByRole('link', { name: '工程一館停電' })).closest('li')!
    await user.click(within(first).getByRole('button', { name: '下架' }))
    const form = within(first).getByRole('form', { name: '下架公開內容' })
    await user.click(within(form).getByRole('button', { name: '確認下架' }))

    expect(await within(first).findByText('已下架')).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({})
    expect(within(first).getByRole('button', { name: '重新上架' })).toBeInTheDocument()
  })

  it('下架原因會送出，含個資時顯示伺服器訊息', async () => {
    const { calls, user } = open({
      'GET /api/v1/admin/publications': { data: PUBLICATIONS },
      'POST /api/v1/admin/publications/pub-1/takedown': {
        status: 422,
        error: { code: 'VALIDATION_ERROR', message: '下架原因不得含個資' },
      },
    })
    const first = (await screen.findByRole('link', { name: '工程一館停電' })).closest('li')!
    await user.click(within(first).getByRole('button', { name: '下架' }))
    const form = within(first).getByRole('form', { name: '下架公開內容' })
    await user.type(within(form).getByLabelText('下架原因（選填）'), '誤判')
    await user.click(within(form).getByRole('button', { name: '確認下架' }))
    expect(await within(first).findByRole('alert')).toHaveTextContent('下架原因不得含個資')
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ reason: '誤判' })
    expect(within(first).getByText('公開中')).toBeInTheDocument()
  })

  it('重新上架交回公開規則判斷，顯示判斷結果', async () => {
    const { user } = open({
      'GET /api/v1/admin/publications': { data: PUBLICATIONS },
      'POST /api/v1/admin/publications/pub-2/restore': {
        data: { id: 'pub-2', state: 'withdrawn', previous: 'taken_down' },
      },
    })
    const second = (await screen.findByRole('link', { name: '圖書館網路維護' })).closest('li')!
    await user.click(within(second).getByRole('button', { name: '重新上架' }))
    expect(await within(second).findByText('已撤下')).toBeInTheDocument()
    expect(within(second).getByRole('status')).toHaveTextContent('公開規則判定目前不公開')
  })

  it('沒有公開內容時顯示空狀態', async () => {
    open({ 'GET /api/v1/admin/publications': { data: [] } })
    expect(await screen.findByText('目前沒有公開內容')).toBeInTheDocument()
  })
})
