/**
 * 公開 API（`/api/v1/public/*`）的型別與呼叫函式：P5-T11 唯讀內容、P5-T13 匿名回報、
 * P8-T06 系列分析頁。只讀取公開規則（P5-T10）判定可公開、已遮蔽 PII 的快照；`id` 是 publication ID，
 * 不是事件或系列 ID。
 */
import { apiGet, apiRequest } from '../api/client'
import type { Resilience } from '../lib/resilience'

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'
export type ActionState = 'discovered' | 'handling' | 'recovered' | 'improved' | 'dropped'

interface PublicBase {
  id: string
  version: number
  published_at: string
  event_type: string
  label: string
}

/** 查證狀態（P8-T06）：只有官方來源或使用者查證為 true 才是 verified，其他一律標「尚未查證」。 */
export type Verification = 'verified' | 'unverified' | 'disputed'

/** 快報所屬的公開系列：第幾起、目前公開的件數。 */
export interface SeriesRef {
  id: string
  name: string
  ordinal: number
  event_count: number
}

/** 一則快報（事件內容）；P8-T06 起未查證的事件也公開 AI 摘要。 */
export interface PublicEventSummary extends PublicBase {
  kind: 'content'
  title: string
  summary: string | null
  location: string | null
  occurrence_date: string | null
  risk_level: RiskLevel | null
  verification: Verification
  verification_label: string
  series: SeriesRef | null
}

/** 未經證實事件的中性安全提醒：只有類別與模板文字。 */
export interface PublicNotice extends PublicBase {
  kind: 'safety_notice'
  notice: string
}

export type FeedItem = PublicEventSummary | PublicNotice

export interface PublicImpact {
  dimension: string
  score: number | null
  uncertainty: number | null
  reason: string | null
}

export interface PublicMeasure {
  action: string
  audience: 'students' | 'school' | string
  horizon: 'short' | 'mid' | 'long' | string
  priority: string
  kind: string | null
  responsible_unit: string | null
}

export interface PublicSource {
  name: string
  url: string | null
}

export interface PublicAction {
  title: string
  state: ActionState
  due_date: string | null
  updated_at: string
}

/** 快報全文：單一事件小報告已停用（P8-T13），`report` 一律為 null；改善進度在系列分析頁。 */
export interface PublicEventDetail extends PublicEventSummary {
  report: { impacts: PublicImpact[]; measures: PublicMeasure[] } | null
  sources: PublicSource[]
}

/** 系列分析頁的一起子事件；`id` 是事件的 publication ID。 */
export interface PublicSeriesEvent {
  id: string
  ordinal: number
  title: string
  occurrence_date: string | null
  location: string | null
  verification: Verification
  verification_label: string
}

export interface PublicFinding {
  description: string
  /** 「推測，尚未查證」或「已確認」。 */
  label: string
  related_ids: string[]
  event_count: number
  total: number
}

export interface PublicCause extends PublicFinding {
  decision: 'confirmed' | 'speculative'
}

export interface PublicComparison {
  title: string
  start: string
  status: 'ok' | 'insufficient'
  before_events: number
  after_events: number
  before_weekly_average: number | null
  after_weekly_average: number | null
  message: string | null
  caveat: string
}

export interface PublicTimelinePoint {
  period_start: string
  count: number
  moving_average: number | null
}

/** 系列分析頁（P8-T06）：件數、時間軸與前後比較由程式只依公開中的子事件計算。 */
export interface PublicSeries {
  id: string
  kind: 'series'
  version: number
  published_at: string
  event_type: string
  label: string
  name: string
  summary: string | null
  event_count: number
  first_date: string | null
  latest_date: string | null
  events: PublicSeriesEvent[]
  timeline: {
    granularity: 'week'
    points: PublicTimelinePoint[]
    markers: { title: string; kind: 'started' | 'completed'; on: string }[]
  }
  bursts: { period_start: string; count: number; expected: number }[]
  location_groups: { label: string; related_ids: string[]; event_count: number; total: number }[]
  pattern_status: 'ok' | 'abstained'
  pattern_note: string | null
  patterns: PublicFinding[]
  causes: PublicCause[]
  measures: PublicMeasure[]
  progress: PublicAction[]
  comparisons: PublicComparison[]
}

