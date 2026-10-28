import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { EVENT_ID, RELATION_GRAPH } from '../../test/fixtures'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'
import type { RelationEdge } from '../api'

// jsdom 沒有 canvas：以假的 Cytoscape 記錄傳入的元素與事件處理器
const cy = vi.hoisted(() => ({
  options: [] as { elements: { data: Record<string, unknown> }[] }[],
  handlers: new Map<string, (e: { target: { id: () => string } }) => void>(),
}))
vi.mock('cytoscape', () => ({
  default: (options: (typeof cy.options)[number]) => {
    cy.options.push(options)
    const instance = {
      on: (event: string, selector: string, handler: (e: { target: { id: () => string } }) => void) => {
        cy.handlers.set(`${event} ${selector}`, handler)
        return instance
      },
      getElementById: () => ({ data: () => undefined, addClass: () => undefined }),
      edges: () => ({ removeClass: () => undefined }),
      destroy: () => undefined,
    }
    return instance
  },
}))

const LIST = 'GET /api/v1/admin/relations'

function open(routes: Record<string, MockRoute | ((call: { body: unknown }) => MockRoute)>) {
  window.history.pushState({}, '', '/admin/relations')
  const api = mockApi({ 'GET /api/v1/auth/me': ME, ...routes })
  render(<App />)
  return { ...api, user: userEvent.setup() }
}

function decided(edge: RelationEdge, decision: RelationEdge['decision'], note: string | null = null): MockRoute {
  const pending = decision === 'pending'
  return {
    data: {
      edge: {
        ...edge,
        decision,
        decided_by: pending ? null : 'admin:u-1',
        decided_at: pending ? null : '2026-10-22T00:00:00Z',
        decision_note: pending ? null : note,
      },
      nodes: RELATION_GRAPH.nodes,
    },
  }
}

