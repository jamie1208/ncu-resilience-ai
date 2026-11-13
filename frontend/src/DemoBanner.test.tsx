import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DemoBanner } from './DemoBanner'
import { isDemo } from './lib/demo'

// P9-T01（ADR-0022）：示範站（VITE_DEMO=1 建置）在後台與公開站頂端標示「示範資料」
describe('DemoBanner', () => {
  it('只在 VITE_DEMO 為 1 或 true 時視為示範站', () => {
    expect(isDemo('1')).toBe(true)
    expect(isDemo('true')).toBe(true)
    expect(isDemo(undefined)).toBe(false)
    expect(isDemo('')).toBe(false)
    expect(isDemo('0')).toBe(false)
  })

  it('示範站顯示示範資料說明', () => {
    render(<DemoBanner demo />)
    const banner = screen.getByRole('note', { name: '示範資料' })
    expect(banner).toHaveTextContent('示範資料')
    expect(banner).toHaveTextContent('虛構')
  })

  it('一般建置不顯示任何內容', () => {
    const { container } = render(<DemoBanner demo={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('預設依建置設定（測試環境沒有 VITE_DEMO）', () => {
    const { container } = render(<DemoBanner />)
    expect(container).toBeEmptyDOMElement()
  })
})