export interface PublicCategory {
  event_type: string
  label: string
  content: number
  safety_notice: number
}

/** 改善進度（P8-T06 起以系列為單位）：`id` 是系列的 publication ID，`title` 是系列名稱。 */
export interface PublicProgress {
  id: string
  kind: 'series'
  version: number
  published_at: string
  event_type: string
  label: string
  title: string
  occurrence_date: string | null
  counts: Partial<Record<ActionState, number>>
  progress: PublicAction[]
}

export interface Page<T> {
  items: T[]
  nextCursor: string | null
}

const BASE = '/api/v1/public'

function query(params: Record<string, string | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value)
  const text = search.toString()
  return text ? `?${text}` : ''
}

async function page<T>(path: string, params: Record<string, string | null | undefined>): Promise<Page<T>> {
  const { data, meta } = await apiGet<T[]>(`${BASE}${path}${query(params)}`)
  const next = meta.next_cursor
  return { items: data, nextCursor: typeof next === 'string' ? next : null }
}

/** 列表查詢：事件類別篩選、關鍵字搜尋（標題、摘要、地點）與 cursor 分頁。 */
export interface ListParams {
  type?: string | null
  q?: string | null
  cursor?: string | null
  limit?: number | null
}

const params = ({ limit, ...p }: ListParams) => ({ ...p, limit: limit ? String(limit) : null })

export const fetchFeed = (p: ListParams = {}) => page<FeedItem>('/feed', params(p))
export const fetchNotices = (p: ListParams = {}) => page<PublicNotice>('/notices', params(p))
export const fetchEvents = (p: ListParams = {}) => page<PublicEventSummary>('/events', params(p))
export const fetchProgress = (p: ListParams = {}) => page<PublicProgress>('/progress', params(p))

export async function fetchCategories(): Promise<PublicCategory[]> {
  return (await apiGet<PublicCategory[]>(`${BASE}/categories`)).data
}

export async function fetchEvent(id: string): Promise<PublicEventDetail> {
  return (await apiGet<PublicEventDetail>(`${BASE}/events/${encodeURIComponent(id)}`)).data
}

export async function fetchSeries(id: string): Promise<PublicSeries> {
  return (await apiGet<PublicSeries>(`${BASE}/series/${encodeURIComponent(id)}`)).data
}

/** 匿名回報（P5-T13）：`content` 10–2000 字，`location` 選填、最多 100 字。 */
export interface ReportInput {
  content: string
  location?: string
}

/** 送出匿名回報；不帶 cookie（端點不需要登入，也不讀取任何身分資訊）。回傳只出現一次的收據碼（P8-T08）。 */
export async function submitReport(input: ReportInput): Promise<{ receiptCode: string | null }> {
  const res = await apiRequest<{ status: string; receipt_code?: string }>(`${BASE}/reports`, {
    method: 'POST',
    credentials: 'omit',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return { receiptCode: res.data?.receipt_code ?? null }
}

/** 匿名回報的處理狀態（P8-T08）：只有這三種，不含投稿內容。 */
export type ReportStatus = 'received' | 'processing' | 'handled'

/** 以收據碼查詢處理狀態；收據碼放在標頭，不進網址，也不快取。 */
export async function fetchReportStatus(receiptCode: string): Promise<ReportStatus> {
  const res = await apiRequest<{ status: ReportStatus }>(`${BASE}/reports/status`, {
    credentials: 'omit',
    cache: 'no-store',
    headers: { 'X-Receipt-Code': receiptCode },
  })
  return res.data.status
}

/** 補件（P8-T08）：與投稿同樣的字數與地點限制。 */
export interface SupplementInput extends ReportInput {
  receipt_code: string
}

export async function submitSupplement(input: SupplementInput): Promise<void> {
  await apiRequest<{ status: string }>(`${BASE}/reports/supplements`, {
    method: 'POST',
    credentials: 'omit',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/** 韌性成績單（P6-T05）：只統計公開中的事件，資料不足的分數為 null。 */
export async function fetchResilience(): Promise<Resilience> {
  return (await apiGet<Resilience>(`${BASE}/resilience`)).data
}
