import { describe, expect, it } from 'vitest'

import { cn } from './cn'

describe('cn', () => {
  it('合併類別並忽略 falsy 值', () => {
    expect(cn('px-4', false, null, undefined, 'py-2', { hidden: false, block: true })).toBe(
      'px-4 py-2 block',
    )
  })

  it('後面的 Tailwind 類別覆蓋衝突的前者', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4')
    expect(cn('bg-muted', 'bg-accent')).toBe('bg-accent')
  })

  it('文字大小與文字色彩不互相覆蓋', () => {
    expect(cn('text-sm', 'text-accent')).toBe('text-sm text-accent')
  })

  it('自訂陰影 token 與內建陰影互相覆蓋', () => {
    expect(cn('shadow-md', 'shadow-accent')).toBe('shadow-accent')
    expect(cn('shadow-accent', 'shadow-accent-lg')).toBe('shadow-accent-lg')
  })

  it('自訂字體 token 與內建字體互相覆蓋', () => {
    expect(cn('font-sans', 'font-display')).toBe('font-display')
    expect(cn('font-display', 'font-semibold')).toBe('font-display font-semibold')
  })
})
