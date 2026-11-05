import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import type { LocationItem } from '../api'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'

const LOCATIONS: LocationItem[] = [
  {
    id: 'loc-1',
    name: '工程五館',
    parent_id: null,
    event_count: 3,
    aliases: [{ id: 'loc-2', name: '工五', event_count: 2 }],
    children: [],
  },
  {
    id: 'loc-3',
    name: '宿舍區',
    parent_id: null,
    event_count: 0,
    aliases: [],
    children: [{ id: 'loc-4', name: '男九舍', event_count: 4 }],
  },
]

function open(routes: Record<string, MockRoute | ((c: never) => MockRoute)>) {
  window.history.pushState({}, '', '/admin/locations')
  const api = mockApi({ 'GET /api/v1/auth/me': ME, ...routes })
  render(<App />)
  return { ...api, user: userEvent.setup() }
}

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('Locations', () => {
  it('列出代表地點、AI 判斷的別名與下層地點，事件數以文字顯示', async () => {
    open({ 'GET /api/v1/admin/locations': { data: LOCATIONS } })
    const five = (await screen.findByRole('heading', { name: '工程五館' })).closest('li')!
    expect(within(five).getByText('3 件事件（含別名）')).toBeInTheDocument()
    const aliases = within(five).getByRole('list', { name: '工程五館的別名' })
    expect(within(aliases).getByText('工五')).toBeInTheDocument()
    expect(within(aliases).getByText('2 件')).toBeInTheDocument()
    const dorms = screen.getByRole('heading', { name: '宿舍區' }).closest('li')!
    const children = within(dorms).getByRole('list', { name: '宿舍區之內的地點' })
    expect(within(children).getByText('男九舍')).toBeInTheDocument()
    expect(within(dorms).getByText(/上層地點只用來篩選，不併入件數/)).toBeInTheDocument()
  })

  it('拆開判斷錯的別名：送出後重新載入，顯示之後不會再自動合併', async () => {
    let split = false
    const { calls, user } = open({
      'GET /api/v1/admin/locations': () => ({
        data: split
          ? [
              { ...LOCATIONS[0], event_count: 1, aliases: [] },
              { id: 'loc-2', name: '工五', parent_id: null, event_count: 2, aliases: [], children: [] },
              LOCATIONS[1],
            ]
          : LOCATIONS,
      }),
      'POST /api/v1/admin/locations/loc-2/split': () => {
        split = true
        return { data: { id: 'loc-2', canonical_id: null, parent_id: null } }
      },
    })
    const aliases = await screen.findByRole('list', { name: '工程五館的別名' })
    await user.click(within(aliases).getByRole('button', { name: '把「工五」拆開' }))

    expect(calls.find((c) => c.method === 'POST')?.path).toBe('/api/v1/admin/locations/loc-2/split')
    expect(await screen.findByRole('status')).toHaveTextContent('已拆開「工五」，之後不會再自動合併')
    expect(await screen.findByRole('heading', { name: '工五' })).toBeInTheDocument()
  })

  it('拆開失敗時顯示伺服器訊息', async () => {
    const { user } = open({
      'GET /api/v1/admin/locations': { data: LOCATIONS },
      'POST /api/v1/admin/locations/loc-4/split': {
        status: 409,
        error: { code: 'CONFLICT', message: '這個地點不是別名，也沒有上層地點，不需要拆開' },
      },
    })
    const children = await screen.findByRole('list', { name: '宿舍區之內的地點' })
    await user.click(within(children).getByRole('button', { name: '把「男九舍」拆開' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('不需要拆開')
  })

  it('導覽列有「地點」', async () => {
    open({ 'GET /api/v1/admin/locations': { data: [] } })
    expect(await screen.findByText('還沒有任何地點')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '地點' })).toHaveAttribute('href', '/admin/locations')
  })
})
