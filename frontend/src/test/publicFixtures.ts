/** 公開前端（P5-T12）的測試資料：Vitest 與 Playwright（frontend/e2e）共用。 */
import type {
  PublicCategory,
  PublicEventDetail,
  PublicEventSummary,
  PublicNotice,
  PublicProgress,
} from '../public/api'

export const PUBLIC_EVENT_ID = '7d3f1c2a-0000-4000-8000-000000000001'

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
  progress: [
    { title: '張貼改道指示', state: 'handling', due_date: '2026-11-05', updated_at: '2026-11-02T03:00:00Z' },
  ],
}

export const PUBLIC_CATEGORIES: PublicCategory[] = [
  { event_type: 'facility', label: '設施', content: 1, safety_notice: 0 },
  { event_type: 'traffic', label: '交通', content: 0, safety_notice: 1 },
  { event_type: 'security', label: '治安', content: 0, safety_notice: 0 },
]

export const PUBLIC_PROGRESS: PublicProgress = {
  id: PUBLIC_EVENT_ID,
  version: 2,
  published_at: PUBLIC_EVENT.published_at,
  event_type: 'facility',
  label: '設施',
  title: PUBLIC_EVENT.title,
  occurrence_date: '2026-11-02',
  counts: { handling: 1, improved: 1 },
  progress: [
    ...PUBLIC_EVENT_DETAIL.progress,
    { title: '加裝夜間照明', state: 'improved', due_date: null, updated_at: '2026-11-01T03:00:00Z' },
  ],
}
