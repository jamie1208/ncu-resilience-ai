import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { AUDIT_LOGS, DASHBOARD, INGESTION_RUNS, SOURCE_HEALTH } from '../../test/fixtures'
import { ME, mockApi } from '../../test/mockApi'

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

const ROUTES = {
  'GET /api/v1/auth/me': ME,
  'GET /api/v1/admin/dashboard': { data: DASHBOARD },
  'GET /api/v1/admin/ingestion-runs/health': { data: SOURCE_HEALTH },
  'GET /api/v1/admin/ingestion-runs?limit=20': { data: INGESTION_RUNS },
  'GET /api/v1/admin/audit-logs?limit=20': { data: AUDIT_LOGS },
}

describe('Ops', () => {
  it('顯示儀表板摘要、來源健康、擷取紀錄與稽核紀錄', async () => {
    window.history.pushState({}, '', '/admin/ops')
    mockApi(ROUTES)
    render(<App />)

    const summary = await screen.findByRole('region', { name: '摘要' })
    expect(await within(summary).findByText('近 7 天新事件')).toBeInTheDocument()
    expect(within(summary).getByText('4')).toBeInTheDocument()
    expect(within(summary).getByRole('link', { name: /工程一館停電/ })).toBeInTheDocument()

    const health = screen.getByRole('region', { name: '來源健康' })
    await within(health).findByText('氣象署警特報')
    const failing = within(health).getByText('氣象署警特報').closest('tr')!
    expect(within(failing).getByText('失敗')).toBeInTheDocument()
    expect(within(failing).getByText('4')).toBeInTheDocument()
    expect(within(failing).getByText('95.0%')).toBeInTheDocument()

    const runs = screen.getByRole('region', { name: '最近擷取' })
    await within(runs).findByText('cwa_warnings')
    expect(within(runs).getByText('cwa_warnings')).toBeInTheDocument()
    expect(within(runs).getByText('2026-10-21 08:10')).toBeInTheDocument()

    const audit = screen.getByRole('region', { name: '稽核紀錄' })
    await within(audit).findByText('event.verify')
    expect(within(audit).getByText('event.verify')).toBeInTheDocument()
    expect(within(audit).getByText('admin:jamie')).toBeInTheDocument()
  })

  it('單一區塊失敗時其他區塊照常顯示', async () => {
    window.history.pushState({}, '', '/admin/ops')
    mockApi({
      ...ROUTES,
      'GET /api/v1/admin/audit-logs?limit=20': {
        status: 403,
        error: { code: 'FORBIDDEN', message: '權限不足' },
      },
    })
    render(<App />)
    const audit = await screen.findByRole('region', { name: '稽核紀錄' })
    expect(await within(audit).findByRole('alert')).toHaveTextContent('權限不足')
    expect(await screen.findByText('氣象署警特報')).toBeInTheDocument()
  })
})
