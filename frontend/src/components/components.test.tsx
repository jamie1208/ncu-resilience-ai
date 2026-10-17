import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Button } from './Button'
import { SectionLabel } from './SectionLabel'

describe('Button', () => {
  it('預設為 primary，觸控高度至少 44px（h-11 以上）並有可見 focus ring', () => {
    render(<Button>查看事件</Button>)
    const btn = screen.getByRole('button', { name: '查看事件' })
    expect(btn).toHaveAttribute('type', 'button')
    expect(btn.className).toMatch(/\bh-1[1-4]\b/)
    expect(btn.className).toContain('focus-visible:ring-2')
    expect(btn.className).toContain('from-accent')
  })

  it('variant 與 size 可切換，className 可覆蓋', () => {
    render(
      <Button variant="outline" size="sm" className="w-full">
        取消
      </Button>,
    )
    const btn = screen.getByRole('button', { name: '取消' })
    expect(btn.className).toContain('border-border')
    expect(btn.className).not.toContain('from-accent')
    expect(btn.className).toContain('w-full')
  })
})

describe('SectionLabel', () => {
  it('英文標籤使用全大寫與寬字距', () => {
    render(<SectionLabel>Live updates</SectionLabel>)
    const text = screen.getByText('Live updates')
    expect(text.className).toContain('uppercase')
    expect(text.className).toContain('tracking-[0.15em]')
  })

  it('中文標籤不使用全大寫與寬字距', () => {
    render(<SectionLabel>校園動態</SectionLabel>)
    const text = screen.getByText('校園動態')
    expect(text.className).not.toContain('uppercase')
    expect(text.className).not.toContain('tracking-[0.15em]')
  })
})
