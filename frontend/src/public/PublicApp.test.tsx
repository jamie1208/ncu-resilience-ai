import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  PUBLIC_CATEGORIES,
  PUBLIC_EVENT,
  PUBLIC_EVENT_DETAIL,
  PUBLIC_EVENT_ID,
  PUBLIC_NOTICE,
  PUBLIC_PROGRESS,
  PUBLIC_SERIES,
  PUBLIC_SERIES_ID,
  PUBLIC_UNVERIFIED,
} from '../test/publicFixtures'
import { mockApi } from '../test/mockApi'
import { PublicApp } from './PublicApp'

function at(path: string) {
  window.history.pushState({}, '', path)
}

afterEach(() => {
  at('/')
  vi.unstubAllGlobals()
})

const ROUTES = {
  'GET /api/v1/public/feed?limit=50': {
    data: [PUBLIC_EVENT, PUBLIC_UNVERIFIED, PUBLIC_NOTICE],
    meta: { next_cursor: null },
  },
  'GET /api/v1/public/feed': { data: [PUBLIC_EVENT, PUBLIC_NOTICE], meta: { next_cursor: null } },
  'GET /api/v1/public/notices': { data: [PUBLIC_NOTICE], meta: { next_cursor: null } },
  'GET /api/v1/public/categories': { data: PUBLIC_CATEGORIES },
  'GET /api/v1/public/events': { data: [PUBLIC_EVENT], meta: { next_cursor: null } },
  [`GET /api/v1/public/events/${PUBLIC_EVENT_ID}`]: { data: PUBLIC_EVENT_DETAIL },
  'GET /api/v1/public/progress': { data: [PUBLIC_PROGRESS], meta: { next_cursor: null } },
  [`GET /api/v1/public/series/${PUBLIC_SERIES_ID}`]: { data: PUBLIC_SERIES },
}

function footer() {
  return screen.getByRole('contentinfo')
}

