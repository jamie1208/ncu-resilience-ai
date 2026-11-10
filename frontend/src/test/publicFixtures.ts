/** 公開前端（P5-T12）的測試資料：Vitest 與 Playwright（frontend/e2e）共用。 */
import type {
  PublicCategory,
  PublicEventDetail,
  PublicEventSummary,
  PublicNotice,
  PublicProgress,
  PublicSeries,
} from '../public/api'

export const PUBLIC_EVENT_ID = '7d3f1c2a-0000-4000-8000-000000000001'
export const PUBLIC_UNVERIFIED_ID = '7d3f1c2a-0000-4000-8000-000000000003'
export const PUBLIC_SERIES_ID = '7d3f1c2a-0000-4000-8000-000000000009'

export const PUBLIC_EVENT: PublicEventSummary = {
  id: PUBLIC_EVENT_ID,
  kind: 'content',
  version: 2,
  published_at: '2026-11-02T01:30:00Z',
  event_type: 'facility',
  label: '設施',
  title: '工程五館外牆整修施工',
  summary: '工程五館外牆整修，施工期間東側出入口封閉。',
  location: '工程五館',
  occurrence_date: '2026-11-02',
  risk_level: 'low',
  verification: 'verified',
  verification_label: '已查證',
  series: { id: PUBLIC_SERIES_ID, name: '工程五館施工封路', ordinal: 2, event_count: 2 },
}

/** 未查證的快報（P8-T06）：公開 AI 摘要並標「尚未查證」。 */
export const PUBLIC_UNVERIFIED: PublicEventSummary = {
  id: PUBLIC_UNVERIFIED_ID,
  kind: 'content',
  version: 1,
  published_at: '2026-11-01T04:00:00Z',
  event_type: 'security',
  label: '治安',
  title: '宿舍區腳踏車失竊',
  summary: '有同學反映宿舍區腳踏車被偷。',
  location: '宿舍區',
  occurrence_date: '2026-11-01',
  risk_level: 'medium',
  verification: 'unverified',
  verification_label: '尚未查證',
  series: null,
}

export const PUBLIC_NOTICE: PublicNotice = {
  id: '7d3f1c2a-0000-4000-8000-000000000002',
  kind: 'safety_notice',
  version: 1,
  published_at: '2026-10-20T08:00:00Z',
  event_type: 'traffic',
  label: '交通',
  notice:
    '【交通｜一般安全提醒】有未經查證的訊息提到可能有交通相關狀況，目前無法確認是否屬實。\n地點：校門口　日期：2026-10-20\n建議：請留意交通狀況並遵守號誌。',
}

export const PUBLIC_EVENT_DETAIL: PublicEventDetail = {
  ...PUBLIC_EVENT,
  report: {
    impacts: [
      { dimension: 'operations', score: 2, uncertainty: 0.2, reason: '東側出入口封閉，需改走西側。' },
    ],
    measures: [
      {
        action: '改由西側出入口進出',
        audience: 'students',
        horizon: 'short',
        priority: 'high',
        kind: 'mitigation',
        responsible_unit: null,
      },
      {
        action: '在出入口張貼改道指示',
        audience: 'school',
        horizon: 'short',
        priority: 'medium',
        kind: 'mitigation',
        responsible_unit: '總務處',
      },
    ],
  },
  sources: [{ name: '中央大學公告', url: 'https://www.ncu.edu.tw/announcements/a01' }],
}

const SERIES_ACTION = {
  title: '張貼改道指示',
  state: 'handling' as const,
  due_date: '2026-11-05',
  updated_at: '2026-11-02T03:00:00Z',
}

/** 公開系列分析頁（P8-T06）：兩起子事件，第 2 起是 PUBLIC_EVENT。 */
export const PUBLIC_SERIES: PublicSeries = {
  id: PUBLIC_SERIES_ID,
  kind: 'series',
  version: 3,
  published_at: '2026-11-02T02:00:00Z',
  event_type: 'facility',
  label: '設施',
  name: '工程五館施工封路',
  summary: '工程五館周邊施工，出入口陸續封閉。',
  event_count: 2,
  first_date: '2026-10-20',
  latest_date: '2026-11-02',
  events: [
    {
      id: 'e-pub-1',
      ordinal: 1,
      title: '工程五館北側通道封閉',
      occurrence_date: '2026-10-20',
      location: '工程五館',
      verification: 'unverified',
      verification_label: '尚未查證',
    },
    {
      id: PUBLIC_EVENT_ID,
      ordinal: 2,
      title: PUBLIC_EVENT.title,
      occurrence_date: '2026-11-02',
      location: '工程五館',
      verification: 'verified',
      verification_label: '已查證',
    },
  ],
  timeline: {
    granularity: 'week',
    points: [
      { period_start: '2026-10-19', count: 1, moving_average: null },
      { period_start: '2026-10-26', count: 0, moving_average: null },
      { period_start: '2026-11-02', count: 1, moving_average: null },
    ],
    markers: [{ title: '張貼改道指示', kind: 'started', on: '2026-11-02' }],
  },
  bursts: [],
  location_groups: [{ label: '工程五館周邊', related_ids: ['e-pub-1', PUBLIC_EVENT_ID], event_count: 2, total: 2 }],
  pattern_status: 'ok',
  pattern_note: null,
  patterns: [
    { description: '封閉多在平日白天施工時段', label: '推測，尚未查證', related_ids: ['e-pub-1', PUBLIC_EVENT_ID], event_count: 2, total: 2 },
  ],
  causes: [
    { description: '外牆整修工程分段進行', label: '已確認', decision: 'confirmed', related_ids: [PUBLIC_EVENT_ID], event_count: 1, total: 2 },
    { description: '施工告示張貼位置不明顯', label: '推測，尚未查證', decision: 'speculative', related_ids: ['e-pub-1'], event_count: 1, total: 2 },
  ],
  measures: [
    { action: '改由西側出入口進出', audience: 'students', horizon: 'short', priority: 'high', kind: 'mitigate', responsible_unit: null },
    { action: '在出入口張貼改道指示', audience: 'school', horizon: 'short', priority: 'medium', kind: 'mitigate', responsible_unit: '總務處' },
  ],
  progress: [SERIES_ACTION],
  comparisons: [
    {
      title: '張貼改道指示',
      start: '2026-11-02',
      status: 'insufficient',
      before_events: 1,
      after_events: 1,
      before_weekly_average: null,
      after_weekly_average: null,
      message: '資料還不夠',
      caveat: '變少不一定是措施的功勞：同一段時間也可能有天氣、學期時程、回報習慣或資料來源的變化，件數是被回報的數量，不等於實際發生次數。',
    },
  ],
}

export const PUBLIC_CATEGORIES: PublicCategory[] = [
  { event_type: 'facility', label: '設施', content: 1, safety_notice: 0 },
  { event_type: 'traffic', label: '交通', content: 0, safety_notice: 1 },
  { event_type: 'security', label: '治安', content: 0, safety_notice: 0 },
]

export const PUBLIC_PROGRESS: PublicProgress = {
  id: PUBLIC_SERIES_ID,
  kind: 'series',
  version: 3,
  published_at: PUBLIC_SERIES.published_at,
  event_type: 'facility',
  label: '設施',
  title: PUBLIC_SERIES.name,
  occurrence_date: '2026-11-02',
  counts: { handling: 1, improved: 1 },
  progress: [
    SERIES_ACTION,
    { title: '加裝夜間照明', state: 'improved', due_date: null, updated_at: '2026-11-01T03:00:00Z' },
  ],
}