beforeEach(() => {
  cy.options.length = 0
  cy.handlers.clear()
})

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('RelationGraph', () => {
  it('列出關聯：兩端事件、類型、決策、分數與方法，並把節點與邊交給 Cytoscape', async () => {
    open({ [LIST]: { data: RELATION_GRAPH } })
    const list = await screen.findByRole('region', { name: '關聯清單' })
    const first = await within(list).findByRole('button', { name: /工程一館停電 ↔ 圖書館網路維護/ })
    expect(within(first).getByText('相關事件')).toBeInTheDocument()
    expect(within(first).getByText('待確認')).toBeInTheDocument()
    expect(within(first).getByText('0.82')).toBeInTheDocument()
    // 推測原因有方向：原因 → 結果
    const cause = within(list).getByRole('button', { name: /工程一館旁道路施工 → 工程一館停電/ })
    expect(within(cause).getByText('推測原因')).toBeInTheDocument()
    expect(within(cause).getByText('已確認')).toBeInTheDocument()

    expect(within(screen.getByRole('navigation', { name: '主要頁面' })).getByText('事件關聯')).toHaveAttribute(
      'aria-current',
      'page',
    )
    const elements = cy.options.at(-1)!.elements.map((e) => e.data)
    expect(elements.filter((d) => d.source === undefined)).toHaveLength(3)
    expect(elements.filter((d) => d.source !== undefined).map((d) => d.id)).toEqual(['rel-1', 'rel-2'])
  })

  it('選擇關聯後顯示詳情：證據、方法、決策紀錄，連到兩端事件', async () => {
    const { user } = open({ [LIST]: { data: RELATION_GRAPH } })
    await user.click(await screen.findByRole('button', { name: /工程一館旁道路施工 → 工程一館停電/ }))
    const detail = screen.getByRole('region', { name: '關聯詳情' })
    expect(within(detail).getByText(/推測，尚未查證/)).toBeInTheDocument()
    expect(within(detail).getByText('src-4')).toBeInTheDocument()
    expect(within(detail).getByText('AI')).toBeInTheDocument()
    expect(within(detail).getByText(/施工時間吻合/)).toBeInTheDocument()
    expect(within(detail).getByRole('link', { name: '工程一館停電' })).toHaveAttribute(
      'href',
      `/admin/events/${EVENT_ID}`,
    )
    // 已決定的只能回滾，不能直接翻轉
    expect(within(detail).queryByRole('button', { name: '駁回' })).not.toBeInTheDocument()
    expect(within(detail).getByRole('button', { name: '回滾到待確認' })).toBeInTheDocument()
  })

  it('在圖上點邊也會選擇該關聯', async () => {
    open({ [LIST]: { data: RELATION_GRAPH } })
    await screen.findByRole('region', { name: '關聯清單' })
    const tap = cy.handlers.get('tap edge')!
    expect(tap).toBeDefined()
    act(() => tap({ target: { id: () => 'rel-1' } }))
    const detail = await screen.findByRole('region', { name: '關聯詳情' })
    expect(within(detail).getByRole('button', { name: '確認' })).toBeInTheDocument()
  })

  it('確認關聯：送出決策與備註，成功後更新狀態並可回滾', async () => {
    const edge = RELATION_GRAPH.edges[0]
    const { calls, user } = open({
      [LIST]: { data: RELATION_GRAPH },
      'POST /api/v1/admin/relations/rel-1/decision': (call) => {
        const body = call.body as { decision: RelationEdge['decision']; note?: string }
        return decided(edge, body.decision, body.note ?? null)
      },
    })
    await user.click(await screen.findByRole('button', { name: /工程一館停電 ↔ 圖書館網路維護/ }))
    const form = screen.getByRole('form', { name: '記錄關聯判斷' })
    await user.type(within(form).getByLabelText('備註（選填）'), '同一棟大樓同時段')
    await user.click(within(form).getByRole('button', { name: '確認' }))

    const detail = screen.getByRole('region', { name: '關聯詳情' })
    expect(await within(detail).findByRole('status')).toHaveTextContent('已確認這條關聯')
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ decision: 'confirmed', note: '同一棟大樓同時段' })
    const item = screen.getByRole('button', { name: /工程一館停電 ↔ 圖書館網路維護/ })
    expect(within(item).getByText('已確認')).toBeInTheDocument()

    await user.click(within(detail).getByRole('button', { name: '回滾到待確認' }))
    expect(await within(item).findByText('待確認')).toBeInTheDocument()
    expect(calls.filter((c) => c.method === 'POST').at(-1)?.body).toEqual({ decision: 'pending' })
  })

  it('駁回後仍留在畫面上，可以回滾', async () => {
    const edge = RELATION_GRAPH.edges[0]
    const { calls, user } = open({
      [LIST]: { data: RELATION_GRAPH },
      'POST /api/v1/admin/relations/rel-1/decision': (call) =>
        decided(edge, (call.body as { decision: RelationEdge['decision'] }).decision),
    })
    await user.click(await screen.findByRole('button', { name: /工程一館停電 ↔ 圖書館網路維護/ }))
    await user.click(within(screen.getByRole('form', { name: '記錄關聯判斷' })).getByRole('button', { name: '駁回' }))
    const item = screen.getByRole('button', { name: /工程一館停電 ↔ 圖書館網路維護/ })
    expect(await within(item).findByText('已駁回')).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ decision: 'rejected' })

    await user.click(screen.getByRole('button', { name: '回滾到待確認' }))
    expect(await within(item).findByText('待確認')).toBeInTheDocument()
  })

  it('伺服器拒絕時顯示訊息，狀態不變', async () => {
    const { user } = open({
      [LIST]: { data: RELATION_GRAPH },
      'POST /api/v1/admin/relations/rel-1/decision': {
        status: 409,
        error: { code: 'CONFLICT', message: '關聯已被決定，請先回滾' },
      },
    })
    await user.click(await screen.findByRole('button', { name: /工程一館停電 ↔ 圖書館網路維護/ }))
    await user.click(screen.getByRole('button', { name: '確認' }))
    const detail = screen.getByRole('region', { name: '關聯詳情' })
    expect(await within(detail).findByRole('alert')).toHaveTextContent('關聯已被決定，請先回滾')
    expect(
      within(screen.getByRole('button', { name: /工程一館停電 ↔ 圖書館網路維護/ })).getByText('待確認'),
    ).toBeInTheDocument()
  })

  it('依類型、決策篩選，可選擇包含已駁回', async () => {
    const { calls, user } = open({
      [LIST]: { data: RELATION_GRAPH },
      [`${LIST}?type=hypothesized_cause`]: { data: { ...RELATION_GRAPH, edges: [RELATION_GRAPH.edges[1]] } },
      [`${LIST}?type=hypothesized_cause&decision=confirmed`]: { data: RELATION_GRAPH },
      [`${LIST}?type=hypothesized_cause&decision=confirmed&include_rejected=true`]: { data: RELATION_GRAPH },
    })
    await user.selectOptions(await screen.findByLabelText('類型'), 'hypothesized_cause')
    expect(await screen.findByRole('button', { name: /工程一館旁道路施工 → 工程一館停電/ })).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('決策'), 'confirmed')
    await user.click(screen.getByLabelText('包含已駁回'))
    expect(calls.map((c) => c.path)).toEqual(
      expect.arrayContaining([
        '/api/v1/admin/relations?type=hypothesized_cause',
        '/api/v1/admin/relations?type=hypothesized_cause&decision=confirmed',
        '/api/v1/admin/relations?type=hypothesized_cause&decision=confirmed&include_rejected=true',
      ]),
    )
  })

  it('沒有關聯時顯示空狀態', async () => {
    open({ [LIST]: { data: { nodes: [], edges: [] } } })
    expect(await screen.findByText('目前沒有符合條件的關聯')).toBeInTheDocument()
  })
})
