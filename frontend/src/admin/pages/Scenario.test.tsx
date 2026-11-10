import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { ME, mockApi, type MockRoute } from '../../test/mockApi'
import { SCENARIO, SCENARIO_EXPLAIN_FAILED, SCENARIO_NOT_ESTIMABLE } from '../../test/scenarioFixtures'
import { matchRoute } from '../router'

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

function open(routes: Record<string, MockRoute> = {}) {
  const api = mockApi({
    'GET /api/v1/auth/me': ME,
    'GET /api/v1/admin/scenarios': { data: [] },
    'POST /api/v1/admin/scenarios': { status: 201, data: SCENARIO },
    ...routes,
  })
  window.history.pushState({}, '', '/admin/scenarios')
  render(<App />)
  return api
}

async function fillMeasure(index: number, values: { name: string; low?: string; high?: string; cost?: string; rationale?: string }) {
  const group = screen.getByRole('group', { name: `措施 ${index}` })
  await userEvent.type(within(group).getByLabelText('名稱'), values.name)
  if (values.low) await userEvent.type(within(group).getByLabelText('效果下限（%）'), values.low)
  if (values.high) await userEvent.type(within(group).getByLabelText('效果上限（%）'), values.high)
  if (values.cost) await userEvent.type(within(group).getByLabelText('成本（新台幣，選填）'), values.cost)
  if (values.rationale) await userEvent.type(within(group).getByLabelText('依據（選填）'), values.rationale)
}

async function fillBasics() {
  const form = await screen.findByRole('form', { name: '模擬參數' })
  await userEvent.clear(within(form).getByLabelText('基準件數'))
  await userEvent.type(within(form).getByLabelText('基準件數'), '12')
  await userEvent.clear(within(form).getByLabelText('期間（天）'))
  await userEvent.type(within(form).getByLabelText('期間（天）'), '90')
  return form
}

