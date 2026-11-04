import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'
import { RESILIENCE } from '../../test/resilienceFixtures'
import type { TrendForecast } from '../api'

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

const EXPLAINED: TrendForecast = {
  outcome: 'explained',
  freq: 'week',
  as_of: '2026-11-01',
  start: '2026-08-31',
  series: [
    {
      signal_id: 's1',
      event_type: 'facility',
      location: '工學院',
      counts: [
        { period_start: '2026-10-12', count: 1 },
        { period_start: '2026-10-19', count: 1 },
        { period_start: '2026-10-26', count: 6 },
      ],
      anomaly: { period_start: '2026-10-26', observed: 6, expected: 1, p_value: 0.0006 },
      evidence_ids: ['ev-1'],
    },
    {
      signal_id: 's2',
      event_type: 'weather',
      location: null,
      counts: [
        { period_start: '2026-10-12', count: 0 },
        { period_start: '2026-10-19', count: 1 },
        { period_start: '2026-10-26', count: 0 },
      ],
      anomaly: null,
      evidence_ids: [],
    },
  ],
  coverage: {
    start: '2026-09-01',
    end: '2026-11-01',
    level: 'partial',
    ratio: 0.87,
    note: 'ncu-cc 缺 4 天（10/12–10/14、10/20）',
    sources: [],
  },
  evidence: [{ evidence_id: 'ev-1', event_id: 'ev-1', title: '工學院停電', source_code: 'ncu-news' }],
  forecast: {
    ai_run_id: 'run-1',
    status: 'succeeded',
    output: {
      status: 'ok',
      explanations: [
        {
          signal_id: 's1',
          summary: '同一週有多起停電，件數增加可能與電力問題集中有關。',
          driver: 'incident_cluster',
          basis: 'hypothesized',
          evidence_ids: ['ev-1'],
        },
      ],
      outlook: [{ statement: '留意工學院後續是否再有停電公告。', signal_id: 's1', evidence_ids: ['ev-1'] }],
      coverage_level: 'partial',
      caveats: ['volume_not_event_count', 'limited_coverage', 'small_counts'],
      evidence_ids: ['ev-1'],
      reason: '異常週集中在停電事件。',
      abstain_reason: null,
    },
    abstain_reason: null,
    error_type: null,
    skill_version: '1.0.0',
  },
  created_by: 'admin:u-1',
  created_at: '2026-11-02T01:00:00Z',
}

const NO_ANOMALY: TrendForecast = {
  ...EXPLAINED,
  outcome: 'no_anomaly',
  series: EXPLAINED.series.map((s) => ({ ...s, anomaly: null, evidence_ids: [] })),
  coverage: { ...EXPLAINED.coverage, level: 'full', ratio: 1, note: null },
  evidence: [],
  forecast: null,
}

function open(latest: TrendForecast | null, post?: MockRoute) {
  const api = mockApi({
    'GET /api/v1/auth/me': ME,
    'GET /api/v1/admin/resilience': { data: RESILIENCE },
    'GET /api/v1/admin/trends/forecast': { data: latest },
    ...(post ? { 'POST /api/v1/admin/trends/forecast': post } : {}),
  })
  window.history.pushState({}, '', '/admin/resilience')
  render(<App />)
  return api
}

describe('趨勢解讀（S09）', () => {
  it('明顯標註聲量≠事件數與資料覆蓋率', async () => {
    open(EXPLAINED)
    const region = await screen.findByRole('region', { name: '趨勢解讀' })
    const note = await within(region).findByRole('note', { name: '解讀限制' })
    expect(within(note).getByText(/聲量≠事件數/)).toBeInTheDocument()
    expect(within(note).getByText(/資料覆蓋率：部分缺漏（87\.0%）/)).toBeInTheDocument()
    expect(within(note).getByText(/ncu-cc 缺 4 天/)).toBeInTheDocument()
  })

  it('顯示異常解釋、推測標示、前瞻訊號與程式計算的數字', async () => {
    open(EXPLAINED)
    const region = await screen.findByRole('region', { name: '趨勢解讀' })
    const explanations = await within(region).findByRole('list', { name: '異常解釋' })
    expect(within(explanations).getByText(/設施・工學院/)).toBeInTheDocument()
    expect(within(explanations).getByText('推測，尚待查證')).toBeInTheDocument()
    expect(within(explanations).getByText(/多起不同事件/)).toBeInTheDocument()
    expect(within(explanations).getByText(/工學院停電/)).toBeInTheDocument()
    expect(within(region).getByText('留意工學院後續是否再有停電公告。')).toBeInTheDocument()
    expect(within(region).getByText('異常週期件數少於 10')).toBeInTheDocument()
    const table = within(region).getByRole('table', { name: '件數序列' })
    expect(within(table).getByText('6（期望 1.0，p = 0.0006）')).toBeInTheDocument()
    expect(within(table).getByText('天氣・全校')).toBeInTheDocument()
    expect(within(region).getByText(/截至 2026-11-01/)).toBeInTheDocument()
  })

  it('沒有異常時說明未呼叫模型', async () => {
    open(NO_ANOMALY)
    const region = await screen.findByRole('region', { name: '趨勢解讀' })
    expect(await within(region).findByText(/沒有偵測到突增，未呼叫模型/)).toBeInTheDocument()
    expect(within(region).getByText(/資料覆蓋率：完整（100\.0%）/)).toBeInTheDocument()
    expect(within(region).queryByRole('list', { name: '異常解釋' })).not.toBeInTheDocument()
  })

  it('尚未執行時可手動觸發', async () => {
    const api = open(null, { status: 201, data: EXPLAINED })
    const region = await screen.findByRole('region', { name: '趨勢解讀' })
    expect(await within(region).findByText(/尚未執行趨勢解讀/)).toBeInTheDocument()
    await userEvent.click(within(region).getByRole('button', { name: '執行趨勢解讀' }))
    expect(await within(region).findByRole('list', { name: '異常解釋' })).toBeInTheDocument()
    const call = api.calls.find((c) => c.method === 'POST')
    expect(call?.path).toBe('/api/v1/admin/trends/forecast')
    expect(call?.body).toEqual({ freq: 'week' })
  })

  it('解讀失敗時仍顯示數字', async () => {
    open({
      ...EXPLAINED,
      outcome: 'failed',
      forecast: { ...EXPLAINED.forecast!, status: 'failed', output: null, error_type: 'LLMError' },
    })
    const region = await screen.findByRole('region', { name: '趨勢解讀' })
    expect(await within(region).findByText(/S09 解讀失敗/)).toBeInTheDocument()
    expect(within(region).getByRole('table', { name: '件數序列' })).toBeInTheDocument()
  })
})
