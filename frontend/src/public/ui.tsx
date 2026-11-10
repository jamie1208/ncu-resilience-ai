/** 公開網站共用的小元件（只用語意 token 與 `@design` 元件，兩套設計通用）。 */
import type { ReactNode } from 'react'

import { Badge, Button, Card, SectionLabel } from '@design'

import { formatTime, label, RISK_LABELS, riskTone } from '../admin/format'
import { Link } from '../admin/Link'
import { cn } from '../lib/cn'
import type { ActionState, FeedItem, PublicAction, PublicEventSummary, SeriesRef, Verification } from './api'

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

/**
 * 查證標籤（ADR-0020 決策 4-2）：文字＋框線樣式，不只靠顏色。「尚未查證」用虛線框、平實措辭
 * （ADR-0018 3-2），不得呈現為已核實。
 */
export function VerificationTag({ verification, text }: { verification: Verification; text: string }) {
  if (verification === 'verified') return <Badge tone="success">{text}</Badge>
  return (
    <span className="inline-flex items-center rounded-full border border-dashed border-warning px-2.5 py-0.5 text-xs font-medium text-warning">
      {text}
    </span>
  )
}

type Tone = 'accent' | 'info' | 'success' | 'warning' | 'danger' | 'muted'

/** 類別的圖示路徑（24×24，線條）與配色；配色只是輔助，類別一律同時寫出文字。 */
const CATEGORY_STYLES: Record<string, { tone: Tone; path: string }> = {
  facility: { tone: 'info', path: 'M14 6l4 4-8 8H6v-4zM13 7l4 4' },
  weather: { tone: 'accent', path: 'M7 17h10a4 4 0 0 0 0-8 6 6 0 0 0-11 2 3 3 0 0 0 1 6z' },
  security: { tone: 'warning', path: 'M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z' },
  traffic: { tone: 'accent', path: 'M5 16V11l2-5h10l2 5v5zM5 16v2M19 16v2M8 13h.01M16 13h.01' },
  health: { tone: 'success', path: 'M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6z' },
  fire: { tone: 'danger', path: 'M12 3c1 4 6 6 6 11a6 6 0 0 1-12 0c0-3 2-5 3-7 1 2 2 3 3 3 0-3-1-5 0-7z' },
  academic: { tone: 'info', path: 'M3 9l9-5 9 5-9 5zM7 11v5c3 2 7 2 10 0v-5' },
  infosec: { tone: 'muted', path: 'M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z' },
}

const TONE_CLASSES: Record<Tone, string> = {
  accent: 'bg-accent/10 text-accent',
  info: 'bg-info-muted text-info',
  success: 'bg-success-muted text-success',
  warning: 'bg-warning-muted text-warning',
  danger: 'bg-danger-muted text-danger',
  muted: 'bg-muted text-muted-foreground',
}

/** 類別圖示（裝飾，旁邊一定有類別文字）。 */
export function CategoryIcon({ type, className }: { type: string; className?: string }) {
  const style = CATEGORY_STYLES[type] ?? { tone: 'muted' as Tone, path: 'M12 4v16M4 12h16' }
  return (
    <span
      aria-hidden="true"
      data-category={type}
      className={cn('inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full', TONE_CLASSES[style.tone], className)}
    >
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
        <path d={style.path} />
      </svg>
    </span>
  )
}

/** 「XX 系列 · 第 N 起」；不在系列或系列只有 1 起時為「單一事件」（ADR-0020 決策 1-2）。 */
export function SeriesText({ series, className }: { series: SeriesRef | null; className?: string }) {
  const text = !series || series.event_count < 2 ? '單一事件' : `${series.name} 系列 · 第 ${series.ordinal} 起`
  return <span className={cn('text-xs font-medium text-foreground', className)}>{text}</span>
}

/** 快報卡片（首頁橫向滑動）：類別圖示與配色、查證標籤、時間與地點、標題與摘要、系列第 N 起。 */
export function NewsCard({ item }: { item: PublicEventSummary }) {
  return (
    <Card as="li" interactive className="flex w-72 shrink-0 snap-start flex-col gap-3 sm:w-80">
      <div className="flex items-center gap-2">
        <CategoryIcon type={item.event_type} />
        <VerificationTag verification={item.verification} text={item.verification_label} />
      </div>
      <h3 className="text-lg font-semibold leading-snug text-foreground">
        <Link
          to={`/events/${encodeURIComponent(item.id)}`}
          className="rounded hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {item.title}
        </Link>
      </h3>
      {item.summary && <p className="line-clamp-3 text-sm leading-relaxed text-muted-foreground">{item.summary}</p>}
      <p className="mt-auto text-xs text-muted-foreground">
        {[item.occurrence_date ?? formatTime(item.published_at), item.location].filter(Boolean).join('・')}
      </p>
      <p>
        <SeriesText series={item.series} />
      </p>
    </Card>
  )
}

/** 動態卡片（事件查詢列表）：快報可點進全文；舊的安全提醒只顯示中性模板文字，沒有連結。 */
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
        <VerificationTag verification={item.verification} text={item.verification_label} />
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
      <p>
        <SeriesText series={item.series} />
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
