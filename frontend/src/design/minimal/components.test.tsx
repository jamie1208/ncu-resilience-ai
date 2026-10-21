import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Button, Card, SectionLabel } from '.'

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

describe('Card', () => {
  it('白底、細框、柔和陰影；預設為 div，as 可指定語意標籤', () => {
    render(
      <ul>
        <Card as="li" data-testid="card">
          內容
        </Card>
      </ul>,
    )
    const card = screen.getByTestId('card')
    expect(card.tagName).toBe('LI')
    expect(card.className).toContain('bg-card')
    expect(card.className).toContain('border-border')
    expect(card.className).toContain('shadow-sm')
  })

  it('featured 使用 accent 邊框；只有 interactive 才有 hover 效果', () => {
    const { rerender } = render(<Card featured data-testid="card">重點</Card>)
    const cls = screen.getByTestId('card').className
    expect(cls).toContain('border-accent/30')
    expect(cls).not.toContain('hover:')
    rerender(
      <Card interactive data-testid="card">
        可互動
      </Card>,
    )
    expect(screen.getByTestId('card').className).toContain('hover:shadow-md')
  })
})
