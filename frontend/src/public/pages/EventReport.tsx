import type { ReactNode } from 'react'

import { Badge, Button, Card } from '@design'

import { cn } from '../../lib/cn'

import { formatTime, label, RISK_LABELS, riskTone } from '../../admin/format'
import { Link } from '../../admin/Link'
import { navigate } from '../../admin/router'
import { fetchEvent, type PublicEventDetail, type PublicSource } from '../api'
import { ErrorText, Muted, PageIntro, SeriesText, VerificationTag } from '../ui'
import { categoryTone } from '../categories'
import { useLoad } from '../useLoad'

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <Card as="section" aria-labelledby={id} className="min-w-0 space-y-3 rounded-2xl p-5">
      <h2 id={id} className="text-lg font-bold text-foreground">
        {title}
      </h2>
      {children}
    </Card>
  )
}

/** 只連到 http(s) 網址，避免快照中的異常網址變成可執行連結。 */
function safeUrl(url: string | null): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null
  } catch {
    return null
  }
}

function SourceItem({ source }: { source: PublicSource }) {
  const href = safeUrl(source.url)
  if (!href) return <li className="text-sm text-foreground">{source.name}</li>
  return (
    <li className="text-sm">
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-4">
        {source.name}
      </a>
    </li>
  )
}

/** 「這是 XX 系列的第 N 起」與「看這系列的分析」（ADR-0020 決策 3-5、4-3），照示範頁用深色區塊。 */
function SeriesLink({ event }: { event: PublicEventDetail }) {
  const series = event.series
  if (!series) return null
  const to = `/series/${encodeURIComponent(series.id)}?from=${encodeURIComponent(event.id)}`
  const many = series.event_count > 1
  return (
    <section
      aria-labelledby="report-series"
      className="flex flex-wrap items-center justify-between gap-4 rounded-[20px] bg-foreground px-7 py-6 text-background"
    >
      <div className="flex min-w-0 flex-[1_1_260px] flex-col gap-1.5">
        <h2 id="report-series" className="text-[13px] font-normal text-background/75">
          {many ? `這是「${series.name}」系列的第 ${series.ordinal} 起` : '目前是單一事件'}
        </h2>
        <p className="text-xl font-bold">{series.name}</p>
        <p className="text-sm text-background/75">
          {many
            ? `同類型的事已經公開 ${series.event_count} 起。看看這系列發生在哪裡、有什麼規律，以及學校改善到哪了。`
            : '分析頁已經建好，之後有相似事件加入就會累積成系列。'}
        </p>
      </div>
      <Button variant="primary" onClick={() => navigate(to)}>
        看這系列的分析
        <span aria-hidden="true">→</span>
      </Button>
    </section>
  )
}

/**
 * 事件頁：快報全文（P8-T13，ADR-0020 決策 3-5）——標題、時間、地點、AI 摘要、查證標籤、官方來源、
 * 「這是 XX 系列的第 N 起」與系列分析頁的連結（P8-T06）。單一事件小報告已停用：即使舊快照仍帶
 * 小報告，也不顯示影響、建議與原因；改善進度在系列分析頁。
 */
export function EventReport({ id }: { id: string }) {
  const { data: event, error, loading } = useLoad(() => fetchEvent(id), id)
  const back = (
    <Link
      to="/"
      className="inline-flex min-h-11 items-center text-[15px] text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      ← 回快報首頁
    </Link>
  )

  if (error?.code === 'NOT_FOUND') {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        {back}
        <PageIntro title="找不到這個事件">
          <p>這則快報不存在，或可能已經下架。</p>
        </PageIntro>
      </div>
    )
  }
  if (error) {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        {back}
        <h1 className="font-display text-3xl text-foreground">快報全文</h1>
        <ErrorText>{error.message}</ErrorText>
      </div>
    )
  }
  if (loading || !event) return <Muted>載入中…</Muted>

  const tone = categoryTone(event.event_type)
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      {back}
      <article className="overflow-hidden rounded-[20px] border border-border bg-card">
        <div className={cn('flex h-[140px] flex-wrap content-center items-center gap-3 px-7', tone.tint)}>
          <span className={cn('text-[13px] font-bold', tone.ink)}>{event.label}</span>
          <VerificationTag verification={event.verification} text={event.verification_label} />
          {event.risk_level && <Badge tone={riskTone(event.risk_level)}>{`風險 ${label(RISK_LABELS, event.risk_level)}`}</Badge>}
        </div>
        <div className="space-y-3.5 p-7">
          <h1 className="text-[28px] leading-snug font-bold text-foreground">{event.title}</h1>
          <dl className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <div>
              <dt className="inline">發生日期：</dt>
              <dd className="inline">{event.occurrence_date ?? '未知'}</dd>
            </div>
            <div>
              <dt className="inline">地點：</dt>
              <dd className="inline">{event.location ?? '未知'}</dd>
            </div>
            <div>
              <dt className="inline">最後更新：</dt>
              <dd className="inline">{formatTime(event.published_at)}</dd>
            </div>
          </dl>
          {event.summary && <p className="text-[17px] leading-[1.8] text-foreground">{event.summary}</p>}
          {event.verification !== 'verified' && (
            <p className="rounded-xl bg-warning-muted px-3.5 py-3 text-sm leading-relaxed text-warning">
              尚未查證：這是 AI 依公開來源整理的摘要，目前沒有官方來源證實，也還沒有人工查證。
            </p>
          )}
          <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <SeriesText series={event.series} className={tone.ink} />
            <span>· AI 依公開來源整理</span>
          </p>
        </div>
      </article>

      <SeriesLink event={event} />

      {event.sources.length > 0 && (
        <Section id="report-sources" title="官方來源">
          <ul className="space-y-1">
            {event.sources.map((s) => (
              <SourceItem key={`${s.name}-${s.url ?? ''}`} source={s} />
            ))}
          </ul>
        </Section>
      )}
      <p className="text-sm text-muted-foreground">
        你也遇到類似的事？
        <Link to="/report" className="font-semibold text-accent underline underline-offset-4">
          匿名告訴我們
        </Link>
        ，多一份資料，分析會更準。
      </p>
    </div>
  )
}
