/**
 * 播報員語氣的例外與禁用項（P8-T10，ADR-0018 3-2、ADR-0020 決策 5，`docs/governance/tone-guide.md`）：
 * 緊急資訊與「尚未查證」文案維持平實、逐字不變；介面原始碼不得出現表情符號。
 */
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Disclaimer } from './admin/ui'
import { PublicApp } from './public/PublicApp'
import { SiteFooter, VerificationTag } from './public/ui'
import { mockApi } from './test/mockApi'
import { PUBLIC_SERIES, PUBLIC_SERIES_ID, PUBLIC_UNVERIFIED } from './test/publicFixtures'

const EMERGENCY_FOOTER =
  '緊急狀況請直接撥打 110（警察）或 119（消防、救護），或聯絡國立中央大學校安中心；本站不是緊急通報管道。'
const EMERGENCY_ADMIN =
  '緊急狀況請直接撥打 110（警察）或 119（消防、救護），或聯絡學校校安中心；本系統不是緊急通報管道。'
const EMERGENCY_REPORT = '這裡不是報案管道。遇到緊急狀況請直接撥打 110 或 119，或聯絡學校校安中心。'
const EMERGENCY_HOME = '看到怪怪的事？匿名跟我們說。這裡不是報案管道，緊急狀況請撥 110 或 119。'
const UNVERIFIED_NOTE = '尚未查證：這是 AI 依公開來源整理的摘要，目前沒有官方來源證實，也還沒有人工查證。'
const UNVERIFIED_HOME = '標示「尚未查證」的內容是 AI 依公開來源整理的摘要，不代表事情確實發生。'
const HYPOTHESIS_NOTE = '規律與可能原因是 AI 依公開資料整理的推測，標示「推測，尚未查證」的內容不代表事實；件數由程式計算。'

function at(path: string) {
  window.history.pushState({}, '', path)
}

afterEach(() => {
  at('/')
  vi.unstubAllGlobals()
})

describe('緊急資訊維持平實', () => {
  it('公開頁尾的緊急聯絡文字逐字不變', () => {
    render(<SiteFooter />)
    expect(screen.getByText(EMERGENCY_FOOTER)).toBeVisible()
  })

  it('後台的緊急聯絡文字逐字不變', () => {
    render(<Disclaimer />)
    expect(screen.getByText(EMERGENCY_ADMIN)).toBeVisible()
  })

  it('匿名回報頁寫明不是報案管道並列出 110、119', async () => {
    mockApi({})
    at('/report')
    render(<PublicApp />)
    expect(await screen.findByText(EMERGENCY_REPORT)).toBeVisible()
  })

  it('首頁的匿名回報入口寫明不是報案管道', async () => {
    mockApi({ 'GET /api/v1/public/feed?limit=50': { data: [], meta: { next_cursor: null } } })
    render(<PublicApp />)
    expect(await screen.findByText(EMERGENCY_HOME)).toBeVisible()
  })
})

describe('「尚未查證」維持平實', () => {
  it('查證標籤照後端文字顯示，不加修飾', () => {
    render(<VerificationTag verification="unverified" text="尚未查證" />)
    expect(screen.getByText('尚未查證')).toBeVisible()
  })

  it('首頁說明「尚未查證」的句子逐字不變', async () => {
    mockApi({ 'GET /api/v1/public/feed?limit=50': { data: [], meta: { next_cursor: null } } })
    render(<PublicApp />)
    expect(await screen.findByText(UNVERIFIED_HOME)).toBeVisible()
  })

  it('快報全文的「尚未查證」說明逐字不變', async () => {
    mockApi({
      [`GET /api/v1/public/events/${PUBLIC_UNVERIFIED.id}`]: { data: { ...PUBLIC_UNVERIFIED, report: null, sources: [] } },
    })
    at(`/events/${PUBLIC_UNVERIFIED.id}`)
    render(<PublicApp />)
    expect(await screen.findByText(UNVERIFIED_NOTE)).toBeVisible()
  })

  it('系列分析頁的推測說明與「推測，尚未查證」標籤逐字不變', async () => {
    mockApi({ [`GET /api/v1/public/series/${PUBLIC_SERIES_ID}`]: { data: PUBLIC_SERIES } })
    at(`/series/${PUBLIC_SERIES_ID}`)
    render(<PublicApp />)
    expect(await screen.findByText(HYPOTHESIS_NOTE)).toBeVisible()
    expect(screen.getAllByText('推測，尚未查證').length).toBeGreaterThan(0)
  })
})

describe('不使用表情符號', () => {
  const sources = import.meta.glob(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}', '!./test/**'], {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>
  // 箭頭（U+2190–21FF，如 ↔ →）是文字符號，用於關聯方向，不算表情符號
  const EMOJI = /(?![\u{2190}-\u{21FF}])[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}]|\u{FE0F}/u

  it('有掃到介面原始碼', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(20)
  })

  it.each(Object.keys(sources))('%s 沒有表情符號', (path) => {
    expect(sources[path]).not.toMatch(EMOJI)
  })
})
