/**
 * 管理端 API（P3-T01 驗證、P3-T03 管理 API、P3-T05 Skills 儀表板）。
 * session 在 HttpOnly cookie，前端看不到；寫入請求在 `X-CSRF-Token` 帶上 CSRF token
 * （登入回應提供，重新整理後改讀非 HttpOnly 的 `ncu_csrf` cookie）。
 */
import { apiGet, apiRequest } from '../api/client'
import type { Resilience } from '../lib/resilience'

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
  /** 目前的人工確認（P7-T07）；沒有時為 null。basis 仍是 S07 的判定，不會改寫。 */
  manual_confirmation?: ManualConfirmation | null
}

/** 推測原因的人工確認：確認依據、決定者與時間（ADR-0017）。 */
export interface ManualConfirmation {
  basis: string
  decided_by: string
  decided_at: string
}

export type CauseDecision = 'confirmed' | 'hypothesized'

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

/** 事件小報告（P4-T09）：每一項都帶 evidence_ids（文章 id 的十六進位）。P8-T13 起只有舊版、唯讀。 */
export interface EventReport {
  id: string
  /** P8-T13 起一律為 true：舊版事件小報告（已停用） */
  legacy: boolean
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
  /** 已被手動併入的目標事件 id（P8-T01）；沒有時為 null */
  merged_into: string | null
  /** 事件所屬的系列（P8-T13）；不在系列時為 null */
  series_id: string | null
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

export type RecurrenceReason = 'within_window' | 'after_improvement'

export interface RecurrenceEvent {
  id: string
  title: string
  status: string
  occurred_on: string
  /** 改善措施達到「改進」的日期（台北）；沒有則為 null */
  improved_on: string | null
}

/** 同地點、同類別的一組再發生提醒（後端讀取時以確定性規則重算） */
export interface RecurrenceGroup {
  location_id: string
  location_name: string | null
  event_type: string
  event_type_label: string
  reasons: RecurrenceReason[]
  message: string
  latest_on: string
  /** 新到舊 */
  events: RecurrenceEvent[]
}

export type PublicationState = 'published' | 'withdrawn' | 'taken_down'
export type PublicationLevel = 'content' | 'safety_notice' | 'none'

/**
 * 公開內容（P5-T14）：公開規則判定的公開狀態；preview 為目前版本的公開標題、系列名稱或提醒文字。
 * P8-T06 起也有系列分析頁（`subject` 為 series，`event_id` 為 null，`event_title` 為系列名稱）。
 */
export interface PublicationItem {
  id: string
  subject: 'event' | 'series'
  event_id: string | null
  series_id: string | null
  event_title: string
  level: PublicationLevel
  state: PublicationState
  version: number
  needs_review: boolean
  reasons: string[]
  preview: string | null
  taken_down_at: string | null
  takedown_reason: string | null
  updated_at: string
}

export interface PublicationChange {
  id: string
  state: PublicationState
  previous: PublicationState
}

export type ReminderFlag = 'possible_allegation' | 'mentions_party' | 'speculation'

/** 通知中心的公開提醒（P8-T06）：疑似指控、推測的規律與原因第一次公開或改變的版本。 */
export interface PublicationReminder {
  publication_id: string
  subject: 'event' | 'series'
  event_id: string | null
  series_id: string | null
  title: string
  version: number
  flags: ReminderFlag[]
  state: PublicationState
  current: boolean
  can_take_down: boolean
  created_at: string
}

export type RelationType = 'same_event' | 'related_event' | 'hypothesized_cause'
export type RelationMethod = 'ai' | 'rule' | 'manual'
export type RelationDecision = 'pending' | 'confirmed' | 'rejected'

/** 關聯圖的節點（事件）。 */
export interface RelationNode {
  id: string
  title: string
  type: string
  status: EventStatus
  occurrence_start_date: string | null
}

/** 關聯圖的邊（P6-T01）；hypothesized_cause 由 source（推測的原因）指向 target（結果）。 */
export interface RelationEdge {
  id: string
  source: string
  target: string
  type: RelationType
  method: RelationMethod
  score: number | null
  evidence_ids: string[]
  ai_run_id: string | null
  decision: RelationDecision
  decided_by: string | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
}

export interface RelationGraph {
  nodes: RelationNode[]
  edges: RelationEdge[]
}

export interface RelationDetail {
  edge: RelationEdge
  nodes: RelationNode[]
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

export type ScenarioStatus = 'estimated' | 'not_estimable'
export type CostTier = 'low' | 'medium' | 'high' | 'unknown'
export type ScenarioLimitation =
  | 'hypothetical_not_prediction'
  | 'independence_assumed'
  | 'unknown_effects'
  | 'cost_unknown'
  | 'small_baseline'
  | 'unsupported_assumption'

/** 一項措施的參數；效果為件數降低比例（0–1），上下限都為 null 代表效果未知。 */
export interface ScenarioMeasureSpec {
  name: string
  effect_low: number | null
  effect_high: number | null
  cost: number | null
  rationale: string | null
}

/** 建立模擬的參數；未給 seed 時後端自動產生。 */
export interface ScenarioRequest {
  baseline: number
  window_days: number
  measures: ScenarioMeasureSpec[]
  seed?: number
  event_type?: string
  location?: string
}

export interface ScenarioMeasureResult {
  measure_id: string
  included: boolean
  cost_tier: CostTier
  at_low: number | null
  at_high: number | null
  swing: number | null
  rank: number | null
}

/** What-if 引擎的結果（P7-T02）；`estimate` 只在 estimated 時有值。 */
export interface ScenarioOutput {
  engine_version: string
  seed: number
  samples: number
  status: ScenarioStatus
  baseline: number
  estimate: {
    point: number
    low: number
    high: number
    p05: number
    p50: number
    p95: number
    reduction: number
  } | null
  measures: ScenarioMeasureResult[]
  excluded_measure_ids: string[]
  total_cost: number | null
  total_cost_tier: CostTier
}

/** S10 scenario-simulator 的輸出（只解釋假設與限制，數字來自引擎）。 */
export interface ScenarioExplanationOutput {
  status: 'ok' | 'abstain'
  summary: string
  assumptions: { measure_id: string; statement: string; evidence_ids: string[] }[]
  key_driver: string | null
  limitations: ScenarioLimitation[]
  evidence_ids: string[]
  reason: string
  abstain_reason: string | null
}

export interface ScenarioRun {
  id: string
  event_id: string | null
  input: {
    baseline: number
    window_days: number
    event_type: string | null
    location: string | null
    seed: number
    measures: (ScenarioMeasureSpec & { measure_id: string })[]
  }
  output: ScenarioOutput
  engine_version: string
  seed: number
  explanation: {
    ai_run_id: string
    status: 'succeeded' | 'abstained' | 'failed'
    output: ScenarioExplanationOutput | null
    abstain_reason: string | null
    error_type: string | null
    skill_version: string
  } | null
  created_by: string
  created_at: string
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

/** 把事件併入目標事件（P8-T01）；之後目標事件會排入重新分析。 */
export async function mergeEvent(eventId: string, targetEventId: string) {
  return (
    await post<{ event_id: string; merged_into: string }>(`/admin/events/${id(eventId)}/merge`, {
      target_event_id: targetEventId,
    })
  ).data
}

/** 把指定的來源文章拆到新事件（P8-T01）；原事件與新事件都會排入重新分析。 */
export async function splitEvent(eventId: string, sourceItemIds: string[], title: string) {
  return (
    await post<{ event_id: string; new_event_id: string }>(`/admin/events/${id(eventId)}/split`, {
      source_item_ids: sourceItemIds,
      title: title.trim(),
    })
  ).data
}

/** 重跑事件分析（非同步排入佇列）。 */
export async function analyzeEvent(eventId: string) {
  return (await post<{ event_id: string; status: string }>(`/admin/events/${id(eventId)}/analyze`))
    .data
}

/** 手動產生事件小報告（非同步排入佇列）。 */
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

export async function listRecurrences(): Promise<RecurrenceGroup[]> {
  return (await apiGet<RecurrenceGroup[]>(`${V1}/admin/recurrences`)).data
}

export async function retractAlert(alertId: string, reason: string) {
  return (await post<{ id: string; state: AlertState }>(`/admin/alerts/${id(alertId)}/retract`, {
    reason: reason.trim(),
  })).data
}

// ---- 地點（P8-T02） ----

export interface LocationRef {
  id: string
  name: string
  event_count: number
}

/** 最上層的代表地點：AI 判斷的別名、位在其中的下層地點與事件數（含別名，不含下層）。 */
export interface LocationItem {
  id: string
  name: string
  parent_id: string | null
  event_count: number
  aliases: LocationRef[]
  children: LocationRef[]
}

export async function listLocations(): Promise<LocationItem[]> {
  return (await apiGet<LocationItem[]>(`${V1}/admin/locations`)).data
}

/** 把判斷錯的別名或上層關係拆開；之後不再自動合併。 */
export async function splitLocation(locationId: string) {
  return (
    await post<{ id: string; canonical_id: string | null; parent_id: string | null }>(
      `/admin/locations/${id(locationId)}/split`,
    )
  ).data
}

// ---- 系列（P8-T04、P8-T05） ----

export interface SeriesSummary {
  id: string
  category: string
  title: string
  title_source: 'first_event' | 'ai' | 'manual'
  event_count: number
  latest_joined_at: string | null
  created_at: string
}

export interface SeriesEvent {
  id: string
  title: string
  status: string
  occurrence_start_date: string | null
  created_at: string
  joined_at: string
  method: string
}

export interface SeriesQuote {
  evidence_id: string
  quote: string
}

/** S12 的規律與原因（一律是推測；原因可人工確認）；件數由程式依引用的子事件計算。 */
export interface SeriesPattern {
  pattern_id: string
  description: string
  label: string
  event_ids: string[]
  event_count: number
  total: number
  quotes: SeriesQuote[]
  mentions_party: boolean
}

export interface SeriesCause extends Omit<SeriesPattern, 'pattern_id'> {
  cause_id: string
  to_verify: string | null
  decision: 'confirmed' | 'hypothesized'
}

export interface SeriesMeasure {
  action: string
  audience: 'students' | 'school' | 'units'
  horizon: 'short' | 'mid' | 'long'
  priority: 'high' | 'medium' | 'low'
  kind: 'respond' | 'investigate' | 'prevent'
  cause_id: string | null
  responsible_unit: string | null
  tracked_action_id: string | null
}

export interface SeriesLocationGroup {
  label: string
  reason: string
  event_ids: string[]
  event_count: number
  total: number
}

export interface SeriesBurst {
  burst_id: string
  period_start: string
  count: number
  expected: number
  explanation: string
  event_ids: string[]
}

export interface SeriesAnalysis {
  id: string
  status: string
  title: string | null
  summary: string | null
  bursts: SeriesBurst[]
  location_groups: SeriesLocationGroup[]
  pattern_status: string
  pattern_abstain_reason: string | null
  patterns: SeriesPattern[]
  causes: SeriesCause[]
  measures: SeriesMeasure[]
  dropped: number
  created_at: string
}

export interface SeriesAction {
  id: string
  /** 遷移過來的事件層級措施（P8-T13）的來源事件；系列層級的措施為 null */
  event_id: string | null
  origin: string
  analysis_id: string | null
  measure_index: number | null
  title: string
  owner: string | null
  due_at: string | null
  state: ActionState
  overdue: boolean
  created_at: string
}

export interface SeriesDetail extends SeriesSummary {
  events: SeriesEvent[]
  removals: { event_id: string; reason: string; created_at: string }[]
  analysis: SeriesAnalysis | null
  latest_attempt: { status: string; abstain_reason: string | null; created_at: string } | null
  actions: SeriesAction[]
  /** 舊版事件小報告的人工確認（P8-T13，唯讀）：每份報告每個原因最新的一筆 */
  legacy_cause_decisions: LegacyCauseDecision[]
}

export interface LegacyCauseDecision {
  event_id: string
  report_id: string
  cause_id: string
  description: string | null
  decision: CauseDecision
  basis: string | null
  decided_by: string
  decided_at: string
}

export type TimelineGranularity = 'day' | 'week' | 'month'
export type TimelineRange = '1M' | '3M' | '6M' | '1Y' | 'all'

export interface SeriesComparison {
  action_id: string
  title: string
  start: string
  status: 'ok' | 'insufficient'
  before_events: number
  after_events: number
  after_weeks: number
  before_weekly_average: number | null
  after_weekly_average: number | null
  change_ratio: number | null
  message: string | null
  caveat: string
}

export interface SeriesTimeline {
  granularity: TimelineGranularity
  range: TimelineRange
  location: string | null
  start: string
  end: string
  points: { period_start: string; count: number; moving_average: number | null }[]
  markers: { action_id: string; title: string; kind: 'started' | 'completed'; on: string }[]
  comparisons: SeriesComparison[]
  locations: { name: string; event_count: number }[]
}

export async function listSeries(filters: { category?: string } = {}): Promise<SeriesSummary[]> {
  return (await apiGet<SeriesSummary[]>(`${V1}/admin/series${query(filters)}`)).data
}

export async function getSeries(seriesId: string): Promise<SeriesDetail> {
  return (await apiGet<SeriesDetail>(`${V1}/admin/series/${id(seriesId)}`)).data
}

export async function getSeriesTimeline(
  seriesId: string,
  params: { granularity: TimelineGranularity; range: TimelineRange; location?: string | null },
): Promise<SeriesTimeline> {
  const path = `${V1}/admin/series/${id(seriesId)}/timeline${query(params)}`
  return (await apiGet<SeriesTimeline>(path)).data
}

/** 手動改名；之後 AI 不再覆蓋名稱。 */
export async function renameSeries(seriesId: string, title: string) {
  return (
    await send<{ id: string; title: string; title_source: string }>(
      'PATCH',
      `/admin/series/${id(seriesId)}`,
      { title },
    )
  ).data
}

/** 從最新分析的改善建議一鍵加入追蹤。 */
export async function addSeriesAction(seriesId: string, analysisId: string, measureIndex: number) {
  return (
    await post<SeriesAction>(`/admin/series/${id(seriesId)}/actions`, {
      analysis_id: analysisId,
      measure_index: measureIndex,
    })
  ).data
}

export async function decideSeriesCause(
  seriesId: string,
  causeId: string,
  analysisId: string,
  decision: 'confirmed' | 'hypothesized',
  basis?: string,
) {
  const body: Record<string, string> = { analysis_id: analysisId, decision }
  if (basis?.trim()) body.basis = basis.trim()
  return (
    await post<{ cause_id: string; decision: string }>(
      `/admin/series/${id(seriesId)}/causes/${id(causeId)}/decision`,
      body,
    )
  ).data
}

export async function splitFromSeries(seriesId: string, eventId: string) {
  return (
    await post<{ series_id: string }>(`/admin/series/${id(seriesId)}/events/${id(eventId)}/split`)
  ).data
}

export async function dissolveSeries(seriesId: string) {
  return (await post<{ series_id: string }>(`/admin/series/${id(seriesId)}/dissolve`)).data
}

// ---- 公開內容 ----

export async function listPublications(
  filters: { state?: PublicationState | ''; event_id?: string } = {},
): Promise<PublicationItem[]> {
  return (await apiGet<PublicationItem[]>(`${V1}/admin/publications${query(filters)}`)).data
}

export async function listPublicationReminders(): Promise<PublicationReminder[]> {
  return (await apiGet<PublicationReminder[]>(`${V1}/admin/publications/reminders`)).data
}

/** 一鍵下架；原因可省略（空白不送），會寫入稽核紀錄。 */
export async function takeDownPublication(publicationId: string, reason = ''): Promise<PublicationChange> {
  const trimmed = reason.trim()
  return (
    await post<PublicationChange>(
      `/admin/publications/${id(publicationId)}/takedown`,
      trimmed ? { reason: trimmed } : {},
    )
  ).data
}

/** 重新上架：交回公開規則判斷，不一定會再公開。 */
export async function restorePublication(publicationId: string): Promise<PublicationChange> {
  return (await post<PublicationChange>(`/admin/publications/${id(publicationId)}/restore`)).data
}

// ---- 事件關聯 ----

/** 關聯圖；預設不含被駁回的邊。 */
export async function listRelations(
  filters: {
    event_id?: string
    type?: RelationType | ''
    decision?: RelationDecision | ''
    include_rejected?: boolean
  } = {},
): Promise<RelationGraph> {
  const { include_rejected, ...rest } = filters
  const params = { ...rest, include_rejected: include_rejected ? 'true' : null }
  return (await apiGet<RelationGraph>(`${V1}/admin/relations${query(params)}`)).data
}

/** 確認、駁回，或回滾到待確認（pending）；備註可省略（空白不送），會寫入稽核紀錄。 */
export async function decideRelation(
  relationId: string,
  decision: RelationDecision,
  note = '',
): Promise<RelationDetail> {
  const trimmed = note.trim()
  const body = trimmed ? { decision, note: trimmed } : { decision }
  return (await post<RelationDetail>(`/admin/relations/${id(relationId)}/decision`, body)).data
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

/** 韌性成績單（P6-T05）：全部事件，`windowDays` 為時間窗天數。 */
export async function getResilience(windowDays: number): Promise<Resilience> {
  return (await apiGet<Resilience>(`${V1}/admin/resilience${query({ window_days: windowDays })}`)).data
}

// ---- What-if 模擬 ----

/** 建立模擬（P7-T02）：引擎計算數值，S10 解釋假設與限制；結果是假設情境，不是成效預測。 */
export async function createScenario(body: ScenarioRequest): Promise<ScenarioRun> {
  return (await post<ScenarioRun>('/admin/scenarios', body)).data
}

/** 最近 50 次模擬（新到舊）。 */
export async function listScenarios(): Promise<ScenarioRun[]> {
  return (await apiGet<ScenarioRun[]>(`${V1}/admin/scenarios`)).data
}

// ---------- 趨勢解讀（P7-T06，S09 trend-forecaster） ----------

export type TrendOutcome = 'explained' | 'abstained' | 'failed' | 'no_anomaly' | 'insufficient_data'
export type CoverageLevel = 'full' | 'partial' | 'low'
export type TrendCaveat = 'volume_not_event_count' | 'limited_coverage' | 'short_history' | 'small_counts'
export type TrendDriver = 'incident_cluster' | 'repeated_reports' | 'coverage_change' | 'seasonal' | 'unexplained'

export interface TrendSignal {
  signal_id: string
  event_type: string
  location: string | null
  counts: { period_start: string; count: number }[]
  anomaly: { period_start: string; observed: number; expected: number; p_value: number } | null
  evidence_ids: string[]
}

export interface TrendCoverage {
  start: string
  end: string
  level: CoverageLevel
  ratio: number | null
  note: string | null
  sources: { code: string; expected_days: number; ingested_days: number; missing_days: number; added_on: string | null }[]
}

export interface TrendForecastOutput {
  status: 'ok' | 'abstain'
  explanations: {
    signal_id: string
    summary: string
    driver: TrendDriver
    basis: 'supported' | 'hypothesized'
    evidence_ids: string[]
  }[]
  outlook: { statement: string; signal_id: string | null; evidence_ids: string[] }[]
  coverage_level: CoverageLevel
  caveats: TrendCaveat[]
  evidence_ids: string[]
  reason: string
  abstain_reason: string | null
}

export interface TrendForecast {
  outcome: TrendOutcome
  freq: 'week' | 'month'
  as_of: string
  start: string
  series: TrendSignal[]
  coverage: TrendCoverage
  evidence: { evidence_id: string; event_id: string; title: string; source_code: string }[]
  forecast: {
    ai_run_id: string
    status: 'succeeded' | 'abstained' | 'failed'
    output: TrendForecastOutput | null
    abstain_reason: string | null
    error_type: string | null
    skill_version: string
  } | null
  created_by: string
  created_at: string
}

/** 最近一次趨勢解讀；從未執行時為 null。 */
export async function getTrendForecast(): Promise<TrendForecast | null> {
  return (await apiGet<TrendForecast | null>(`${V1}/admin/trends/forecast`)).data
}

/** 手動觸發一次趨勢解讀（截止日預設台北昨日）。 */
export async function runTrendForecast(freq: 'week' | 'month'): Promise<TrendForecast> {
  return (await post<TrendForecast>('/admin/trends/forecast', { freq })).data
}
