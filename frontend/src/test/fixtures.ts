/** 管理端 API 的假資料（vitest 與 Playwright 共用；內容皆為虛構，不含個資）。 */
import type {
  AlertItem,
  AuditLog,
  Dashboard,
  EventDetail,
  EventItem,
  EventReport,
  IngestionRun,
  SkillSummary,
  SourceHealth,
} from '../admin/api'

export const EVENT_ID = '0b4f2a52-6a43-4c55-9d43-1d2a2b0e0001'
export const EVENT_ID_2 = '0b4f2a52-6a43-4c55-9d43-1d2a2b0e0002'

export const EVENTS: EventItem[] = [
  {
    id: EVENT_ID,
    title: '工程一館停電',
    type: 'power_outage',
    status: 'unverified',
    occurrence_start_date: '2026-10-20',
    created_at: '2026-10-20T02:10:00Z',
    updated_at: '2026-10-20T02:30:00Z',
    evidence_count: 2,
    risk: { score: 64.5, level: 'high' },
  },
  {
    id: EVENT_ID_2,
    title: '圖書館網路維護',
    type: 'network',
    status: 'verified',
    occurrence_start_date: null,
    created_at: '2026-10-19T08:00:00Z',
    updated_at: '2026-10-19T09:00:00Z',
    evidence_count: 1,
    risk: null,
  },
]

export const EVENT_DETAIL: EventDetail = {
  id: EVENT_ID,
  title: '工程一館停電',
  type: 'power_outage',
  status: 'unverified',
  occurrence_start_date: '2026-10-20',
  created_at: '2026-10-20T02:10:00Z',
  updated_at: '2026-10-20T02:30:00Z',
  summary: null,
  evidence: [
    {
      source_item_id: 'si-1',
      source_code: 'ncu_announcements',
      url: 'https://www.ncu.edu.tw/announcements/1',
      published_at: '2026-10-20T01:00:00Z',
      support_type: 'supports',
      excerpt: '工程一館今日上午 9 時至 12 時停電施工。',
    },
    {
      source_item_id: 'si-2',
      source_code: 'facebook_ncu_group',
      url: 'https://example.invalid/post/2',
      published_at: null,
      support_type: 'contradicts',
      excerpt: '<b>聽說</b>停電延到明天',
    },
  ],
  risk_assessments: [
    {
      id: 'ra-2',
      score: 64.5,
      level: 'high',
      dimensions: {
        personal_safety: { score: 20, uncertainty: 0.2 },
        operations: { score: 85, uncertainty: 0.1 },
        property: { score: 30, uncertainty: 0.3 },
        reputation: { score: 10, uncertainty: 0.4 },
        scope: { score: 70, uncertainty: 0.2 },
        _rules: ['floor'],
        _weighted_level: 'medium',
      },
      policy_version: '2026-10-21.1',
      ai_run_id: 'run-2',
      created_at: '2026-10-20T02:30:00Z',
    },
    {
      id: 'ra-1',
      score: 40,
      level: 'medium',
      dimensions: {
        personal_safety: { score: 20, uncertainty: 0.3 },
        operations: { score: 60, uncertainty: 0.3 },
        property: { score: 30, uncertainty: 0.3 },
        reputation: { score: 10, uncertainty: 0.4 },
        scope: { score: 50, uncertainty: 0.3 },
        _rules: [],
        _weighted_level: 'medium',
      },
      policy_version: '2026-10-21.1',
      ai_run_id: 'run-1',
      created_at: '2026-10-20T02:15:00Z',
    },
  ],
  report: null,
}

/** 事件小報告（evidence_ids 是 source_item_id 去掉連字號）。 */
export const EVENT_REPORT: EventReport = {
  id: 'rp-1',
  trigger: 'auto',
  valid: true,
  invalidated_at: null,
  invalidated_reason: null,
  verification_status: 'unverified',
  risk_level: 'high',
  impacts: {
    status: 'ok',
    message: null,
    items: [
      { dimension: 'operations', score: 85, uncertainty: 0.1, reason: '停電影響上課與實驗。', evidence_ids: ['si1'] },
      { dimension: 'personal_safety', score: 20, uncertainty: 0.2, reason: null, evidence_ids: ['si1'] },
    ],
  },
  causes: {
    status: 'ok',
    message: null,
    items: [
      {
        cause_id: 'c1',
        description: '變電箱老舊跳脫。',
        basis: 'confirmed',
        label: '已確認',
        quotes: [{ evidence_id: 'si1', quote: '停電原因為變電箱老舊跳脫' }],
        to_verify: null,
        evidence_ids: ['si1'],
      },
      {
        cause_id: 'c2',
        description: '疑似附近施工挖到電纜。',
        basis: 'hypothesized',
        label: '推測，尚未查證',
        quotes: [{ evidence_id: 'si2', quote: '<b>聽說</b>是施工挖到電纜' }],
        to_verify: '向總務處確認施工是否影響供電。',
        evidence_ids: ['si2'],
      },
    ],
  },
  missing_information: ['變電箱更換時程'],
  measures: {
    status: 'ok',
    message: null,
    items: [
      {
        action: '停電期間改到其他大樓自習。',
        audience: 'students',
        horizon: 'short',
        priority: 'high',
        kind: 'respond',
        cause_id: null,
        cause_basis: null,
        responsible_unit: null,
        evidence_ids: ['si1'],
      },
      {
        action: '盤點同年份的變電箱並排定汰換。',
        audience: 'school',
        horizon: 'mid',
        priority: 'high',
        kind: 'prevent',
        cause_id: 'c1',
        cause_basis: 'confirmed',
        responsible_unit: '總務處',
        evidence_ids: ['si1'],
      },
      {
        action: '確認附近施工是否曾影響供電線路。',
        audience: 'school',
        horizon: 'short',
        priority: 'medium',
        kind: 'investigate',
        cause_id: 'c2',
        cause_basis: 'hypothesized',
        responsible_unit: '總務處',
        evidence_ids: ['si2'],
      },
    ],
  },
  referrals: [{ unit: '總務處', evidence_ids: ['si1', 'si2'] }],
  root_cause_run_id: 'run-7',
  solution_run_id: 'run-8',
  created_at: '2026-10-20T02:40:00Z',
}