describe('What-if 模擬', () => {
  it('路由、導覽，以及明顯的「假設情境，非成效預測」標示', async () => {
    expect(matchRoute('/admin/scenarios')).toEqual({ page: 'scenarios' })
    open()
    const nav = await screen.findByRole('navigation', { name: '主要頁面' })
    expect(within(nav).getByRole('link', { name: 'What-if 模擬' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('heading', { level: 1, name: 'What-if 模擬' })).toBeInTheDocument()
    expect(screen.getByRole('note', { name: '假設情境，非成效預測' })).toBeInTheDocument()
  })

  it('送出參數：百分比換成比例、空白欄位送 null、未填種子不送', async () => {
    const api = open()
    const form = await fillBasics()
    await userEvent.type(within(form).getByLabelText('地點（選填）'), '工程二館')
    await fillMeasure(1, { name: '加裝路燈', low: '20', high: '40', cost: '300000', rationale: '他校案例' })
    await userEvent.click(within(form).getByRole('button', { name: '新增措施' }))
    await fillMeasure(2, { name: '張貼告示' })
    await userEvent.click(within(form).getByRole('button', { name: '執行模擬' }))

    await screen.findByRole('region', { name: '模擬結果' })
    const post = api.calls.find((c) => c.method === 'POST' && c.path === '/api/v1/admin/scenarios')
    expect(post?.body).toEqual({
      baseline: 12,
      window_days: 90,
      location: '工程二館',
      measures: [
        { name: '加裝路燈', effect_low: 0.2, effect_high: 0.4, cost: 300000, rationale: '他校案例' },
        { name: '張貼告示', effect_low: null, effect_high: null, cost: null, rationale: null },
      ],
    })
  })

  it('效果只填一邊或下限大於上限時不送出', async () => {
    const api = open()
    const form = await fillBasics()
    await fillMeasure(1, { name: '加裝路燈', low: '20' })
    await userEvent.click(within(form).getByRole('button', { name: '執行模擬' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('措施 1：效果上下限要同時填寫或同時留空')

    const group = screen.getByRole('group', { name: '措施 1' })
    await userEvent.type(within(group).getByLabelText('效果上限（%）'), '10')
    await userEvent.click(within(form).getByRole('button', { name: '執行模擬' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('措施 1：效果下限不可大於上限')
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('可新增與移除措施，最多 10 項', async () => {
    open()
    const form = await screen.findByRole('form', { name: '模擬參數' })
    const add = within(form).getByRole('button', { name: '新增措施' })
    for (let i = 0; i < 9; i++) await userEvent.click(add)
    expect(within(form).getAllByRole('group', { name: /^措施 \d+$/ })).toHaveLength(10)
    expect(add).toBeDisabled()
    await userEvent.click(within(form).getByRole('button', { name: '移除措施 10' }))
    expect(within(form).getAllByRole('group', { name: /^措施 \d+$/ })).toHaveLength(9)
  })

  it('結果：估計值、範圍、分位數、降低比例、種子與無法計算的措施', async () => {
    open()
    const form = await fillBasics()
    await fillMeasure(1, { name: '加裝路燈', low: '20', high: '40' })
    await userEvent.click(within(form).getByRole('button', { name: '執行模擬' }))

    const result = await screen.findByRole('region', { name: '模擬結果' })
    expect(within(result).getByText('假設情境，非成效預測')).toBeInTheDocument()
    expect(within(result).getByText('7.1')).toBeInTheDocument()
    expect(within(result).getByText(/基準 12 件／90 天/)).toBeInTheDocument()
    expect(within(result).getByText(/5\.8 – 8\.6 件/)).toBeInTheDocument()
    expect(within(result).getByText(/6\.3 – 8\.0 件/)).toBeInTheDocument()
    expect(within(result).getByText(/40\.5%/)).toBeInTheDocument()
    expect(within(result).getByText(/種子 42/)).toBeInTheDocument()
    expect(within(result).getByText(/張貼告示：效果未知，未納入計算/)).toBeInTheDocument()
  })

  it('龍捲風圖依敏感度排序，並以文字列出每項措施的範圍', async () => {
    open({ 'GET /api/v1/admin/scenarios': { data: [SCENARIO] } })
    await userEvent.click(await screen.findByRole('button', { name: /加裝路燈、增加夜間巡邏、張貼告示/ }))
    const chart = await screen.findByRole('figure', { name: /敏感度（龍捲風圖）/ })
    const rows = within(chart).getAllByRole('listitem')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('加裝路燈'),
      expect.stringContaining('增加夜間巡邏'),
    ])
    expect(rows[0]).toHaveTextContent('6.1 – 8.2 件')
    expect(rows[0]).toHaveTextContent('擺幅 2.0')
    expect(within(chart).queryByText(/張貼告示/)).not.toBeInTheDocument()
  })

  it('AI 解釋：摘要、關鍵假設、每項假設說明與限制標註', async () => {
    open({ 'GET /api/v1/admin/scenarios': { data: [SCENARIO] } })
    await userEvent.click(await screen.findByRole('button', { name: /加裝路燈/ }))
    const explain = await screen.findByRole('region', { name: 'AI 解釋' })
    expect(within(explain).getByText(/90 天內件數可能從 12 件降到約 7\.14 件/)).toBeInTheDocument()
    expect(within(explain).getByText(/最影響結果的假設：加裝路燈/)).toBeInTheDocument()
    expect(within(explain).getByText(/依據為他校案例/)).toBeInTheDocument()
    expect(within(explain).getByText('假設情境，不是成效預測')).toBeInTheDocument()
    expect(within(explain).getByText('假設各措施效果互相獨立')).toBeInTheDocument()
    expect(within(explain).getByText('有措施效果未知，未納入計算')).toBeInTheDocument()
  })

  it('效果全部未知：顯示無法估計，沒有龍捲風圖', async () => {
    open({ 'GET /api/v1/admin/scenarios': { data: [SCENARIO_NOT_ESTIMABLE] } })
    await userEvent.click(await screen.findByRole('button', { name: /張貼告示/ }))
    const result = await screen.findByRole('region', { name: '模擬結果' })
    expect(within(result).getByText('無法估計')).toBeInTheDocument()
    expect(within(result).getByText(/所有措施的效果都未知/)).toBeInTheDocument()
    expect(screen.queryByRole('figure', { name: /龍捲風圖/ })).not.toBeInTheDocument()
  })

  it('AI 解釋失敗時仍顯示引擎結果', async () => {
    open({ 'GET /api/v1/admin/scenarios': { data: [SCENARIO_EXPLAIN_FAILED] } })
    await userEvent.click(await screen.findByRole('button', { name: /加裝路燈/ }))
    const explain = await screen.findByRole('region', { name: 'AI 解釋' })
    expect(within(explain).getByText(/AI 解釋失敗，數值仍由引擎計算/)).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: '模擬結果' })).getByText('7.1')).toBeInTheDocument()
  })

  it('後端驗證錯誤顯示在表單', async () => {
    open({
      'POST /api/v1/admin/scenarios': {
        status: 422,
        error: { code: 'VALIDATION_ERROR', message: '基準件數不正確' },
      },
    })
    const form = await fillBasics()
    await fillMeasure(1, { name: '加裝路燈', low: '20', high: '40' })
    await userEvent.click(within(form).getByRole('button', { name: '執行模擬' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('基準件數不正確')
  })

  it('最近的模擬列出時間與措施', async () => {
    open({ 'GET /api/v1/admin/scenarios': { data: [SCENARIO, SCENARIO_NOT_ESTIMABLE] } })
    const history = await screen.findByRole('region', { name: '最近的模擬' })
    expect(await within(history).findAllByRole('button')).toHaveLength(2)
    expect(within(history).getByText('2026-11-04 10:00')).toBeInTheDocument()
    expect(within(history).getByText('無法估計')).toBeInTheDocument()
  })
})

describe('What-if 基準件數自動帶入（P8-T09）', () => {
  const BASELINE = {
    baseline: 4.5,
    observed: 8,
    weeks: 4,
    history_start: '2026-05-18',
    history_end: '2026-06-14',
    window_days: 90,
    event_type: null,
    location: null,
  }

  it('依時間窗、類別、地點查詢並自動帶入，送出時交給後端計算', async () => {
    const api = open({ 'GET /api/v1/admin/scenarios/baseline': { data: BASELINE } })
    const form = await screen.findByRole('form', { name: '模擬參數' })
    await waitFor(() => expect(within(form).getByLabelText('基準件數')).toHaveValue('4.5'))
    expect(within(form).getByText(/依過去 4 週的 8 件推算/)).toBeInTheDocument()

    await userEvent.type(within(form).getByLabelText('事件類型（選填）'), 'security')
    await userEvent.type(within(form).getByLabelText('地點（選填）'), '總圖')
    await waitFor(() =>
      expect(api.calls.some((c) => c.path === '/api/v1/admin/scenarios/baseline?window_days=90&event_type=security&location=%E7%B8%BD%E5%9C%96')).toBe(true),
    )

    await fillMeasure(1, { name: '加裝路燈', low: '20', high: '40' })
    await userEvent.click(within(form).getByRole('button', { name: '執行模擬' }))
    await screen.findByRole('region', { name: '模擬結果' })
    const post = api.calls.find((c) => c.method === 'POST' && c.path === '/api/v1/admin/scenarios')
    expect(post?.body).not.toHaveProperty('baseline')
    expect(post?.body).toMatchObject({ window_days: 90, event_type: 'security', location: '總圖' })
  })

  it('手改值優先，之後不被自動值覆蓋，可改回自動計算', async () => {
    const api = open({ 'GET /api/v1/admin/scenarios/baseline': { data: BASELINE } })
    const form = await screen.findByRole('form', { name: '模擬參數' })
    const input = within(form).getByLabelText('基準件數')
    await waitFor(() => expect(input).toHaveValue('4.5'))
    await userEvent.clear(input)
    await userEvent.type(input, '12')
    await userEvent.clear(within(form).getByLabelText('期間（天）'))
    await userEvent.type(within(form).getByLabelText('期間（天）'), '30')
    await waitFor(() => expect(api.calls.some((c) => c.path.includes('window_days=30'))).toBe(true))
    expect(input).toHaveValue('12')

    await fillMeasure(1, { name: '加裝路燈', low: '20', high: '40' })
    await userEvent.click(within(form).getByRole('button', { name: '執行模擬' }))
    await screen.findByRole('region', { name: '模擬結果' })
    const post = api.calls.find((c) => c.method === 'POST' && c.path === '/api/v1/admin/scenarios')
    expect(post?.body).toMatchObject({ baseline: 12, window_days: 30 })

    await userEvent.click(within(form).getByRole('button', { name: '改回自動計算' }))
    expect(input).toHaveValue('4.5')
  })

  it('沒有歷史資料時提示手動填寫', async () => {
    open({ 'GET /api/v1/admin/scenarios/baseline': { data: { ...BASELINE, baseline: null, observed: 0, weeks: 0, history_start: null, history_end: null } } })
    const form = await screen.findByRole('form', { name: '模擬參數' })
    expect(await within(form).findByText(/沒有歷史資料可以推算/)).toBeInTheDocument()
    expect(within(form).getByLabelText('基準件數')).toHaveValue('')
  })
})
