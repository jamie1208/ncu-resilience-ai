/**
 * 校園韌性成績單（P6-T05，規格 §15）的型別與顯示格式，後台與公開版共用。
 * 分數由後端 `app/resilience/cri.py` 計算；資料不足時為 null，一律顯示 N/A，不補任何數字。
 */

export type DimensionKey = 'detection' | 'response' | 'recovery' | 'adaptation'

export interface ResilienceMetric {
  key: string
  unit: 'days' | 'ratio'
  value: number | null
  score: number | null
  sample_size: number
  eligible: number
  coverage: number | null
}

export interface ResilienceDimension {
  key: DimensionKey
  label: string
  score: number | null
  sample_size: number
  eligible: number
  coverage: number | null
  reason: string | null
  metrics: ResilienceMetric[]
}

export interface Resilience {
  score: number | null
  scope: 'all' | 'published'
  window: { start: string; end: string; days: number }
  event_count: number
  action_count: number
  min_samples: number
  weights: number[]
  dimensions: ResilienceDimension[]
  limitations: string[]
}

export const METRIC_LABELS: Record<string, string> = {
  delay_days: '延遲中位數',
  days: '恢復天數中位數',
  completion_rate: '改善完成率',
  repeat_ratio: '重複事件比例',
}

/** 各構面在問什麼（白話說明）。 */
export const DIMENSION_QUESTIONS: Record<DimensionKey, string> = {
  detection: '事情發生後，多快被發現？',
  response: '發現之後，多快開始處理？',
  recovery: '開始處理之後，多快恢復？',
  adaptation: '改善有沒有做完、同樣的事有沒有再發生？',
}

export const NA = 'N/A'

export function formatScore(score: number | null): string {
  return score === null ? NA : String(score)
}

export function formatMetric(metric: ResilienceMetric): string {
  if (metric.value === null) return NA
  return metric.unit === 'days' ? `${metric.value} 天` : `${Math.round(metric.value * 100)}%`
}

export function formatCoverage(coverage: number | null): string {
  return coverage === null ? '—' : `${Math.round(coverage * 100)}%`
}
