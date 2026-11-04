/**
 * 公開 API（`/api/v1/public/*`）的型別與呼叫函式：P5-T11 唯讀內容、P5-T13 匿名回報。
 * 只讀取公開規則（P5-T10）判定可公開、已遮蔽 PII 的快照；`id` 是 publication ID，不是事件 ID。
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

/** 已查證事件的摘要（事件內容）。 */
export interface PublicEventSummary extends PublicBase {
  kind: 'content'
  title: string
  summary: string | null
  location: string | null
  occurrence_date: string | null
  risk_level: RiskLevel | null
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

/** 公開版小報告：只有影響與建議，沒有任何推測原因。 */
export interface PublicEventDetail extends PublicEventSummary {
  report: { impacts: PublicImpact[]; measures: PublicMeasure[] } | null
  sources: PublicSource[]
  progress: PublicAction[]
}

export interface PublicCategory {
  event_type: string
  label: string
  content: number
  safety_notice: number
}

export interface PublicProgress {
  id: string
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

/** 列表查詢：事件類別篩選與 cursor 分頁。 */
export interface ListParams {
  type?: string | null
  cursor?: string | null
}

export const fetchFeed = (p: ListParams = {}) => page<FeedItem>('/feed', { ...p })
export const fetchNotices = (p: ListParams = {}) => page<PublicNotice>('/notices', { ...p })
export const fetchEvents = (p: ListParams = {}) => page<PublicEventSummary>('/events', { ...p })
export const fetchProgress = (p: ListParams = {}) => page<PublicProgress>('/progress', { ...p })

export async function fetchCategories(): Promise<PublicCategory[]> {
  return (await apiGet<PublicCategory[]>(`${BASE}/categories`)).data
}

export async function fetchEvent(id: string): Promise<PublicEventDetail> {
  return (await apiGet<PublicEventDetail>(`${BASE}/events/${encodeURIComponent(id)}`)).data
}

/** 匿名回報（P5-T13）：`content` 10–2000 字，`location` 選填、最多 100 字。 */
export interface ReportInput {
  content: string
  location?: string
}

/** 送出匿名回報；不帶 cookie（端點不需要登入，也不讀取任何身分資訊）。 */
export async function submitReport(input: ReportInput): Promise<void> {
  await apiRequest<{ status: string }>(`${BASE}/reports`, {
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
