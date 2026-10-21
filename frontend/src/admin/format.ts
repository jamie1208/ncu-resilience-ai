/** 管理端的顯示格式：時間轉 Asia/Taipei、代碼轉中文、語意色調。 */
import type { BadgeTone } from '../design/contract'

const TIME_FORMAT = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Taipei',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** ISO 時間（UTC）→ `YYYY-MM-DD HH:mm`（台北時間）；缺值顯示破折號。 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const parts = Object.fromEntries(TIME_FORMAT.formatToParts(date).map((p) => [p.type, p.value]))
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
}

export function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}

/** 美元：小額保留四位小數，避免單次呼叫的費用顯示成 $0.00。 */
export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `$${value.toFixed(value < 1 ? 4 : 2)}`
}

export function label(labels: Record<string, string>, code: string): string {
  return labels[code] ?? code
}

export const STATUS_LABELS: Record<string, string> = {
  detected: '偵測到',
  analyzing: '分析中',
  unverified: '未查證',
  verified: '已查證',
  disputed: '有爭議',
  false: '不實',
  monitoring: '持續追蹤',
  resolved: '已解決',
  archived: '已封存',
}

export const STATUS_TONES: Record<string, BadgeTone> = {
  unverified: 'warning',
  verified: 'success',
  disputed: 'warning',
  false: 'danger',
  monitoring: 'info',
}

export const RISK_LABELS: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  critical: '極高',
}

export function riskTone(level: string): BadgeTone {
  if (level === 'high' || level === 'critical') return 'danger'
  return level === 'medium' ? 'warning' : 'neutral'
}

export const ALERT_LABELS: Record<string, string> = {
  candidate: '候選',
  policy_checked: '已檢查規則',
  eligible: '符合通知條件',
  blocked: '規則阻擋',
  published: '已通知',
  updated: '已更新',
  retracted: '已撤回',
  expired: '已過期',
}

export const ALERT_TONES: Record<string, BadgeTone> = {
  blocked: 'warning',
  published: 'info',
  updated: 'info',
  retracted: 'danger',
}

export const HEALTH_LABELS: Record<string, string> = {
  healthy: '正常',
  degraded: '不穩定',
  failing: '失敗',
  stale: '逾時未更新',
  disabled: '已停用',
  unknown: '未知',
}

export const HEALTH_TONES: Record<string, BadgeTone> = {
  healthy: 'success',
  degraded: 'warning',
  failing: 'danger',
  stale: 'warning',
}

/** 風險五面向（config/risk-policy.yaml 的 weights 順序）。 */
export const DIMENSION_LABELS: Record<string, string> = {
  personal_safety: '人身安全',
  operations: '營運',
  property: '財產',
  reputation: '聲譽',
  scope: '影響範圍',
}

export const SUPPORT_LABELS: Record<string, string> = {
  supports: '支持',
  contradicts: '反駁',
  mentions: '提及',
}

export const SUPPORT_TONES: Record<string, BadgeTone> = {
  supports: 'success',
  contradicts: 'danger',
}
