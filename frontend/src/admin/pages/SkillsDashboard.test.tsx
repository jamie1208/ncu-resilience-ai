import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { SKILLS } from '../../test/fixtures'
import { ME, mockApi } from '../../test/mockApi'

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('SkillsDashboard', () => {
  it('每個 Skill 顯示版本、近 7 天執行數、失敗率、費用與評測結果', async () => {
    window.history.pushState({}, '', '/admin/skills')
    mockApi({ 'GET /api/v1/auth/me': ME, 'GET /api/v1/admin/skills': { data: SKILLS } })
    render(<App />)

    const card = (await screen.findByRole('heading', { name: 'event-classifier' })).closest('article')!
    const c = within(card)
    expect(c.getByText('v1.2.0')).toBeInTheDocument()
    expect(c.getByText('12')).toBeInTheDocument()
    expect(c.getByText('8.3%')).toBeInTheDocument()
    expect(c.getByText('$0.0421')).toBeInTheDocument()
    expect(c.getByText(/1\.1\.0、1\.2\.0/)).toBeInTheDocument()
    expect(c.getByText('評測通過')).toBeInTheDocument()
    expect(c.getByText(/gpt-5-mini/)).toBeInTheDocument()
    expect(c.getByText('27／28')).toBeInTheDocument()
    expect(c.getByText('macro_f1')).toBeInTheDocument()
    expect(c.getByText(/0\.91.*≥ 0\.85/)).toBeInTheDocument()

    const idle = within(screen.getByRole('heading', { name: 'risk-assessor' }).closest('article')!)
    expect(idle.getByText('尚無此版本的評測報告')).toBeInTheDocument()
  })

  it('評測未通過以 danger 標籤與文字標示', async () => {
    window.history.pushState({}, '', '/admin/skills')
    const failing = [{ ...SKILLS[0], eval: { ...SKILLS[0].eval!, passed: false, mode: 'recorded' } }]
    mockApi({ 'GET /api/v1/auth/me': ME, 'GET /api/v1/admin/skills': { data: failing } })
    render(<App />)
    const badge = await screen.findByText('評測未通過')
    expect(badge.className).toContain('bg-danger-muted')
    expect(screen.getByText(/錄製回應/)).toBeInTheDocument()
  })
})