export const ALERTS: AlertItem[] = [
  {
    id: 'al-1',
    event_id: EVENT_ID,
    event_title: '工程一館停電',
    audience: 'owner',
    state: 'published',
    version: 2,
    body: '工程一館停電施工至 12 時，請留意。',
    retract_reason: null,
    flags: [],
    created_at: '2026-10-20T02:31:00Z',
    updated_at: '2026-10-20T02:31:00Z',
  },
  {
    id: 'al-2',
    event_id: EVENT_ID_2,
    event_title: '圖書館網路維護',
    audience: 'owner',
    state: 'retracted',
    version: 1,
    body: '圖書館網路維護。',
    retract_reason: '資訊已過時',
    flags: [],
    created_at: '2026-10-19T09:01:00Z',
    updated_at: '2026-10-19T10:00:00Z',
  },
]

export const DASHBOARD: Dashboard = {
  events: {
    by_status: { unverified: 3, verified: 5 },
    by_type: { power_outage: 2, network: 6 },
    created_last_7d: 4,
    high_risk_open: [{ id: EVENT_ID, title: '工程一館停電', level: 'high' }],
  },
  alerts: { by_state: { published: 1, retracted: 1 } },
  ai_runs_last_7d: [
    {
      skill_name: 'event-classifier',
      runs: 12,
      failed: 1,
      abstained: 2,
      failure_rate: 0.0833,
      cost_usd: 0.0421,
    },
  ],
}

export const SOURCE_HEALTH: SourceHealth[] = [
  {
    code: 'ncu_announcements',
    name: '中央大學公告',
    enabled: true,
    access_policy: 'public',
    poll_interval: 3600,
    status: 'healthy',
    last_run_status: 'succeeded',
    last_run_at: '2026-10-21T00:00:00Z',
    last_success_at: '2026-10-21T00:00:00Z',
    consecutive_failures: 0,
    runs_in_window: 168,
    success_rate: 1,
    open_dead_letters: 0,
  },
  {
    code: 'cwa_warnings',
    name: '氣象署警特報',
    enabled: true,
    access_policy: 'open_data',
    poll_interval: 600,
    status: 'failing',
    last_run_status: 'failed',
    last_run_at: '2026-10-21T00:10:00Z',
    last_success_at: '2026-10-20T12:00:00Z',
    consecutive_failures: 4,
    runs_in_window: 900,
    success_rate: 0.95,
    open_dead_letters: 2,
  },
]

export const INGESTION_RUNS: IngestionRun[] = [
  {
    id: 'ir-1',
    source_code: 'cwa_warnings',
    status: 'failed',
    started_at: '2026-10-21T00:10:00Z',
    finished_at: '2026-10-21T00:10:05Z',
    counts: { fetched: 0, errors: { timeout: 1 } },
  },
]

export const AUDIT_LOGS: AuditLog[] = [
  {
    id: 'au-1',
    actor: 'admin:jamie',
    action: 'event.verify',
    target: `event:${EVENT_ID}`,
    request_id: 'req-1',
    metadata: { from: 'unverified', to: 'verified' },
    created_at: '2026-10-20T03:00:00Z',
  },
]

export const SKILLS: SkillSummary[] = [
  {
    name: 'event-classifier',
    version: '1.2.0',
    schema_version: '1',
    runs_last_7d: {
      runs: 12,
      failed: 1,
      abstained: 2,
      failure_rate: 0.0833,
      cost_usd: 0.0421,
      versions: ['1.1.0', '1.2.0'],
    },
    eval: {
      mode: 'live',
      model: 'gpt-5-mini',
      split: 'test',
      generated_at: '2026-10-21T03:59:14+00:00',
      passed: true,
      cases: 28,
      cases_passed: 27,
      pass_rate: 0.9643,
      schema_pass_rate: 1,
      abstain_rate: 0.0357,
      cost_usd: 0.029082,
      metrics: [{ name: 'macro_f1', value: 0.91, threshold: 0.85, passed: true }],
    },
  },
  {
    name: 'risk-assessor',
    version: '1.0.0',
    schema_version: '1',
    runs_last_7d: {
      runs: 0,
      failed: 0,
      abstained: 0,
      failure_rate: 0,
      cost_usd: 0,
      versions: [],
    },
    eval: null,
  },
]
