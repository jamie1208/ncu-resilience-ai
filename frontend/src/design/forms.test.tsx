import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { BadgeTone, DesignLibrary } from './contract'
import * as minimal from './minimal'
import * as playful from './playful'
import type { DesignName } from './registry'

// P3-T05 管理前端用的 Badge 與表單元件：兩套設計共同的行為要求
const libraries: Record<DesignName, DesignLibrary> = { minimal, playful }
// 觸控高度：minimal 至少 44px（h-11），playful 至少 48px（h-12）
const minHeight: Record<DesignName, RegExp> = { minimal: /\bh-1[1-6]\b/, playful: /\bh-1[2-6]\b/ }

const TONE_CLASS: Record<BadgeTone, string> = {
  neutral: 'bg-muted',
  info: 'bg-info-muted',
  success: 'bg-success-muted',
  warning: 'bg-warning-muted',
  danger: 'bg-danger-muted',
}

describe.each(Object.keys(libraries) as DesignName[])('%s 管理端元件', (name) => {
  const { Badge, Input, Select, Textarea } = libraries[name]

  it('Badge 依 tone 使用語意色，預設 neutral，文字照常顯示', () => {
    const { unmount } = render(<Badge>未查證</Badge>)
    expect(screen.getByText('未查證').className).toContain('bg-muted')
    unmount()
    for (const [tone, cls] of Object.entries(TONE_CLASS) as [BadgeTone, string][]) {
      const { unmount: u } = render(<Badge tone={tone}>等級</Badge>)
      expect(screen.getByText('等級').className).toContain(cls)
      u()
    }
  })

  it('Input、Select 觸控高度足夠且有可見 focus ring，可接收 label', () => {
    render(
      <>
        <label>
          帳號
          <Input name="username" />
        </label>
        <label>
          狀態
          <Select defaultValue="b">
            <option value="a">A</option>
            <option value="b">B</option>
          </Select>
        </label>
      </>,
    )
    const input = screen.getByLabelText('帳號')
    const select = screen.getByLabelText('狀態')
    for (const el of [input, select]) {
      expect(el.className).toMatch(minHeight[name])
      expect(el.className).toContain('focus-visible:ring-2')
    }
    expect(select).toHaveValue('b')
  })

  it('Textarea 有可見 focus ring 並保留傳入的屬性', () => {
    render(<Textarea aria-label="備註" maxLength={200} />)
    const area = screen.getByLabelText('備註')
    expect(area).toHaveAttribute('maxLength', '200')
    expect(area.className).toContain('focus-visible:ring-2')
  })
})
