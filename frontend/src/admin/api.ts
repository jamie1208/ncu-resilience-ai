/**
 * 管理端 API（P3-T01 驗證、P3-T03 管理 API、P3-T05 Skills 儀表板）。
 * session 在 HttpOnly cookie，前端看不到；寫入請求在 `X-CSRF-Token` 帶上 CSRF token
 * （登入回應提供，重新整理後改讀非 HttpOnly 的 `ncu_csrf` cookie）。
 */
import { apiGet, apiRequest } from '../api/client'

const V1 = '/api/v1'
const CSRF_COOKIE = 'ncu_csrf'

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'
export type EventStatus =
  | 'detected'
  | 'analyzing'
  | 'unverified'
  | 'verified'
  | 'disputed'
  | 'false'
  | 'monitoring'
  | 'resolved'
  | 'archived'
/** 管理者可設定的事件狀態（detected、analyzing 只由分析流程設定）。 */
export const VERIFY_STATUSES = [
  'unverified',
  'verified',
  'disputed',
  'false',
  'monitoring',
  'resolved',
  'archived',
] as const satisfies readonly EventStatus[]
export type VerifyStatus = (typeof VERIFY_STATUSES)[number]
export type AlertState =
  | 'candidate'
  | 'policy_checked'
  | 'eligible'
  | 'blocked'
  | 'published'
  | 'updated'
  | 'retracted'
  | 'expired'
/** 只有已通知（published、updated）的通知可以撤回。 */
export const RETRACTABLE_STATES: readonly AlertState[] = ['published', 'updated']

export interface Me {
  username: string
  role: string
}

export interface Page<T> {
  items: T[]
  nextCursor: string | null
}

export interface EventItem {
  id: string
  title: string
  type: string
  status: EventStatus
  occurrence_start_date: string | null
  created_at: string
  updated_at: string
  evidence_count: number
  risk: { score: number; level: RiskLevel } | null
}

export interface Evidence {
  source_item_id: string
  source_code: string
  url: string
  published_at: string | null
  support_type: string
  excerpt: string | null
}

export interface DimensionScore {
  score: number
  uncertainty: number
}

export interface RiskAssessment {
  id: string
  score: number
  level: RiskLevel
  /** 五個面向的分數；`_rules`、`_weighted_level` 為規則引擎的附註。 */
  dimensions: Record<string, unknown>
  policy_version: string
  ai_run_id: string | null
  created_at: string
}

/** 事件說明：S02 依來源整理的中性摘要與出處（P4-T08）。 */
export interface EventSummary {
  text: string
  evidence_ids: string[]
  source_item_id: string | null
  ai_run_id: string | null
  updated_at: string | null
}

/** 可能的影響：S06 的一個分項（分數由規則引擎計算）。 */
export interface ReportImpact {
  dimension: string
  score: number
  uncertainty: number
  reason: string | null
  evidence_ids: string[]
}

export type CauseBasis = 'confirmed' | 'hypothesized'

/** S07 的原因；hypothesized 一律帶「推測，尚未查證」標示，confirmed 附官方來源的逐字原句。 */
export interface ReportCause {
  cause_id: string
  description: string
  basis: CauseBasis
  label: string
  quotes: { evidence_id: string; quote: string }[]
  to_verify: string | null
  evidence_ids: string[]
}

export type MeasureAudience = 'students' | 'school'
export type MeasureHorizon = 'short' | 'mid' | 'long'

/** S08 的措施；cause_basis 沿用（規則改判後的）原因性質。 */
export interface ReportMeasure {
  action: string
  audience: MeasureAudience
  horizon: MeasureHorizon
  priority: 'high' | 'medium' | 'low'
  kind: 'respond' | 'investigate' | 'prevent'
  cause_id: string | null
  cause_basis: CauseBasis | null
  responsible_unit: string | null
  evidence_ids: string[]
}

/** 報告的一個區塊；abstained／failed 時沒有項目，message 是給使用者看的說明。 */
export interface ReportSection<T> {
  status: 'ok' | 'abstained' | 'failed' | 'unavailable'
  message: string | null
  items: T[]
}

/** 事件小報告（P4-T09）：每一項都帶 evidence_ids（文章 id 的十六進位）。 */
export interface EventReport {
  id: string
  trigger: 'auto' | 'manual'
  valid: boolean
  invalidated_at: string | null
  invalidated_reason: string | null
  verification_status: string
  risk_level: RiskLevel | null
  impacts: ReportSection<ReportImpact>
  causes: ReportSection<ReportCause>
  missing_information: string[]
  measures: ReportSection<ReportMeasure>
  /** 建議轉給的單位：只顯示給使用者，系統不寄信 */
  referrals: { unit: string; evidence_ids: string[] }[]
  root_cause_run_id: string | null
  solution_run_id: string | null
  created_at: string
}

