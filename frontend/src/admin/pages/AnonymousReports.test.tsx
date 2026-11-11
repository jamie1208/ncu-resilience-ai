import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import type { InboxGroup } from '../api'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'

const INBOX = '/api/v1/admin/anonymous-reports'

const EVENT = { id: 'evt-1', title: '工程五館走廊燈管閃爍', status: 'unverified' }

const GROUPS: InboxGroup[] = [
  {
    id: 7,
    latest_submitted_at: '2026-11-01T02:00:00Z',
    events: [],
    reports: [
      {
        id: 7,
        kind: 'original',
        content: '宿舍區飲水機漏水，地板很滑',
        location: null,
        submitted_at: '2026-11-01T02:00:00Z',
        ingestion_status: 'not_ingested',
        source_item_id: null,
        analysis_status: null,
        events: [],
      },
    ],
  },
  {
    id: 5,
    latest_submitted_at: '2026-11-01T04:00:00Z',
    events: [EVENT],
    reports: [
      {
        id: 5,
        kind: 'original',
        content: '工程五館三樓走廊燈管一直閃爍，晚上很暗',
        location: '工程五館',
        submitted_at: '2026-11-01T00:00:00Z',
        ingestion_status: 'ingested',
        source_item_id: 'item-5',
        analysis_status: 'done',
        events: [EVENT],
      },
      {
        id: 9,
        kind: 'supplement',
        content: '補充：今天早上還是一樣，緊急照明也沒亮',
        location: null,
        submitted_at: '2026-11-01T04:00:00Z',
        ingestion_status: 'ingested',
        source_item_id: 'item-9',
        analysis_status: 'queued',
        events: [],
      },
    ],
  },
]

function open(routes: Record<string, MockRoute | ((c: never) => MockRoute)>) {
  window.history.pushState({}, '', '/admin/anonymous-reports')
  const api = mockApi({ 'GET /api/v1/auth/me': ME, ...routes })
  render(<App />)
  return { ...api, user: userEvent.setup() }
}

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('AnonymousReports', () => {
  it('導覽有「匿名回報」', async () => {
    open({ [`GET ${INBOX}`]: { data: [] } })
    const nav = await screen.findByRole('navigation', { name: '主要頁面' })
    expect(within(nav).getByRole('link', { name: '匿名回報' })).toHaveAttribute(
      'href',
      '/admin/anonymous-reports',
    )
  })

  it('補件依收據排在原投稿之下，顯示收到時間（台北）、擷取與分析狀態、所屬事件', async () => {
    open({ [`GET ${INBOX}`]: { data: GROUPS } })
    const group = (await screen.findByText('工程五館三樓走廊燈管一直閃爍，晚上很暗')).closest(
      '[data-group]',
    ) as HTMLElement
    const list = within(group).getByRole('list', { name: '同一張收據的投稿' })
    const reports = within(list).getAllByRole('listitem')
    expect(reports).toHaveLength(2)
    expect(within(reports[0]).getByText('原投稿')).toBeInTheDocument()
    expect(within(reports[0]).getByText('2026-11-01 08:00')).toBeInTheDocument()
    expect(within(reports[0]).getByText('地點：工程五館')).toBeInTheDocument()
    expect(within(reports[0]).getByText('分析完成')).toBeInTheDocument()
    expect(within(reports[1]).getByText('補件')).toBeInTheDocument()
    expect(within(reports[1]).getByText('補充：今天早上還是一樣，緊急照明也沒亮')).toBeInTheDocument()
    expect(within(reports[1]).getByText('排隊分析中')).toBeInTheDocument()
    expect(within(group).getByText('1 則補件')).toBeInTheDocument()
    const link = within(group).getByRole('link', { name: /工程五館走廊燈管閃爍/ })
    expect(link).toHaveAttribute('href', '/admin/events/evt-1')
  })

  it('沒有成立事件、還沒擷取的投稿也看得到', async () => {
    open({ [`GET ${INBOX}`]: { data: GROUPS } })
    const group = (await screen.findByText('宿舍區飲水機漏水，地板很滑')).closest(
      '[data-group]',
    ) as HTMLElement
    expect(within(group).getByText('尚未擷取')).toBeInTheDocument()
    expect(within(group).getByText('還沒有歸入任何事件')).toBeInTheDocument()
  })

  it('收件匣是空的時顯示說明', async () => {
    open({ [`GET ${INBOX}`]: { data: [] } })
    expect(await screen.findByText('還沒有收到匿名回報。')).toBeInTheDocument()
  })

  it('有下一頁時可以載入更多', async () => {
    const { calls, user } = open({
      [`GET ${INBOX}`]: { data: [GROUPS[0]], meta: { next_cursor: '7' } },
      [`GET ${INBOX}?cursor=7`]: { data: [GROUPS[1]] },
    })
    await screen.findByText('宿舍區飲水機漏水，地板很滑')
    await user.click(screen.getByRole('button', { name: '載入更多' }))
    expect(await screen.findByText('工程五館三樓走廊燈管一直閃爍，晚上很暗')).toBeInTheDocument()
    expect(calls.some((c) => c.path === `${INBOX}?cursor=7`)).toBe(true)
    expect(screen.queryByRole('button', { name: '載入更多' })).not.toBeInTheDocument()
  })
})
