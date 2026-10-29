import { describe, expect, it } from 'vitest'

import { RESILIENCE, RESILIENCE_PARTIAL } from '../test/resilienceFixtures'
import { formatCoverage, formatMetric, formatScore, METRIC_LABELS } from './resilience'

describe('resilience 格式', () => {
  it('分數為 null 時顯示 N/A，不顯示 0 或小數', () => {
    expect(formatScore(null)).toBe('N/A')
    expect(formatScore(72)).toBe('72')
  })

  it('天數與比例的指標', () => {
    const [completion, repeat] = RESILIENCE.dimensions[3].metrics
    expect(formatMetric(RESILIENCE.dimensions[1].metrics[0])).toBe('11.5 天')
    expect(formatMetric(RESILIENCE.dimensions[0].metrics[0])).toBe('3 天')
    expect(formatMetric(completion)).toBe('40%')
    expect(formatMetric(repeat)).toBe('4%')
    expect(formatMetric(RESILIENCE_PARTIAL.dimensions[2].metrics[0])).toBe('N/A')
  })

  it('覆蓋率四捨五入到整數百分比，沒有應列入的資料時為 —', () => {
    expect(formatCoverage(0.83)).toBe('83%')
    expect(formatCoverage(null)).toBe('—')
  })

  it('每個指標都有中文名稱', () => {
    for (const d of RESILIENCE.dimensions) for (const m of d.metrics) expect(METRIC_LABELS[m.key]).toBeTruthy()
  })
})