export interface EventDetail {
  id: string
  title: string
  type: string
  status: EventStatus
  occurrence_start_date: string | null
  created_at: string
  updated_at: string
  summary: EventSummary | null
  evidence: Evidence[]
  risk_assessments: RiskAssessment[]
  report: EventReport | null
}

export type ActionState = 'discovered' | 'handling' | 'recovered' | 'improved' | 'dropped'
/** 改善措施的狀態轉移（與 app/resilience/actions.py 相同）；改進、不追蹤為終點。 */
export const ACTION_TRANSITIONS: Record<ActionState, ActionState[]> = {
  discovered: ['handling', 'dropped'],
  handling: ['recovered', 'dropped'],
  recovered: ['improved', 'handling', 'dropped'],
  improved: [],
  dropped: [],
}
/** 轉到恢復、改進必須附佐證。 */
export const PROOF_REQUIRED: readonly ActionState[] = ['recovered', 'improved']

/** 改善措施追蹤（P4-T05）；measure 是從 S08 建立時的措施快照，手動新增為 null。 */
export interface ResponseAction {
  id: string
  event_id: string
  origin: 'measure' | 'manual'
  report_id: string | null
  measure_index: number | null
  measure: ReportMeasure | null
  title: string
  owner: string | null
  due_at: string | null
  state: ActionState
  proof: string | null
  overdue: boolean
  created_at: string
  updated_at: string
}

export interface ResponseActionItem extends ResponseAction {
  event: { id: string; title: string }
}

export interface ActionTransition {
  from_state: ActionState | null
  to_state: ActionState
  note: string | null
  proof: string | null
  actor: string
  created_at: string
}

export interface ResponseActionDetail extends ResponseAction {
  history: ActionTransition[]
}

export interface AlertItem {
  id: string
  event_id: string
  event_title: string
  audience: string
  state: AlertState
  version: number
  body: string
  retract_reason: string | null
  /** 管理端要人工判斷的標記，例如 possible_allegation（疑似指控） */
  flags: string[]
  created_at: string
  updated_at: string
}

export interface AiRunStats {
  skill_name: string
  runs: number
  failed: number
  abstained: number
  failure_rate: number
  cost_usd: number
}

export interface Dashboard {
  events: {
    by_status: Record<string, number>
    by_type: Record<string, number>
    created_last_7d: number
    high_risk_open: { id: string; title: string; level: RiskLevel }[]
  }
  alerts: { by_state: Record<string, number> }
  ai_runs_last_7d: AiRunStats[]
}

export type HealthStatus = 'healthy' | 'degraded' | 'failing' | 'stale' | 'disabled' | 'unknown'

export interface SourceHealth {
  code: string
  name: string
  enabled: boolean
  access_policy: string
  poll_interval: number | null
  status: HealthStatus
  last_run_status: string | null
  last_run_at: string | null
  last_success_at: string | null
  consecutive_failures: number
  runs_in_window: number
  success_rate: number | null
  open_dead_letters: number
}

export interface IngestionRun {
  id: string
  source_code: string
  status: string
  started_at: string
  finished_at: string | null
  counts: Record<string, unknown>
}

