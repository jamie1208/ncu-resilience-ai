import { describe, expect, it } from 'vitest'

import {
  formatPercent,
  formatTime,
  formatUsd,
  label,
  riskTone,
  RISK_LABELS,
  STATUS_LABELS,
} from './format'

describe('format', () => {
  it('時間一律以 Asia/Taipei 顯示（UTC+8）', () => {
    expect(formatTime('2026-10-21T16:30:00Z')).toBe('2026-10-22 00:30')
    expect(formatTime('2026-10-21T03:59:14+00:00')).toBe('2026-10-21 11:59')
    expect(formatTime(null)).toBe('—')
  })

  it('百分比、美元與缺值', () => {
    expect(formatPercent(0.3333)).toBe('33.3%')
    expect(formatPercent(null)).toBe('—')
    expect(formatUsd(0.013)).toBe('$0.0130')
    expect(formatUsd(12.5)).toBe('$12.50')
  })

  it('已知代碼轉中文，未知代碼原樣顯示', () => {
    expect(label(STATUS_LABELS, 'unverified')).toBe('未查證')
    expect(label(RISK_LABELS, 'critical')).toBe('極高')
    expect(label(STATUS_LABELS, 'brand_new')).toBe('brand_new')
  })

  it('高、極高風險用 danger，中用 warning', () => {
    expect(riskTone('critical')).toBe('danger')
    expect(riskTone('high')).toBe('danger')
    expect(riskTone('medium')).toBe('warning')
    expect(riskTone('low')).toBe('neutral')
  })
})
