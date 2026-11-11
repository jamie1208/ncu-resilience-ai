import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import type { SeriesDetail, SeriesSummary, SeriesTimeline } from '../api'
import { matchRoute } from '../router'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'

const LIST: SeriesSummary[] = [
  {
    id: 's-1',
    category: 'traffic',
    title: '停放機車遭碰撞',
    title_source: 'ai',
    event_count: 3,
    latest_joined_at: '2026-11-01T02:00:00+00:00',
    created_at: '2026-10-06T02:00:00+00:00',
  },
  {
    id: 's-2',
    category: 'facility',
    title: '工程五館電梯故障',
    title_source: 'first_event',
    event_count: 1,
    latest_joined_at: '2026-11-03T02:00:00+00:00',
    created_at: '2026-11-03T02:00:00+00:00',
  },
]

const DETAIL: SeriesDetail = {
  ...LIST[0],
  events: [
    { id: 'e-1', title: '工五機車被撞', status: 'unverified', occurrence_start_date: '2026-10-06',
      created_at: '2026-10-06T02:00:00+00:00', joined_at: '2026-10-06T02:00:00+00:00', method: 'fallback' },
    { id: 'e-2', title: '宵夜街機車被擦撞', status: 'verified', occurrence_start_date: '2026-10-15',
      created_at: '2026-10-15T02:00:00+00:00', joined_at: '2026-10-15T02:00:00+00:00', method: 'ai' },
    { id: 'e-3', title: '工程五館機車被撞倒', status: 'unverified', occurrence_start_date: '2026-10-27',
      created_at: '2026-10-27T02:00:00+00:00', joined_at: '2026-10-27T02:00:00+00:00', method: 'ai' },
  ],
  removals: [],
  analysis: {
    id: 'a-1',
    status: 'ok',
    title: '停放機車遭碰撞',
    summary: '停放機車被碰撞，集中在工程五館外。',
    bursts: [],
    location_groups: [
      { label: '工程五館', reason: '工五是工程五館的簡稱。', event_ids: ['e-1', 'e-3'], event_count: 2, total: 3 },
    ],
    pattern_status: 'ok',
    pattern_abstain_reason: null,
    patterns: [
      { pattern_id: 'p1', description: '都發生在晚間、停在路邊。', label: '推測，尚未查證',
        event_ids: ['e-1', 'e-3'], event_count: 2, total: 3,
        quotes: [{ evidence_id: 'x', quote: '晚上停在工五路邊' }], mentions_party: false },
    ],
    causes: [
      { cause_id: 'c1', description: '可能是路邊停放區太靠近車道。', label: '推測，尚未查證',
        to_verify: '向總務處確認停車區與車道距離。', event_ids: ['e-1', 'e-3'], event_count: 2, total: 3,
        quotes: [{ evidence_id: 'y', quote: '那邊車道很窄' }], mentions_party: false, decision: 'hypothesized' },
      { cause_id: 'c2', description: '可能是某店家員工移動機車。', label: '推測，尚未查證',
        to_verify: '確認店家是否移車。', event_ids: ['e-2'], event_count: 1, total: 3,
        quotes: [{ evidence_id: 'z', quote: '被移到旁邊' }], mentions_party: true, decision: 'hypothesized' },
    ],
    measures: [
      { action: '盡量停在畫線的機車格內。', audience: 'students', horizon: 'short', priority: 'medium',
        kind: 'prevent', cause_id: null, responsible_unit: null, tracked_action_id: null },
      { action: '查看工程五館外停車區與車道的距離。', audience: 'school', horizon: 'mid', priority: 'medium',
        kind: 'investigate', cause_id: 'c1', responsible_unit: '總務處', tracked_action_id: null },
      { action: '請機車行協助提醒停放位置。', audience: 'units', horizon: 'short', priority: 'low',
        kind: 'prevent', cause_id: null, responsible_unit: '機車行', tracked_action_id: 'act-9' },
    ],
    dropped: 1,
    created_at: '2026-11-09T04:00:00+00:00',
  },
  latest_attempt: { status: 'ok', abstain_reason: null, created_at: '2026-11-09T04:00:00+00:00' },
  actions: [
    { id: 'act-9', event_id: null, origin: 'series_measure', analysis_id: 'a-1', measure_index: 2,
      title: '請機車行協助提醒停放位置。', owner: '機車行', due_at: null, state: 'handling', overdue: false,
      created_at: '2026-11-01T02:00:00+00:00' },
  ],
  legacy_cause_decisions: [],
}

