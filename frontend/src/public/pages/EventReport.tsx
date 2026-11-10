import type { ReactNode } from 'react'

import { Badge, Button, Card } from '@design'

import { formatTime, label, RISK_LABELS, riskTone } from '../../admin/format'
import { Link } from '../../admin/Link'
import { navigate } from '../../admin/router'
import { fetchEvent, type PublicEventDetail, type PublicSource } from '../api'
import { CategoryIcon, ErrorText, Muted, PageIntro, SeriesText, VerificationTag } from '../ui'
import { useLoad } from '../useLoad'

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <Card as="section" aria-labelledby={id} className="min-w-0 space-y-3">
      <h2 id={id} className="text-lg font-semibold text-foreground">
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

/** 「這是 XX 系列的第 N 起」與「看這系列的分析」（ADR-0020 決策 3-5、4-3）。 */
function SeriesLink({ event }: { event: PublicEventDetail }) {
  const series = event.series
  if (!series) return null
  const to = `/series/${encodeURIComponent(series.id)}?from=${encodeURIComponent(event.id)}`
  return (
    <Card as="section" aria-labelledby="report-series" className="space-y-3">
      <h2 id="report-series" className="text-lg font-semibold text-foreground">
        {series.event_count > 1 ? `這是「${series.name}」系列的第 ${series.ordinal} 起` : '目前是單一事件'}
      </h2>
      <p className="text-sm text-muted-foreground">
        {series.event_count > 1
          ? `這個系列目前公開 ${series.event_count} 起，分析頁有每週件數、AI 看到的規律、改善建議與追蹤。`
          : '之後有同類型的事件時，會一起放進分析頁。'}
      </p>
      <Button variant="primary" size="sm" onClick={() => navigate(to)}>
        看這系列的分析
      </Button>
    </Card>
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
    <Link to="/events" className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
      ← 回事件查詢
    </Link>
  )

  if (error?.code === 'NOT_FOUND') {
    return (
      <div className="space-y-6">
        <PageIntro eyebrow="快報全文" title="找不到這個事件">
          <p>這個事件不存在，或可能已經下架。</p>
        </PageIntro>
        {back}
      </div>
    )
  }
  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="font-display text-3xl text-foreground">快報全文</h1>
        <ErrorText>{error.message}</ErrorText>
        {back}
      </div>
    )
  }
  if (loading || !event) return <Muted>載入中…</Muted>

  return (
    <article className="space-y-6">
      {back}
      <PageIntro eyebrow="快報全文" title={event.title}>
        {event.summary && <p>{event.summary}</p>}
      </PageIntro>

      <div className="flex flex-wrap items-center gap-2">
        <CategoryIcon type={event.event_type} />
        <VerificationTag verification={event.verification} text={event.verification_label} />
        <Badge>{event.label}</Badge>
        <SeriesText series={event.series} />
        {event.risk_level && <Badge tone={riskTone(event.risk_level)}>{`風險 ${label(RISK_LABELS, event.risk_level)}`}</Badge>}
      </div>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-muted-foreground">地點</dt>
        <dd className="text-foreground">{event.location ?? '未知'}</dd>
        <dt className="text-muted-foreground">發生日期</dt>
        <dd className="text-foreground">{event.occurrence_date ?? '未知'}</dd>
        <dt className="text-muted-foreground">最後更新</dt>
        <dd className="text-foreground">{formatTime(event.published_at)}</dd>
      </dl>
      {event.verification !== 'verified' && (
        <p className="text-sm text-muted-foreground">
          尚未查證：這是 AI 依公開來源整理的摘要，目前沒有官方來源證實，也還沒有人工查證。
        </p>
      )}

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
      <p className="text-sm">
        <Link to="/report" className="text-accent underline underline-offset-4">
          你也遇到類似的事？匿名告訴我們
        </Link>
      </p>
    </article>
  )
}
