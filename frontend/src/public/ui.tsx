/** 公開網站共用的小元件（只用語意 token 與 `@design` 元件，兩套設計通用）。 */
import type { ReactNode } from 'react'

import { Badge, Button, Card } from '@design'

import { formatTime, label, RISK_LABELS, riskTone } from '../admin/format'
import { Link } from '../admin/Link'
import { cn } from '../lib/cn'
import type { ActionState, FeedItem, PublicAction, PublicEventSummary, SeriesRef, Verification } from './api'
import { categoryStyle, categoryTone } from './categories'

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

/** 頁面標題區：選填的小標（彩色粗體字）＋大標題＋說明。 */
export function PageIntro({
  eyebrow,
  title,
  children,
  eyebrowClassName,
}: {
  eyebrow?: ReactNode
  title: string
  children?: ReactNode
  eyebrowClassName?: string
}) {
  return (
    <header className="space-y-2.5">
      {eyebrow && <p className={cn('text-[13px] font-bold text-accent', eyebrowClassName)}>{eyebrow}</p>}
      <h1 className="font-display text-[34px] leading-tight text-foreground sm:text-[40px]">{title}</h1>
      {children && <div className="max-w-3xl space-y-2 text-base leading-[1.75] text-foreground/80">{children}</div>}
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
    <span className="inline-flex items-center rounded-full border border-dashed border-warning bg-warning-muted px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap text-warning">
      {text}
    </span>
  )
}

/** 類別圖示（裝飾，旁邊一定有類別文字）；`size="lg"` 用在卡片色塊。 */
export function CategoryIcon({ type, className, size = 'sm' }: { type: string; className?: string; size?: 'sm' | 'lg' }) {
  const style = categoryStyle(type)
  const tone = categoryTone(type)
  if (size === 'lg') {
    return (
      <svg
        aria-hidden="true"
        data-category={type}
        viewBox="0 0 24 24"
        className={cn('h-[52px] w-[52px]', tone.ink, className)}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d={style.path} />
      </svg>
    )
  }
  return (
    <span
      aria-hidden="true"
      data-category={type}
      className={cn('inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full', tone.tint, tone.ink, className)}
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
  return <span className={cn('text-xs font-semibold text-foreground', className)}>{text}</span>
}

/**
 * 快報卡片（首頁橫向滑動，照示範頁）：上方類別色塊＋大圖示，查證標籤疊在色塊左上；
 * 下方時間與地點、標題、摘要、系列第 N 起。整張卡片可點（標題連結延伸到整張卡）。
 */
export function NewsCard({ item }: { item: PublicEventSummary }) {
  const tone = categoryTone(item.event_type)
  return (
    <Card
      as="li"
      interactive
      className="relative flex w-[280px] shrink-0 snap-start flex-col overflow-hidden rounded-2xl p-0 hover:shadow-lg focus-within:ring-2 focus-within:ring-ring"
    >
      <div className={cn('relative flex h-[120px] items-center justify-center', tone.tint)}>
        <CategoryIcon type={item.event_type} size="lg" />
        <span className="absolute top-3 left-3">
          <VerificationTag verification={item.verification} text={item.verification_label} />
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <p className="text-xs text-muted-foreground">
          {[item.occurrence_date ?? formatTime(item.published_at), item.location].filter(Boolean).join('・')}
        </p>
        <h3 className="text-[17px] leading-snug font-bold text-foreground">
          <Link
            to={`/events/${encodeURIComponent(item.id)}`}
            className="after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
          >
            {item.title}
          </Link>
        </h3>
        {item.summary && <p className="line-clamp-3 text-sm leading-relaxed text-foreground/80">{item.summary}</p>}
        <p className="mt-auto pt-1.5">
          <SeriesText series={item.series} className={tone.ink} />
        </p>
      </div>
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

const FOOTER_LINKS = [
  { to: '/events', label: '事件查詢' },
  { to: '/progress', label: '改善進度' },
  { to: '/about', label: '關於本站' },
] as const

/** 每一頁的頁尾：非官方聲明與緊急聯絡方式，清楚可見，不加任何動畫（ADR-0015 決策 7）；附導覽列以外的頁面連結。 */
export function SiteFooter() {
  return (
    <footer className="border-t border-border bg-muted">
      <div className="mx-auto max-w-6xl space-y-3 px-4 py-8 text-sm">
        <nav aria-label="更多頁面" className="flex flex-wrap gap-x-5 gap-y-1">
          {FOOTER_LINKS.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="inline-flex min-h-11 items-center font-semibold text-foreground underline-offset-4 hover:underline"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <p className="font-medium text-foreground">個人專題，非國立中央大學官方系統，內容不代表學校立場。</p>
        <p className="font-medium text-danger">
          緊急狀況請直接撥打 110（警察）或 119（消防、救護），或聯絡國立中央大學校安中心；本站不是緊急通報管道。
        </p>
      </div>
    </footer>
  )
}
