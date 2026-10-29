import type { Resilience } from '../lib/resilience'

const LIMITATIONS = [
  '回報變多不等於校園變危險：件數反映的是被張貼、被回報、被系統收到的事件，宣導或來源增加都會讓件數變多。',
  '只涵蓋系統收集的來源（校方公告、社群社團貼文、匿名回報）；沒有被張貼或回報的事件不在統計內。',
]

/** 四個構面齊全的成績單。 */
export const RESILIENCE: Resilience = {
  score: 72,
  scope: 'all',
  window: { start: '2026-05-02', end: '2026-10-28', days: 180 },
  event_count: 24,
  action_count: 11,
  min_samples: 5,
  weights: [0.25, 0.25, 0.25, 0.25],
  dimensions: [
    {
      key: 'detection',
      label: '發現',
      score: 85,
      sample_size: 20,
      eligible: 24,
      coverage: 0.83,
      reason: null,
      metrics: [{ key: 'delay_days', unit: 'days', value: 3, score: 85, sample_size: 20, eligible: 24, coverage: 0.83 }],
    },
    {
      key: 'response',
      label: '回應',
      score: 64,
      sample_size: 9,
      eligible: 24,
      coverage: 0.38,
      reason: null,
      metrics: [{ key: 'delay_days', unit: 'days', value: 11.5, score: 64, sample_size: 9, eligible: 24, coverage: 0.38 }],
    },
    {
      key: 'recovery',
      label: '恢復',
      score: 70,
      sample_size: 7,
      eligible: 9,
      coverage: 0.78,
      reason: null,
      metrics: [{ key: 'days', unit: 'days', value: 20, score: 70, sample_size: 7, eligible: 9, coverage: 0.78 }],
    },
    {
      key: 'adaptation',
      label: '調適',
      score: 68,
      sample_size: 10,
      eligible: 11,
      coverage: 0.91,
      reason: null,
      metrics: [
        { key: 'completion_rate', unit: 'ratio', value: 0.4, score: 40, sample_size: 10, eligible: 11, coverage: 0.91 },
        { key: 'repeat_ratio', unit: 'ratio', value: 0.04, score: 96, sample_size: 22, eligible: 24, coverage: 0.92 },
      ],
    },
  ],
  limitations: LIMITATIONS,
}

/** 資料不足：恢復構面樣本不足，總分為 null。 */
export const RESILIENCE_PARTIAL: Resilience = {
  ...RESILIENCE,
  score: null,
  dimensions: RESILIENCE.dimensions.map((d) =>
    d.key === 'recovery'
      ? {
          ...d,
          score: null,
          sample_size: 2,
          coverage: 0.22,
          reason: 'insufficient_samples',
          metrics: [{ ...d.metrics[0], value: null, score: null, sample_size: 2, coverage: 0.22 }],
        }
      : d,
  ),
}

/** 公開版：只統計公開中的事件，附公開限制。 */
export const PUBLIC_RESILIENCE: Resilience = {
  ...RESILIENCE_PARTIAL,
  scope: 'published',
  limitations: [...LIMITATIONS, '公開版只統計已查證並公開的事件。'],
}
