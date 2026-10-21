/** 管理端頁面共用的小元件（只用語意 token，兩套設計通用）。 */
import type { ReactNode } from 'react'

import { cn } from '../lib/cn'

export function PageHeader({ title, description, actions }: {
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="font-display text-2xl text-foreground">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions}
    </div>
  )
}

/** 區塊標題＋內容；以 aria-labelledby 形成可辨識的 region。 */
export function Section({ id, title, children, className }: {
  id: string
  title: string
  children: ReactNode
  className?: string
}) {
  return (
    <section aria-labelledby={id} className={cn('min-w-0', className)}>
      <h2 id={id} className="mb-2 text-sm font-semibold text-foreground">
        {title}
      </h2>
      {children}
    </section>
  )
}

export function ErrorText({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-lg border border-danger/20 bg-danger-muted px-3 py-2 text-sm text-danger">
      {children}
    </p>
  )
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="rounded-lg border border-success/20 bg-success-muted px-3 py-2 text-sm text-success">
      {children}
    </p>
  )
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>
}

/** 載入中／錯誤／空狀態的共同處理。 */
export function LoadState({ loading, error, empty, emptyText }: {
  loading: boolean
  error: string | null
  empty?: boolean
  emptyText?: string
}) {
  if (error) return <ErrorText>{error}</ErrorText>
  if (loading) return <Muted>載入中…</Muted>
  if (empty) return <Muted>{emptyText ?? '沒有資料'}</Muted>
  return null
}

/** 緊急提示與非官方聲明：清楚可見，不加任何動畫。 */
export function Disclaimer({ className }: { className?: string }) {
  return (
    <div className={cn('space-y-1 text-sm', className)}>
      <p className="font-medium text-danger">
        緊急狀況請直接撥打 110（警察）或 119（消防、救護），或聯絡學校校安中心；本系統不是緊急通報管道。
      </p>
      <p className="text-muted-foreground">個人使用的學生研究專題，非校方官方系統，內容不代表學校立場。</p>
    </div>
  )
}
