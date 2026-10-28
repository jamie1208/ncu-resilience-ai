import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  PUBLIC_CATEGORIES,
  PUBLIC_EVENT,
  PUBLIC_EVENT_DETAIL,
  PUBLIC_EVENT_ID,
  PUBLIC_NOTICE,
  PUBLIC_PROGRESS,
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
  'GET /api/v1/public/feed': { data: [PUBLIC_EVENT, PUBLIC_NOTICE], meta: { next_cursor: null } },
  'GET /api/v1/public/notices': { data: [PUBLIC_NOTICE], meta: { next_cursor: null } },
  'GET /api/v1/public/categories': { data: PUBLIC_CATEGORIES },
  'GET /api/v1/public/events': { data: [PUBLIC_EVENT], meta: { next_cursor: null } },
  [`GET /api/v1/public/events/${PUBLIC_EVENT_ID}`]: { data: PUBLIC_EVENT_DETAIL },
  'GET /api/v1/public/progress': { data: [PUBLIC_PROGRESS], meta: { next_cursor: null } },
}

function footer() {
  return screen.getByRole('contentinfo')
}

describe('PublicApp', () => {
  it.each(['/', '/events', `/events/${PUBLIC_EVENT_ID}`, '/progress', '/about', '/nope'])(
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

  it('首頁顯示最新動態：事件可點進小報告，安全提醒標明未經查證、沒有連結', async () => {
    const { calls } = mockApi(ROUTES)
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: /最新動態/ })).toBeInTheDocument()
    const feed = await screen.findByRole('list', { name: '最新動態' })
    const items = within(feed).getAllByRole('listitem')
    expect(items).toHaveLength(2)

    const event = within(items[0])
    expect(event.getByRole('link', { name: /工程五館外牆整修施工/ })).toHaveAttribute(
      'href',
      `/events/${PUBLIC_EVENT_ID}`,
    )
    expect(event.getByText('已查證')).toBeInTheDocument()
    expect(event.getByText('設施')).toBeInTheDocument()
    expect(event.getByText(/風險 低/)).toBeInTheDocument()
    // published_at 轉台北時間
    expect(event.getByText(/2026-11-02 09:30/)).toBeInTheDocument()

    const notice = within(items[1])
    expect(notice.getByText('安全提醒')).toBeInTheDocument()
    expect(notice.getByText(/^未經查證・/)).toBeInTheDocument()
    expect(notice.getByText(/目前無法確認是否屬實/)).toBeInTheDocument()
    expect(notice.queryByRole('link')).not.toBeInTheDocument()

    expect(calls.map((c) => c.path)).toContain('/api/v1/public/feed')
    // 公開前端不呼叫任何管理或登入 API
    expect(calls.every((c) => c.path.startsWith('/api/v1/public/'))).toBe(true)
  })

  it('首頁可切換成只看安全提醒', async () => {
    const { calls } = mockApi(ROUTES)
    render(<PublicApp />)
    const user = userEvent.setup()
    await screen.findByRole('list', { name: '最新動態' })
    await user.click(screen.getByRole('button', { name: '只看安全提醒' }))
    expect(screen.getByRole('button', { name: '只看安全提醒' })).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(calls.at(-1)?.path).toBe('/api/v1/public/notices'))
    const list = await screen.findByRole('list', { name: '安全提醒' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
  })

  it('有下一頁時可載入更多（cursor 分頁），新項目接在後面', async () => {
    const second = { ...PUBLIC_EVENT, id: 'second', title: '宵夜街路燈修復' }
    const { calls } = mockApi({
      ...ROUTES,
      'GET /api/v1/public/feed': { data: [PUBLIC_EVENT], meta: { next_cursor: 'c1' } },
      'GET /api/v1/public/feed?cursor=c1': { data: [second], meta: { next_cursor: null } },
    })
    render(<PublicApp />)
    const user = userEvent.setup()
    await screen.findByRole('link', { name: /工程五館外牆整修施工/ })
    await user.click(screen.getByRole('button', { name: '載入更多' }))
    expect(await screen.findByRole('link', { name: /宵夜街路燈修復/ })).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: '最新動態' })).getAllByRole('listitem')).toHaveLength(2)
    expect(calls.some((c) => c.path === '/api/v1/public/feed?cursor=c1')).toBe(true)
    expect(screen.queryByRole('button', { name: '載入更多' })).not.toBeInTheDocument()
  })

  it('沒有公開內容時顯示空狀態，API 失敗時顯示錯誤', async () => {
    mockApi({ 'GET /api/v1/public/feed': { data: [], meta: { next_cursor: null } } })
    const { unmount } = render(<PublicApp />)
    expect(await screen.findByText(/目前沒有公開的動態/)).toBeInTheDocument()
    unmount()

    mockApi({
      'GET /api/v1/public/feed': {
        status: 429,
        error: { code: 'RATE_LIMITED', message: '請求太頻繁，請稍後再試' },
      },
    })
    render(<PublicApp />)
    expect(await screen.findByRole('alert')).toHaveTextContent('請求太頻繁，請稍後再試')
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
    expect(await screen.findByText(/這個類別目前沒有已查證的公開事件/)).toBeInTheDocument()
    // 只有安全提醒的類別提示去首頁看提醒
    expect(screen.getByText(/1 則安全提醒/)).toBeInTheDocument()
    expect(calls.some((c) => c.path === '/api/v1/public/events?type=traffic')).toBe(true)
  })

  it('事件頁顯示公開版小報告：影響、建議、官方來源、改善進度，不出現原因分析', async () => {
    mockApi(ROUTES)
    at(`/events/${PUBLIC_EVENT_ID}`)
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '工程五館外牆整修施工' })).toBeInTheDocument()
    expect(screen.getByText('工程五館外牆整修，施工期間東側出入口封閉。')).toBeInTheDocument()
    expect(screen.getByText(/工程五館/, { selector: 'dd' })).toBeInTheDocument()

    const impacts = screen.getByRole('region', { name: '可能的影響' })
    expect(within(impacts).getByText('營運')).toBeInTheDocument()
    expect(within(impacts).getByText('東側出入口封閉，需改走西側。')).toBeInTheDocument()

    const advice = screen.getByRole('region', { name: '建議' })
    expect(within(within(advice).getByRole('group', { name: '同學可以做' })).getByText('改由西側出入口進出')).toBeInTheDocument()
    const school = within(within(advice).getByRole('group', { name: '學校可以做' }))
    expect(school.getByText('在出入口張貼改道指示')).toBeInTheDocument()
    expect(school.getByText(/建議單位：總務處/)).toBeInTheDocument()

    const sources = screen.getByRole('region', { name: '官方來源' })
    const link = within(sources).getByRole('link', { name: /中央大學公告/ })
    expect(link).toHaveAttribute('href', 'https://www.ncu.edu.tw/announcements/a01')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))

    const progress = screen.getByRole('region', { name: '改善進度' })
    expect(within(progress).getByText('張貼改道指示')).toBeInTheDocument()
    expect(within(progress).getByText('處理中')).toBeInTheDocument()

    expect(screen.queryByText(/原因/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /回事件查詢/ })).toHaveAttribute('href', '/events')
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

  it('事件頁在沒有小報告時說明尚未產生；找不到時顯示已下架或不存在', async () => {
    mockApi({
      [`GET /api/v1/public/events/${PUBLIC_EVENT_ID}`]: {
        data: { ...PUBLIC_EVENT_DETAIL, report: null, sources: [], progress: [] },
      },
    })
    at(`/events/${PUBLIC_EVENT_ID}`)
    const { unmount } = render(<PublicApp />)
    expect(await screen.findByText(/尚未產生公開版小報告/)).toBeInTheDocument()
    unmount()

    mockApi({})
    at('/events/missing')
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '找不到這個事件' })).toBeInTheDocument()
    expect(screen.getByText(/可能已經下架/)).toBeInTheDocument()
  })

  it('改善進度：列出各事件的措施與狀態，事件可點進小報告', async () => {
    mockApi(ROUTES)
    at('/progress')
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '改善進度' })).toBeInTheDocument()
    const card = (await screen.findByRole('link', { name: /工程五館外牆整修施工/ })).closest('li')
    expect(card).not.toBeNull()
    const item = within(card as HTMLElement)
    expect(item.getByText('張貼改道指示')).toBeInTheDocument()
    expect(item.getByText('處理中')).toBeInTheDocument()
    expect(item.getByText('加裝夜間照明')).toBeInTheDocument()
    expect(item.getByText('已改進')).toBeInTheDocument()
    expect(item.getByText(/預計完成 2026-11-05/)).toBeInTheDocument()
  })

  it('關於本站說明非官方身分與公開規則', async () => {
    mockApi({})
    at('/about')
    render(<PublicApp />)
    const main = within(await screen.findByRole('main'))
    expect(main.getByRole('heading', { level: 1, name: '關於本站' })).toBeInTheDocument()
    expect(main.getByText(/不是國立中央大學的官方系統/)).toBeVisible()
    expect(main.getByText(/未經查證的事件只公開中性的安全提醒/)).toBeInTheDocument()
    expect(main.getByText(/不公開推測的原因/)).toBeInTheDocument()
    expect(main.getByText(/不顯示社群貼文原文/)).toBeInTheDocument()
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