export interface AuditLog {
  id: string
  actor: string
  action: string
  target: string | null
  request_id: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface EvalMetric {
  name: string
  value: number | null
  threshold: number | null
  passed: boolean | null
}

export interface SkillEval {
  mode: string | null
  model: string | null
  split: string | null
  generated_at: string | null
  passed: boolean
  cases: number | null
  cases_passed: number | null
  pass_rate: number | null
  schema_pass_rate: number | null
  abstain_rate: number | null
  cost_usd: number | null
  metrics: EvalMetric[]
}

export interface SkillSummary {
  name: string
  version: string
  schema_version: string
  runs_last_7d: {
    runs: number
    failed: number
    abstained: number
    failure_rate: number
    cost_usd: number
    versions: string[]
  }
  eval: SkillEval | null
}

// ---- CSRF ----

let csrfToken: string | null = null

function readCookie(name: string): string | null {
  for (const part of document.cookie.split(';')) {
    const [key, ...rest] = part.trim().split('=')
    if (key === name) return decodeURIComponent(rest.join('='))
  }
  return null
}

/** 登出或 session 失效時清掉記憶體中的 CSRF token。 */
export function clearCsrfToken(): void {
  csrfToken = null
}

function post<T>(path: string, body?: unknown) {
  return send<T>('POST', path, body)
}

function send<T>(method: 'POST' | 'PATCH', path: string, body?: unknown) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = csrfToken ?? readCookie(CSRF_COOKIE)
  if (token) headers.set('X-CSRF-Token', token)
  return apiRequest<T>(`${V1}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

async function getPage<T>(path: string): Promise<Page<T>> {
  const { data, meta } = await apiGet<T[]>(`${V1}${path}`)
  const next = meta.next_cursor
  return { items: data, nextCursor: typeof next === 'string' ? next : null }
}

const id = encodeURIComponent

// ---- 驗證 ----

/** 登入；成功時記下 CSRF token。 */
export async function login(username: string, password: string): Promise<Me> {
  const { data } = await post<{ username: string; csrf_token: string }>('/auth/login', {
    username,
    password,
  })
  csrfToken = data.csrf_token
  return { username: data.username, role: 'admin' }
}

/** 登出並清除 CSRF token（即使伺服器端 session 已失效）。 */
export async function logout(): Promise<void> {
  try {
    await post('/auth/logout')
  } finally {
    clearCsrfToken()
  }
}

/** 目前登入者；未登入時拋出 UNAUTHORIZED。 */
export async function fetchMe(): Promise<Me> {
  return (await apiGet<Me>(`${V1}/auth/me`)).data
}

// ---- 事件 ----

export function listEvents(
  filters: { status?: EventStatus | ''; type?: string; cursor?: string | null } = {},
) {
  return getPage<EventItem>(`/admin/events${query(filters)}`)
}

export async function getEvent(eventId: string): Promise<EventDetail> {
  return (await apiGet<EventDetail>(`${V1}/admin/events/${id(eventId)}`)).data
}

/** 記錄查證結果；備註會寫入稽核紀錄，空白不送。 */
export async function verifyEvent(eventId: string, status: VerifyStatus, note = '') {
  const trimmed = note.trim()
  const body = trimmed ? { status, note: trimmed } : { status }
  return (await post<{ id: string; status: EventStatus }>(`/admin/events/${id(eventId)}/verify`, body))
    .data
}

/** 重跑事件分析（非同步排入佇列）。 */
export async function analyzeEvent(eventId: string) {
  return (await post<{ event_id: string; status: string }>(`/admin/events/${id(eventId)}/analyze`))
    .data
}

/** 手動產生事件小報告（非同步排入佇列）。 */
export async function generateReport(eventId: string) {
  return (await post<{ event_id: string; status: string }>(`/admin/events/${id(eventId)}/report`))
    .data
}

// ---- 改善措施追蹤 ----

export async function listActions(
  filters: { state?: ActionState | ''; event_id?: string } = {},
): Promise<ResponseActionItem[]> {
  return (await apiGet<ResponseActionItem[]>(`${V1}/admin/actions${query(filters)}`)).data
}

export async function getAction(actionId: string): Promise<ResponseActionDetail> {
  return (await apiGet<ResponseActionDetail>(`${V1}/admin/actions/${id(actionId)}`)).data
}

/** 新增追蹤項目：帶 report_id、measure_index 從小報告的校方措施建立，否則以 title 手動新增。 */
export async function createAction(
  eventId: string,
  body:
    | { report_id: string; measure_index: number }
    | { title: string; owner?: string; due_at?: string },
): Promise<ResponseActionDetail> {
  return (await post<ResponseActionDetail>(`/admin/events/${id(eventId)}/actions`, body)).data
}

/** 修改負責人、期限；null 代表清除。 */
export async function updateAction(
  actionId: string,
  body: { owner?: string | null; due_at?: string | null },
): Promise<ResponseActionDetail> {
  return (await send<ResponseActionDetail>('PATCH', `/admin/actions/${id(actionId)}`, body)).data
}

/** 變更狀態；備註、佐證空白不送。 */
export async function transitionAction(
  actionId: string,
  to: ActionState,
  { note = '', proof = '' }: { note?: string; proof?: string } = {},
): Promise<ResponseActionDetail> {
  const body: Record<string, string> = { to }
  if (note.trim()) body.note = note.trim()
  if (proof.trim()) body.proof = proof.trim()
  return (await post<ResponseActionDetail>(`/admin/actions/${id(actionId)}/transitions`, body)).data
}

// ---- 通知 ----

export function listAlerts(filters: { state?: AlertState | ''; cursor?: string | null } = {}) {
  return getPage<AlertItem>(`/admin/alerts${query(filters)}`)
}

export async function retractAlert(alertId: string, reason: string) {
  return (await post<{ id: string; state: AlertState }>(`/admin/alerts/${id(alertId)}/retract`, {
    reason: reason.trim(),
  })).data
}

// ---- 儀表板與運維 ----

export async function getDashboard(): Promise<Dashboard> {
  return (await apiGet<Dashboard>(`${V1}/admin/dashboard`)).data
}

export async function getSourceHealth(): Promise<SourceHealth[]> {
  return (await apiGet<SourceHealth[]>(`${V1}/admin/ingestion-runs/health`)).data
}

export function listIngestionRuns(limit = 20) {
  return getPage<IngestionRun>(`/admin/ingestion-runs${query({ limit })}`)
}

export function listAuditLogs(limit = 20) {
  return getPage<AuditLog>(`/admin/audit-logs${query({ limit })}`)
}

export async function listSkills(): Promise<SkillSummary[]> {
  return (await apiGet<SkillSummary[]>(`${V1}/admin/skills`)).data
}
