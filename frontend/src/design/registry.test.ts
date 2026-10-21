import { describe, expect, it } from 'vitest'

import { DEFAULT_DESIGN, DESIGN_NAMES, FONT_STYLESHEETS, resolveDesign } from './registry'

describe('resolveDesign', () => {
  it('未設定或空字串時使用預設設計 minimal', () => {
    expect(DEFAULT_DESIGN).toBe('minimal')
    expect(resolveDesign(undefined)).toBe('minimal')
    expect(resolveDesign('')).toBe('minimal')
  })

  it('接受已登記的設計名稱（忽略前後空白）', () => {
    expect(resolveDesign('playful')).toBe('playful')
    expect(resolveDesign(' minimal ')).toBe('minimal')
  })

  it('未知的設計名稱直接拋錯，讓建置失敗', () => {
    expect(() => resolveDesign('brutalist')).toThrow(/VITE_DESIGN/)
    expect(() => resolveDesign('Playful')).toThrow(/VITE_DESIGN/)
  })
})

describe('FONT_STYLESHEETS', () => {
  it('每套設計都有 Google Fonts 樣式表，且都載入繁中 fallback 字體', () => {
    for (const name of DESIGN_NAMES) {
      const href = FONT_STYLESHEETS[name]
      expect(href).toMatch(/^https:\/\/fonts\.googleapis\.com\/css2\?/)
      expect(href).toContain('Noto+Sans+TC')
      expect(href).toContain('display=swap')
    }
  })

  it('playful 載入 Outfit 與 Plus Jakarta Sans', () => {
    expect(FONT_STYLESHEETS.playful).toContain('family=Outfit')
    expect(FONT_STYLESHEETS.playful).toContain('family=Plus+Jakarta+Sans')
  })
})