describe('PublicApp', () => {
  it.each(['/', '/events', `/events/${PUBLIC_EVENT_ID}`, `/series/${PUBLIC_SERIES_ID}`, '/progress', '/about', '/nope'])(
    '%s 的頁尾標明非官方並列出校安中心、110、119',
    async (path) => {
      mockApi(ROUTES)
      at(path)
      render(<PublicApp />)
      await screen.findByRole('heading', { level: 1 })
      const foot = within(footer())
      expect(foot.getByText(/個人專題，非國立中央大學官方系統/)).toBeVisible()
      expect(foot.getByText(/校安中心/)).toBeVisible()
      expect(foot.getByText(/110/)).toBeVisible()
      expect(foot.getByText(/119/)).toBeVisible()
    },
  )

  it('首頁依類別分區顯示快報卡片：查證標籤、系列第 N 起、連到快報全文（ADR-0020 決策 4）', async () => {
    const { calls } = mockApi(ROUTES)
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '最新校園快報' })).toBeInTheDocument()

    const facility = await screen.findByRole('list', { name: '設施快報' })
    const [card] = within(facility).getAllByRole('listitem')
    const item = within(card)
    expect(item.getByRole('link', { name: '工程五館外牆整修施工' })).toHaveAttribute('href', `/events/${PUBLIC_EVENT_ID}`)
    expect(item.getByText('已查證')).toBeInTheDocument()
    expect(item.getByText('工程五館施工封路 系列 · 第 2 起')).toBeInTheDocument()
    expect(item.getByText(/2026-11-02・工程五館/)).toBeInTheDocument()

    const security = within(screen.getByRole('list', { name: '治安快報' }))
    // 未查證的快報公開 AI 摘要，標「尚未查證」，不得呈現為已查證
    expect(security.getByText('尚未查證')).toBeInTheDocument()
    expect(security.queryByText('已查證')).not.toBeInTheDocument()
    expect(security.getByText('有同學反映宿舍區腳踏車被偷。')).toBeInTheDocument()
    expect(security.getByText('單一事件')).toBeInTheDocument()

    // 每區一列，可以左右滑（scroll-snap），區塊標題有類別名稱
    expect(facility.className).toMatch(/overflow-x-auto/)
    expect(facility.className).toMatch(/snap-x/)
    expect(screen.getByRole('heading', { level: 2, name: /設施/ })).toBeInTheDocument()
    // 舊的安全提醒不再出現在首頁
    expect(screen.queryByText(/目前無法確認是否屬實/)).not.toBeInTheDocument()

    // 底部：校園韌性成績單與匿名回報入口
    const main = within(screen.getByRole('main'))
    expect(main.getByRole('link', { name: '校園韌性成績單' })).toHaveAttribute('href', '/resilience')
    expect(main.getByRole('link', { name: '匿名回報' })).toHaveAttribute('href', '/report')

    expect(calls.map((c) => c.path)).toContain('/api/v1/public/feed?limit=50')
    // 公開前端不呼叫任何管理或登入 API
    expect(calls.every((c) => c.path.startsWith('/api/v1/public/'))).toBe(true)
  })

  it('首頁沒有公開快報時顯示空狀態，API 失敗時顯示錯誤', async () => {
    mockApi({ 'GET /api/v1/public/feed?limit=50': { data: [], meta: { next_cursor: null } } })
    const { unmount } = render(<PublicApp />)
    expect(await screen.findByText(/目前沒有公開的快報/)).toBeInTheDocument()
    unmount()

    mockApi({
      'GET /api/v1/public/feed?limit=50': {
        status: 429,
        error: { code: 'RATE_LIMITED', message: '請求太頻繁，請稍後再試' },
      },
    })
    render(<PublicApp />)
    expect(await screen.findByRole('alert')).toHaveTextContent('請求太頻繁，請稍後再試')
  })

  it('事件查詢有下一頁時可載入更多（cursor 分頁），新項目接在後面', async () => {
    const second = { ...PUBLIC_EVENT, id: 'second', title: '宵夜街路燈修復' }
    const { calls } = mockApi({
      ...ROUTES,
      'GET /api/v1/public/events': { data: [PUBLIC_EVENT], meta: { next_cursor: 'c1' } },
      'GET /api/v1/public/events?cursor=c1': { data: [second], meta: { next_cursor: null } },
    })
    at('/events')
    render(<PublicApp />)
    const user = userEvent.setup()
    await screen.findByRole('link', { name: /工程五館外牆整修施工/ })
    await user.click(screen.getByRole('button', { name: '載入更多' }))
    expect(await screen.findByRole('link', { name: /宵夜街路燈修復/ })).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: '公開事件' })).getAllByRole('listitem')).toHaveLength(2)
    expect(calls.some((c) => c.path === '/api/v1/public/events?cursor=c1')).toBe(true)
    expect(screen.queryByRole('button', { name: '載入更多' })).not.toBeInTheDocument()
  })

  it('事件分類查詢：列出類別與數量，選擇類別後依類別查詢', async () => {
    const { calls } = mockApi({
      ...ROUTES,
      'GET /api/v1/public/events?type=traffic': { data: [], meta: { next_cursor: null } },
    })
    at('/events')
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '事件分類查詢' })).toBeInTheDocument()
    const categories = await screen.findByRole('group', { name: '事件類別' })
    expect(within(categories).getByRole('button', { name: /全部/ })).toHaveAttribute('aria-pressed', 'true')
    expect(within(categories).getByRole('button', { name: /設施.*1/ })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: /工程五館外牆整修施工/ })).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(within(categories).getByRole('button', { name: /交通/ }))
    expect(within(categories).getByRole('button', { name: /交通/ })).toHaveAttribute('aria-pressed', 'true')
    expect(await screen.findByText(/這個類別目前沒有公開的快報/)).toBeInTheDocument()
    expect(calls.some((c) => c.path === '/api/v1/public/events?type=traffic')).toBe(true)
  })

  it('事件頁是快報全文：標題、摘要、地點、查證標籤、官方來源、系列第 N 起；不顯示小報告（P8-T13）', async () => {
    mockApi(ROUTES)
    at(`/events/${PUBLIC_EVENT_ID}`)
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '工程五館外牆整修施工' })).toBeInTheDocument()
    expect(screen.getByText('工程五館外牆整修，施工期間東側出入口封閉。')).toBeInTheDocument()
    expect(screen.getByText(/工程五館/, { selector: 'dd' })).toBeInTheDocument()

    expect(screen.getByText('快報全文')).toBeInTheDocument()
    expect(screen.getByText('已查證')).toBeInTheDocument()

    // 舊快照可能仍帶小報告；快報全文不顯示（ADR-0020 決策 3-5）
    expect(screen.queryByRole('region', { name: '可能的影響' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '建議' })).not.toBeInTheDocument()
    expect(screen.queryByText('東側出入口封閉，需改走西側。')).not.toBeInTheDocument()
    expect(screen.queryByText('改由西側出入口進出')).not.toBeInTheDocument()

    const sources = screen.getByRole('region', { name: '官方來源' })
    const link = within(sources).getByRole('link', { name: /中央大學公告/ })
    expect(link).toHaveAttribute('href', 'https://www.ncu.edu.tw/announcements/a01')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))

    // 改善進度移到系列分析頁；快報全文只連到系列
    expect(screen.queryByRole('region', { name: '改善進度' })).not.toBeInTheDocument()
    const series = screen.getByRole('region', { name: '這是「工程五館施工封路」系列的第 2 起' })
    const user = userEvent.setup()
    await user.click(within(series).getByRole('button', { name: '看這系列的分析' }))
    expect(window.location.pathname).toBe(`/series/${PUBLIC_SERIES_ID}`)
    expect(window.location.search).toBe(`?from=${PUBLIC_EVENT_ID}`)
    expect(await screen.findByRole('heading', { level: 1, name: '工程五館施工封路' })).toBeInTheDocument()
  })

  it('未查證的快報全文標「尚未查證」並說明，附匿名回報入口', async () => {
    mockApi({
      [`GET /api/v1/public/events/${PUBLIC_UNVERIFIED.id}`]: {
        data: { ...PUBLIC_UNVERIFIED, report: null, sources: [] },
      },
    })
    at(`/events/${PUBLIC_UNVERIFIED.id}`)
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '宿舍區腳踏車失竊' })).toBeInTheDocument()
    expect(screen.getByText('尚未查證')).toBeInTheDocument()
    expect(screen.queryByText('已查證')).not.toBeInTheDocument()
    expect(screen.getByText(/目前沒有官方來源證實/)).toBeInTheDocument()
    expect(screen.getByText('單一事件')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '你也遇到類似的事？匿名告訴我們' })).toHaveAttribute('href', '/report')
  })

  it('事件頁不顯示非 http(s) 的來源連結', async () => {
    mockApi({
      [`GET /api/v1/public/events/${PUBLIC_EVENT_ID}`]: {
        data: { ...PUBLIC_EVENT_DETAIL, sources: [{ name: '可疑來源', url: 'javascript:alert(1)' }] },
      },
    })
    at(`/events/${PUBLIC_EVENT_ID}`)
    render(<PublicApp />)
    const sources = await screen.findByRole('region', { name: '官方來源' })
    expect(within(sources).getByText('可疑來源')).toBeInTheDocument()
    expect(within(sources).queryByRole('link')).not.toBeInTheDocument()
  })

  it('事件頁沒有小報告時照常顯示快報，不提小報告；找不到時顯示已下架或不存在', async () => {
    mockApi({
      [`GET /api/v1/public/events/${PUBLIC_EVENT_ID}`]: {
        data: { ...PUBLIC_EVENT_DETAIL, report: null, sources: [] },
      },
    })
    at(`/events/${PUBLIC_EVENT_ID}`)
    const { unmount } = render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '工程五館外牆整修施工' })).toBeInTheDocument()
    expect(screen.queryByText(/小報告/)).not.toBeInTheDocument()
    unmount()

    mockApi({})
    at('/events/missing')
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '找不到這個事件' })).toBeInTheDocument()
    expect(screen.getByText(/可能已經下架/)).toBeInTheDocument()
  })

  it('改善進度：以系列為單位列出措施與狀態，系列可點進分析頁', async () => {
    mockApi(ROUTES)
    at('/progress')
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '改善進度' })).toBeInTheDocument()
    const link = await screen.findByRole('link', { name: '工程五館施工封路' })
    expect(link).toHaveAttribute('href', `/series/${PUBLIC_SERIES_ID}`)
    const item = within(link.closest('li') as HTMLElement)
    expect(item.getByText('張貼改道指示')).toBeInTheDocument()
    expect(item.getByText('處理中')).toBeInTheDocument()
    expect(item.getByText('加裝夜間照明')).toBeInTheDocument()
    expect(item.getByText('已改進')).toBeInTheDocument()
    expect(item.getByText(/預計完成 2026-11-05/)).toBeInTheDocument()
  })

  it('系列分析頁：規律與原因一律帶標籤、建議分對象、前後比較只並列平均並附說明、標出剛剛看的那一則', async () => {
    const { calls } = mockApi(ROUTES)
    at(`/series/${PUBLIC_SERIES_ID}?from=${PUBLIC_EVENT_ID}`)
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '工程五館施工封路' })).toBeInTheDocument()
    expect(screen.getByText('工程五館周邊施工，出入口陸續封閉。')).toBeInTheDocument()
    expect(screen.getByText('2 起', { selector: 'dd' })).toBeInTheDocument()

    const weekly = within(screen.getByRole('region', { name: '每週件數' }))
    expect(weekly.getByRole('img', { name: /每週件數.*共 2 起/ })).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(weekly.getByRole('button', { name: '1 個月' }))
    expect(weekly.getByRole('button', { name: '1 個月' })).toHaveAttribute('aria-pressed', 'true')

    const patterns = within(screen.getByRole('region', { name: 'AI 看到的規律' }))
    expect(patterns.getByText('封閉多在平日白天施工時段')).toBeInTheDocument()
    expect(patterns.getByText('推測，尚未查證')).toBeInTheDocument()
    expect(patterns.getByText('2 起中有 2 起')).toBeInTheDocument()

    const causes = within(screen.getByRole('region', { name: '可能原因' }))
    expect(causes.getByText('外牆整修工程分段進行')).toBeInTheDocument()
    expect(causes.getByText('已確認')).toBeInTheDocument()
    expect(causes.getByText('施工告示張貼位置不明顯')).toBeInTheDocument()
    expect(causes.getByText('推測，尚未查證')).toBeInTheDocument()

    const measures = within(screen.getByRole('region', { name: '改善建議' }))
    expect(measures.getByRole('heading', { name: '給同學' })).toBeInTheDocument()
    expect(measures.getByRole('heading', { name: '給學校' })).toBeInTheDocument()
    expect(measures.getByText('在出入口張貼改道指示')).toBeInTheDocument()

    expect(within(screen.getByRole('region', { name: '改善追蹤' })).getByText('張貼改道指示')).toBeInTheDocument()
    const compare = within(screen.getByRole('region', { name: '改善前後比較' }))
    expect(compare.getByText('資料還不夠')).toBeInTheDocument()
    expect(compare.getByText(/變少不一定是措施的功勞/)).toBeInTheDocument()
    expect(compare.queryByText(/%/)).not.toBeInTheDocument()

    const events = within(screen.getByRole('region', { name: '這個系列的每一起' }))
    const items = events.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[1]).toHaveAttribute('aria-current', 'true')
    expect(within(items[1]).getByText('你剛剛看的')).toBeInTheDocument()
    expect(within(items[0]).getByText('尚未查證')).toBeInTheDocument()
    expect(within(items[1]).getByRole('link', { name: '工程五館外牆整修施工' })).toHaveAttribute(
      'href',
      `/events/${PUBLIC_EVENT_ID}`,
    )
    expect(calls.every((c) => c.path.startsWith('/api/v1/public/'))).toBe(true)
  })

  it('單一事件的系列頁：規律要至少 2 起；找不到時顯示已下架或不存在', async () => {
    const single = {
      ...PUBLIC_SERIES,
      event_count: 1,
      events: [PUBLIC_SERIES.events[1]],
      patterns: [],
      pattern_status: 'abstained' as const,
      pattern_note: '要至少 2 起才會找規律',
      comparisons: [],
    }
    mockApi({ [`GET /api/v1/public/series/${PUBLIC_SERIES_ID}`]: { data: single } })
    at(`/series/${PUBLIC_SERIES_ID}`)
    const { unmount } = render(<PublicApp />)
    expect(await screen.findByText('單一事件')).toBeInTheDocument()
    expect(screen.getByText('要至少 2 起才會找規律')).toBeInTheDocument()
    expect(screen.getByText('目前只有 1 起。')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '改善前後比較' })).not.toBeInTheDocument()
    unmount()

    mockApi({})
    at('/series/missing')
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '找不到這個系列' })).toBeInTheDocument()
    expect(screen.getByText(/可能已經下架/)).toBeInTheDocument()
  })

  it('關於本站說明非官方身分與公開規則', async () => {
    mockApi({})
    at('/about')
    render(<PublicApp />)
    const main = within(await screen.findByRole('main'))
    expect(main.getByRole('heading', { level: 1, name: '關於本站' })).toBeInTheDocument()
    expect(main.getByText(/不是國立中央大學的官方系統/)).toBeVisible()
    // P8-T06（ADR-0018、ADR-0020）：未查證標「尚未查證」，推測只在系列分析頁且一律標示
    expect(main.getByText(/其他一律標「尚未查證」/)).toBeInTheDocument()
    expect(main.getByText(/只出現在系列分析頁，一律標示「推測，尚未查證」/)).toBeInTheDocument()
    expect(main.getByText(/不顯示社群貼文原文/)).toBeInTheDocument()
    expect(main.getByText(/匿名回報原文永不公開/)).toBeInTheDocument()
  })

  it('導覽列切換頁面並標示目前頁面；沒有管理後台或登入連結', async () => {
    mockApi(ROUTES)
    render(<PublicApp />)
    const user = userEvent.setup()
    const nav = await screen.findByRole('navigation', { name: '主要頁面' })
    await user.click(within(nav).getByRole('link', { name: '改善進度' }))
    expect(await screen.findByRole('heading', { level: 1, name: '改善進度' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/progress')
    expect(within(nav).getByRole('link', { name: '改善進度' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('link', { name: /登入|管理/ })).not.toBeInTheDocument()
    expect(document.querySelector('a[href^="/admin"]')).toBeNull()
  })

  it('不存在的路徑顯示找不到頁面', async () => {
    mockApi({})
    at('/nope')
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '找不到頁面' })).toBeInTheDocument()
  })
})
