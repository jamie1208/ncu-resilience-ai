import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi } from '../../test/mockApi'
import { PUBLIC_RESILIENCE, RESILIENCE } from '../../test/resilienceFixtures'
import { PublicApp } from '../PublicApp'
import { matchPublicRoute } from '../routes'

const URL = 'GET /api/v1/public/resilience'

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

function open(data: unknown) {
  const api = mockApi({ [URL]: { data } })
  window.history.pushState({}, '', '/resilience')
  render(<PublicApp />)
  return api
}

describe('公開版韌性成績單', () => {
  it('/resilience 是公開頁面，導覽有入口', () => {
    expect(matchPublicRoute('/resilience')).toEqual({ page: 'resilience' })
    open(PUBLIC_RESILIENCE)
    const nav = screen.getByRole('navigation', { name: '主要頁面' })
    const link = within(nav).getByRole('link', { name: '韌性成績單' })
    expect(link).toHaveAttribute('aria-current', 'page')
  })

  it('醒目標明「回報變多不等於校園變危險」', async () => {
    open(PUBLIC_RESILIENCE)
    await screen.findByRole('heading', { level: 1, name: '校園韌性成績單' })
    const note = screen.getByRole('note', { name: '閱讀前請注意' })
    expect(within(note).getByText(/回報變多不等於校園變危險/)).toBeInTheDocument()
  })

  it('資料不足時總分與構面顯示 N/A，不顯示數字', async () => {
    open(PUBLIC_RESILIENCE)
    const overall = await screen.findByRole('region', { name: '總分' })
    expect(within(overall).getByText('N/A')).toBeInTheDocument()
    expect(within(overall).getByText(/資料不足/)).toBeInTheDocument()
    const recovery = screen.getByRole('article', { name: '開始處理後，多久恢復正常？' })
    expect(within(recovery).getByText('N/A')).toBeInTheDocument()
    expect(within(recovery).getByText(/樣本 2 筆/)).toBeInTheDocument()
    expect(within(recovery).getByText(/少於 5 筆/)).toBeInTheDocument()
  })

  it('附時間窗、樣本量、覆蓋率與來源限制', async () => {
    open(PUBLIC_RESILIENCE)
    await screen.findByRole('region', { name: '總分' })
    expect(screen.getByText(/2026-05-02 至 2026-10-28（180 天）/)).toBeInTheDocument()
    expect(screen.getByText(/24 件事件/)).toBeInTheDocument()
    const detection = screen.getByRole('article', { name: '出事後，多快有人知道？' })
    expect(within(detection).getByText('85')).toBeInTheDocument()
    expect(within(detection).getByText(/覆蓋率 83%/)).toBeInTheDocument()
    const limits = screen.getByRole('region', { name: '資料限制' })
    expect(within(limits).getByText('公開版只統計已查證並公開的事件。')).toBeInTheDocument()
  })

  it('白話說明：四個構面改成四個問題，並寫明目標是本專題自訂（ADR-0020 決策 5）', async () => {
    open(PUBLIC_RESILIENCE)
    await screen.findByRole('heading', { level: 1, name: '校園韌性成績單' })
    expect(screen.getByText(/學校遇到狀況時，處理得好不好？/)).toBeInTheDocument()
    expect(screen.getByText(/幾天算好是本專題自己訂的目標，不是學校的官方標準/)).toBeInTheDocument()
    const questions = within(screen.getByRole('list', { name: '四個問題' }))
    for (const q of [
      '出事後，多快有人知道？',
      '知道之後，多快開始處理？',
      '開始處理後，多久恢復正常？',
      '同樣的事，有沒有一再發生？',
    ]) {
      expect(questions.getByRole('article', { name: q })).toBeInTheDocument()
    }
    expect(screen.queryByText(/構面/)).not.toBeInTheDocument()
  })

  it('資料齊全時顯示整數總分', async () => {
    open({ ...RESILIENCE, scope: 'published' })
    const overall = await screen.findByRole('region', { name: '總分' })
    expect(within(overall).getByText('72')).toBeInTheDocument()
  })

  it('載入失敗顯示錯誤', async () => {
    mockApi({ [URL]: { status: 500, error: { code: 'INTERNAL_ERROR', message: '伺服器錯誤' } } })
    window.history.pushState({}, '', '/resilience')
    render(<PublicApp />)
    expect(await screen.findByRole('alert')).toHaveTextContent('伺服器錯誤')
  })
})
