import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Button, Card, SectionLabel } from '.'

describe('Playful Button', () => {
  it('預設為 primary 糖果按鈕：膠囊、2px 深色框、硬陰影、彈跳曲線', () => {
    render(<Button>查看事件</Button>)
    const btn = screen.getByRole('button', { name: '查看事件' })
    expect(btn).toHaveAttribute('type', 'button')
    expect(btn.className).toContain('rounded-full')
    expect(btn.className).toContain('border-2')
    expect(btn.className).toContain('border-foreground')
    expect(btn.className).toContain('bg-accent')
    expect(btn.className).toContain('shadow-pop')
    expect(btn.className).toContain('ease-bounce')
  })

  it('觸控高度至少 48px（h-12 以上），且有可見 focus ring', () => {
    for (const size of ['sm', 'md', 'lg'] as const) {
      const { unmount } = render(<Button size={size}>按鈕</Button>)
      const btn = screen.getByRole('button', { name: '按鈕' })
      expect(btn.className).toMatch(/\bh-1[2-6]\b/)
      expect(btn.className).toContain('focus-visible:ring-2')
      unmount()
    }
  })

  it('手機寬度硬陰影縮為 2px，sm 以上才用 4px', () => {
    render(<Button>送出</Button>)
    const btn = screen.getByRole('button', { name: '送出' })
    expect(btn.className).toContain('shadow-pop-sm')
    expect(btn.className).toContain('sm:shadow-pop')
  })

  it('outline 為透明底、hover 填黃色，不使用 accent 底色；className 可覆蓋', () => {
    render(
      <Button variant="outline" className="w-full">
        取消
      </Button>,
    )
    const btn = screen.getByRole('button', { name: '取消' })
    expect(btn.className).toContain('bg-transparent')
    expect(btn.className).toContain('hover:bg-tertiary')
    expect(btn.className).not.toContain('bg-accent')
    expect(btn.className).toContain('w-full')
  })
})

describe('Playful SectionLabel', () => {
  it('英文標籤使用粗體全大寫與寬字距', () => {
    render(<SectionLabel>Live updates</SectionLabel>)
    const text = screen.getByText('Live updates')
    expect(text.className).toContain('uppercase')
    expect(text.className).toContain('tracking-wide')
    expect(text.className).toContain('font-bold')
  })

  it('中文標籤不使用全大寫與寬字距', () => {
    render(<SectionLabel>校園動態</SectionLabel>)
    const text = screen.getByText('校園動態')
    expect(text.className).not.toContain('uppercase')
    expect(text.className).not.toContain('tracking-wide')
  })

  it('pulse 只在 motion-safe 時動畫，裝飾形狀對輔助技術隱藏', () => {
    const { container } = render(<SectionLabel pulse>Live</SectionLabel>)
    const shape = container.querySelector('[aria-hidden="true"]')
    expect(shape).not.toBeNull()
    expect(shape?.getAttribute('class')).toContain('motion-safe:')
  })
})

describe('Playful Card', () => {
  it('貼紙卡：白底、2px 深色框、硬陰影；預設為 div', () => {
    render(<Card data-testid="card">內容</Card>)
    const card = screen.getByTestId('card')
    expect(card.tagName).toBe('DIV')
    expect(card.className).toContain('bg-card')
    expect(card.className).toContain('border-2')
    expect(card.className).toContain('shadow-sticker')
  })

  it('featured 改用粉紅硬陰影；as 可指定語意標籤', () => {
    render(
      <ul>
        <Card as="li" featured data-testid="card">
          重點
        </Card>
      </ul>,
    )
    const card = screen.getByTestId('card')
    expect(card.tagName).toBe('LI')
    expect(card.className).toContain('shadow-sticker-featured')
  })

  it('只有 interactive 才有 hover 搖擺效果，且限 motion-safe', () => {
    const { rerender } = render(<Card data-testid="card">靜態</Card>)
    expect(screen.getByTestId('card').className).not.toContain('hover:')
    rerender(
      <Card interactive data-testid="card">
        可互動
      </Card>,
    )
    const cls = screen.getByTestId('card').className
    expect(cls).toContain('motion-safe:hover:-rotate-1')
    expect(cls).toContain('motion-safe:hover:scale-[1.02]')
  })
})