const TIMELINE: SeriesTimeline = {
  granularity: 'week',
  range: '6M',
  location: null,
  start: '2026-05-11',
  end: '2026-11-09',
  points: [
    { period_start: '2026-10-05', count: 1, moving_average: null },
    { period_start: '2026-10-12', count: 1, moving_average: 0.5 },
    { period_start: '2026-10-19', count: 0, moving_average: 0.5 },
    { period_start: '2026-10-26', count: 1, moving_average: 0.75 },
  ],
  markers: [{ action_id: 'act-9', title: '請機車行協助提醒停放位置。', kind: 'started', on: '2026-11-02' }],
  comparisons: [
    { action_id: 'act-9', title: '請機車行協助提醒停放位置。', start: '2026-11-02', status: 'insufficient',
      before_events: 3, after_events: 0, after_weeks: 1, before_weekly_average: null,
      after_weekly_average: null, change_ratio: null, message: '資料還不夠',
      caveat: '變少不一定是措施的功勞：同一段時間也可能有其他變化。' },
  ],
  locations: [
    { name: '工五', event_count: 1 },
    { name: '工程五館', event_count: 1 },
  ],
}

function open(path: string, routes: Record<string, MockRoute | ((c: never) => MockRoute)>) {
  window.history.pushState({}, '', path)
  const api = mockApi({ 'GET /api/v1/auth/me': ME, ...routes })
  render(<App />)
  return { ...api, user: userEvent.setup() }
}

