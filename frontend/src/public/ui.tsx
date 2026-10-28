/** 公開網站共用的小元件（只用語意 token 與 `@design` 元件，兩套設計通用）。 */
import type { ReactNode } from 'react'

import { Badge, Button, Card, SectionLabel } from '@design'

import { formatTime, label, RISK_LABELS, riskTone } from '../admin/format'
import { Link } from '../admin/Link'
import type { ActionState, FeedItem, PublicAction } from './api'

const ACTION_LABELS: Record<ActionState, string> = {
  discovered: '已發現',
  handling: '處理中',
  recovered: '已恢復',
  improved: '已改進',
  dropped: '不追蹤',
}

const ACTION_TONES: Partial<Record<ActionState, 'info' | 'success'>> = {
  handling: 'info',
  recovered: 'success',
  improved: 'success',
}

/** 頁面標題區：區塊標籤＋標題＋說明。 */
export function PageIntro({ eyebrow, title, children }: { eyebrow: string; title: string; children?: ReactNode }) {
  return (
    <header className="space-y-4">
      <SectionLabel>{eyebrow}</SectionLabel>
      <h1 className="font-display text-3xl leading-tight text-foreground sm:text-4xl">{title}</h1>
      {children && <div className="max-w-2xl text-base leading-relaxed text-muted-foreground">{children}</div>}
    </header>
  )
}

export function ErrorText({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-lg border border-danger/20 bg-danger-muted px-4 py-3 text-sm text-danger">
      {children}
    </p>
  )
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>
}

/** 列表的載入中、錯誤、空狀態與「載入更多」。 */
export function ListState({
  loading,
  error,
  empty,
  emptyText,
  loadMore,
}: {
  loading: boolean
  error: string | null
  empty: boolean
  emptyText: string
  loadMore: (() => void) | null
}) {
  if (error) return <ErrorText>{error}</ErrorText>
  if (loading) return <Muted>載入中…</Muted>
  if (loadMore) {
    return (
      <div className="flex justify-center">
        <Button variant="outline" onClick={loadMore}>
          載入更多
        </Button>
      </div>
    )
  }
  return empty ? <Muted>{emptyText}</Muted> : null
}

/** 動態卡片：已查證事件可點進小報告；安全提醒只顯示中性模板文字，沒有連結。 */
export function FeedCard({ item }: { item: FeedItem }) {
  if (item.kind === 'safety_notice') {
    return (
      <Card as="li" className="space-y-3 border-warning/40">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="warning">安全提醒</Badge>
          <Badge>{item.label}</Badge>
          <span className="text-xs text-muted-foreground">未經查證・{formatTime(item.published_at)}</span>
        </div>
        <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">{item.notice}</p>
      </Card>
    )
  }
  return (
    <Card as="li" interactive className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="success">已查證</Badge>
        <Badge>{item.label}</Badge>
        {item.risk_level && <Badge tone={riskTone(item.risk_level)}>{`風險 ${label(RISK_LABELS, item.risk_level)}`}</Badge>}
        <span className="text-xs text-muted-foreground">{formatTime(item.published_at)}</span>
      </div>
      <h3 className="text-lg font-semibold text-foreground">
        <Link
          to={`/events/${encodeURIComponent(item.id)}`}
          className="rounded hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {item.title}
        </Link>
      </h3>
      {item.summary && <p className="text-sm leading-relaxed text-muted-foreground">{item.summary}</p>}
      <p className="text-xs text-muted-foreground">
        {[item.location, item.occurrence_date].filter(Boolean).join('・') || '地點、日期未知'}
      </p>
    </Card>
  )
}

/** 改善措施列表（名稱、狀態、預計完成日）。 */
export function ActionList({ actions, labelledBy }: { actions: PublicAction[]; labelledBy?: string }) {
  return (
    <ul aria-labelledby={labelledBy} className="space-y-2">
      {actions.map((a, i) => (
        <li key={`${a.title}-${i}`} className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={ACTION_TONES[a.state]}>{label(ACTION_LABELS, a.state)}</Badge>
          <span className="min-w-0 text-foreground">{a.title}</span>
          {a.due_date && <span className="text-xs text-muted-foreground">{`預計完成 ${a.due_date}`}</span>}
        </li>
      ))}
    </ul>
  )
}

/** 每一頁的頁尾：非官方聲明與緊急聯絡方式，清楚可見，不加任何動畫（ADR-0015 決策 7）。 */
export function SiteFooter() {
  return (
    <footer className="border-t border-border bg-muted">
      <div className="mx-auto max-w-5xl space-y-3 px-4 py-8 text-sm sm:px-6">
        <p className="font-medium text-foreground">個人專題，非國立中央大學官方系統，內容不代表學校立場。</p>
        <p className="font-medium text-danger">
          緊急狀況請直接撥打 110（警察）或 119（消防、救護），或聯絡國立中央大學校安中心；本站不是緊急通報管道。
        </p>
        <p className="text-muted-foreground">
          <Link to="/about" className="underline underline-offset-4 hover:text-foreground">
            關於本站與公開規則
          </Link>
        </p>
      </div>
    </footer>
  )
}
