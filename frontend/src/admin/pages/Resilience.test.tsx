import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { ME, mockApi } from '../../test/mockApi'
import { RESILIENCE, RESILIENCE_PARTIAL } from '../../test/resilienceFixtures'
import { matchRoute } from '../router'

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

function open(data: unknown) {
  const api = mockApi({ 'GET /api/v1/auth/me': ME, 'GET /api/v1/admin/resilience': { data } })
  window.history.pushState({}, '', '/admin/resilience')
  render(<App />)
  return api
}

describe('後台韌性成績單', () => {
  it('路由與導覽', async () => {
    expect(matchRoute('/admin/resilience')).toEqual({ page: 'resilience' })
    open(RESILIENCE)
    const nav = await screen.findByRole('navigation', { name: '主要頁面' })
    expect(within(nav).getByRole('link', { name: '韌性成績單' })).toHaveAttribute('aria-current', 'page')
  })

  it('顯示總分、各構面分數、樣本量、覆蓋率與指標明細', async () => {
    open(RESILIENCE)
    const overall = await screen.findByRole('region', { name: '總分' })
    expect(within(overall).getByText('72')).toBeInTheDocument()
    const response = screen.getByRole('article', { name: '回應' })
    expect(within(response).getAllByText('64')).toHaveLength(2) // 構面與指標
    expect(within(response).getByText('11.5 天')).toBeInTheDocument()
    expect(within(response).getByText(/9／24/)).toBeInTheDocument()
    expect(within(response).getByText(/38%/)).toBeInTheDocument()
    const adaptation = screen.getByRole('article', { name: '調適' })
    expect(within(adaptation).getByText('改善完成率')).toBeInTheDocument()
    expect(within(adaptation).getByText('重複事件比例')).toBeInTheDocument()
    expect(within(adaptation).getByText('40%')).toBeInTheDocument()
  })

  it('附時間窗、樣本量、門檻、權重與來源限制', async () => {
    open(RESILIENCE)
    await screen.findByRole('region', { name: '總分' })
    expect(screen.getByText(/2026-05-02 至 2026-10-28（180 天）/)).toBeInTheDocument()
    expect(screen.getByText(/事件 24 件、改善措施 11 項/)).toBeInTheDocument()
    expect(screen.getByText(/每項指標至少 5 筆/)).toBeInTheDocument()
    expect(screen.getByText(/各構面權重 0\.25/)).toBeInTheDocument()
    const limits = screen.getByRole('region', { name: '資料限制' })
    expect(within(limits).getByText(/回報變多不等於校園變危險/)).toBeInTheDocument()
  })

  it('樣本不足的構面顯示 N/A，總分也是 N/A', async () => {
    open(RESILIENCE_PARTIAL)
    const overall = await screen.findByRole('region', { name: '總分' })
    expect(within(overall).getByText('N/A')).toBeInTheDocument()
    const recovery = screen.getByRole('article', { name: '恢復' })
    expect(within(recovery).getAllByText('N/A').length).toBeGreaterThan(0)
    expect(within(recovery).getByText(/樣本不足/)).toBeInTheDocument()
  })

  it('可切換時間窗', async () => {
    const api = open(RESILIENCE)
    await screen.findByRole('region', { name: '總分' })
    await userEvent.selectOptions(screen.getByLabelText('時間窗'), '90')
    await vi.waitFor(() =>
      expect(api.calls.some((c) => c.path === '/api/v1/admin/resilience?window_days=90')).toBe(true),
    )
  })
})