const DETAIL_ROUTES = {
  'GET /api/v1/admin/series/s-1': { data: DETAIL },
  'GET /api/v1/admin/series/s-1/timeline': { data: TIMELINE },
}

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('Series', () => {
  it('路由與導覽列', async () => {
    expect(matchRoute('/admin/series')).toEqual({ page: 'series', seriesId: null })
    expect(matchRoute('/admin/series/s-1')).toEqual({ page: 'series', seriesId: 's-1' })
    open('/admin/series', { 'GET /api/v1/admin/series': { data: [] } })
    expect(await screen.findByText('還沒有任何系列，同類事件累積到 2 起就會自動成立。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '系列' })).toHaveAttribute('href', '/admin/series')
  })

  it('系列清單：名稱、件數、1 件顯示為單一事件，名稱來源以文字標示', async () => {
    open('/admin/series', { 'GET /api/v1/admin/series': { data: LIST } })
    const first = (await screen.findByRole('link', { name: '停放機車遭碰撞' })).closest('li')!
    expect(first.querySelector('a')).toHaveAttribute('href', '/admin/series/s-1')
    expect(within(first).getByText('3 件')).toBeInTheDocument()
    expect(within(first).getByText('AI 命名')).toBeInTheDocument()
    const single = screen.getByRole('link', { name: '工程五館電梯故障' }).closest('li')!
    expect(within(single).getByText('單一事件')).toBeInTheDocument()
  })

  it('分析：規律與原因帶推測標籤，件數由程式算，提到個人或店家時註明', async () => {
    open('/admin/series/s-1', DETAIL_ROUTES)
    expect(await screen.findByRole('heading', { name: '停放機車遭碰撞', level: 1 })).toBeInTheDocument()
    expect(screen.getByText('停放機車被碰撞，集中在工程五館外。')).toBeInTheDocument()

    const patterns = screen.getByRole('region', { name: 'AI 看到的規律' })
    expect(within(patterns).getByText('都發生在晚間、停在路邊。')).toBeInTheDocument()
    expect(within(patterns).getByText('推測，尚未查證')).toBeInTheDocument()
    expect(within(patterns).getByText('3 件中有 2 件')).toBeInTheDocument()
    expect(within(patterns).getByText('「晚上停在工五路邊」')).toBeInTheDocument()

    const causes = screen.getByRole('region', { name: '可能原因' })
    expect(within(causes).getAllByText('推測，尚未查證')).toHaveLength(2)
    expect(within(causes).getByText('待查證：向總務處確認停車區與車道距離。')).toBeInTheDocument()
    expect(within(causes).getByText('提到特定個人、群體、店家或單位，公開前請確認')).toBeInTheDocument()

    const places = screen.getByRole('region', { name: '發生地點' })
    expect(within(places).getByText('工程五館')).toBeInTheDocument()
    expect(within(places).getByText('3 件中有 2 件')).toBeInTheDocument()
    expect(screen.getByText(/有 1 項引用不正確，已丟棄/)).toBeInTheDocument()
  })

  it('時間軸：長條圖可讀、措施標記、前後比較資料不夠時只顯示「資料還不夠」並附說明', async () => {
    open('/admin/series/s-1', DETAIL_ROUTES)
    const chart = await screen.findByRole('img', { name: /每週件數/ })
    expect(chart).toHaveAccessibleName(expect.stringContaining('共 3 件'))
    const timeline = screen.getByRole('region', { name: '時間軸' })
    expect(within(timeline).getByText(/開始處理：請機車行協助提醒停放位置。（2026-11-02）/)).toBeInTheDocument()
    const compare = screen.getByRole('region', { name: '改善前後比較' })
    expect(within(compare).getByText('資料還不夠')).toBeInTheDocument()
    expect(within(compare).getByText(/變少不一定是措施的功勞/)).toBeInTheDocument()
    expect(within(compare).queryByText(/%/)).not.toBeInTheDocument()
  })

  it('切換粒度、範圍與地點時重新查詢時間軸', async () => {
    const { calls, user } = open('/admin/series/s-1', DETAIL_ROUTES)
    await screen.findByRole('img', { name: /每週件數/ })
    await user.selectOptions(screen.getByLabelText('粒度'), 'month')
    await user.selectOptions(screen.getByLabelText('範圍'), '1Y')
    await user.selectOptions(screen.getByLabelText('地點'), '工五')
    const last = calls.filter((c) => c.path.startsWith('/api/v1/admin/series/s-1/timeline')).at(-1)!
    expect(last.path).toContain('granularity=month')
    expect(last.path).toContain('range=1Y')
    expect(last.path).toContain(`location=${encodeURIComponent('工五')}`)
  })

  it('改善建議：學校與相關單位的可一鍵加入追蹤，同學的不追蹤，已追蹤的顯示狀態', async () => {
    let added = false
    const { calls, user } = open('/admin/series/s-1', {
      ...DETAIL_ROUTES,
      'GET /api/v1/admin/series/s-1': () => ({
        data: added
          ? { ...DETAIL, analysis: { ...DETAIL.analysis!, measures: DETAIL.analysis!.measures.map((m, i) =>
              i === 1 ? { ...m, tracked_action_id: 'act-10' } : m) } }
          : DETAIL,
      }),
      'POST /api/v1/admin/series/s-1/actions': () => {
        added = true
        return { status: 201, data: { ...DETAIL.actions[0], id: 'act-10', state: 'discovered' } }
      },
    })
    const measures = await screen.findByRole('region', { name: '改善建議' })
    const items = within(measures).getAllByRole('listitem')
    expect(within(items[0]).queryByRole('button')).not.toBeInTheDocument()
    expect(within(items[0]).getByText('同學')).toBeInTheDocument()
    expect(within(items[2]).getByText('已在追蹤')).toBeInTheDocument()
    await user.click(within(items[1]).getByRole('button', { name: '加入追蹤：查看工程五館外停車區與車道的距離。' }))

    const post = calls.find((c) => c.method === 'POST')!
    expect(post.path).toBe('/api/v1/admin/series/s-1/actions')
    expect(post.body).toEqual({ analysis_id: 'a-1', measure_index: 1 })
    expect(await screen.findByRole('status')).toHaveTextContent('已加入改善追蹤')
  })

  it('手動改名：送出 PATCH，之後 AI 不會覆蓋', async () => {
    const { calls, user } = open('/admin/series/s-1', {
      ...DETAIL_ROUTES,
      'PATCH /api/v1/admin/series/s-1': { data: { id: 's-1', title: '機車停放被碰撞', title_source: 'manual' } },
    })
    const input = await screen.findByLabelText('系列名稱')
    await user.clear(input)
    await user.type(input, '機車停放被碰撞')
    await user.click(screen.getByRole('button', { name: '改名' }))
    const patch = calls.find((c) => c.method === 'PATCH')!
    expect(patch.body).toEqual({ title: '機車停放被碰撞' })
    expect(await screen.findByRole('status')).toHaveTextContent('已改名，之後 AI 不會覆蓋這個名稱')
  })

  it('人工確認原因：必須填依據，送出後顯示已確認', async () => {
    const { calls, user } = open('/admin/series/s-1', {
      ...DETAIL_ROUTES,
      'POST /api/v1/admin/series/s-1/causes/c1/decision': { status: 201, data: { cause_id: 'c1', decision: 'confirmed' } },
    })
    const causes = await screen.findByRole('region', { name: '可能原因' })
    const first = within(causes).getAllByRole('listitem')[0]
    await user.type(within(first).getByLabelText('確認依據'), '總務處回覆確認')
    await user.click(within(first).getByRole('button', { name: '標為已確認' }))
    const post = calls.find((c) => c.method === 'POST')!
    expect(post.body).toEqual({ analysis_id: 'a-1', decision: 'confirmed', basis: '總務處回覆確認' })
    expect(await screen.findByRole('status')).toHaveTextContent('已標為人工確認')
  })

  it('遷移（P8-T13）：事件層級的措施標出來源事件，舊小報告的原因確認唯讀列出', async () => {
    const migrated: SeriesDetail = {
      ...DETAIL,
      actions: [
        ...DETAIL.actions,
        { id: 'act-1', event_id: 'e-2', origin: 'measure', analysis_id: null, measure_index: 0,
          title: '宵夜街加裝反光標示', owner: '總務處', due_at: null, state: 'handling', overdue: false,
          created_at: '2026-10-16T02:00:00+00:00' },
      ],
      legacy_cause_decisions: [
        { event_id: 'e-2', report_id: 'rp-1', cause_id: 'c1', description: '路邊照明不足',
          decision: 'confirmed', basis: '11/5 電話向總務處確認', decided_by: 'admin:u-1',
          decided_at: '2026-11-05T02:00:00+00:00' },
      ],
    }
    open('/admin/series/s-1', {
      'GET /api/v1/admin/series/s-1': { data: migrated },
      'GET /api/v1/admin/series/s-1/timeline': { data: TIMELINE },
    })
    const actions = await screen.findByRole('region', { name: '改善追蹤' })
    const item = within(actions).getByText('宵夜街加裝反光標示').closest('li')!
    expect(within(item).getByRole('link', { name: '來自事件：宵夜街機車被擦撞' })).toHaveAttribute(
      'href',
      '/admin/events/e-2',
    )

    const legacy = screen.getByRole('region', { name: '舊版事件小報告的原因確認' })
    expect(within(legacy).getByText('路邊照明不足')).toBeInTheDocument()
    expect(within(legacy).getByText('已人工確認')).toBeInTheDocument()
    expect(within(legacy).getByText(/確認依據：11\/5 電話向總務處確認/)).toBeInTheDocument()
    expect(within(legacy).getByRole('link', { name: '宵夜街機車被擦撞' })).toHaveAttribute('href', '/admin/events/e-2')
    expect(within(legacy).queryByRole('button')).not.toBeInTheDocument()
  })

  it('沒有舊版確認時不顯示該區塊', async () => {
    open('/admin/series/s-1', DETAIL_ROUTES)
    await screen.findByRole('region', { name: '改善追蹤' })
    expect(screen.queryByRole('region', { name: '舊版事件小報告的原因確認' })).not.toBeInTheDocument()
  })

  it('還沒有分析時說明原因', async () => {
    open('/admin/series/s-1', {
      'GET /api/v1/admin/series/s-1': { data: { ...DETAIL, analysis: null, latest_attempt: null } },
      'GET /api/v1/admin/series/s-1/timeline': { data: TIMELINE },
    })
    expect(await screen.findByText(/還沒有系列分析/)).toBeInTheDocument()
  })

  it('改善建議區塊有「評估效益」入口（P8-T15），帶系列代號進 What-if 頁', async () => {
    const { user } = open('/admin/series/s-1', DETAIL_ROUTES)
    const measures = await screen.findByRole('region', { name: '改善建議' })
    const link = within(measures).getByRole('link', { name: '評估效益' })
    expect(link).toHaveAttribute('href', '/admin/scenarios?series=s-1')
    await user.click(link)
    expect(window.location.pathname + window.location.search).toBe('/admin/scenarios?series=s-1')
  })
})
