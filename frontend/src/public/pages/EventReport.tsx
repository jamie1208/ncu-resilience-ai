import type { ReactNode } from 'react'

import { Badge, Card } from '@design'

import { DIMENSION_LABELS, formatTime, label, RISK_LABELS, riskTone } from '../../admin/format'
import { Link } from '../../admin/Link'
import { fetchEvent, type PublicMeasure, type PublicSource } from '../api'
import { ActionList, ErrorText, Muted, PageIntro } from '../ui'
import { useLoad } from '../useLoad'

const AUDIENCES: [string, string][] = [
  ['students', '同學可以做'],
  ['school', '學校可以做'],
]
const HORIZONS: [string, string][] = [
  ['short', '短期'],
  ['mid', '中期'],
  ['long', '長期'],
]
const PRIORITY_LABELS: Record<string, string> = { high: '優先', medium: '一般', low: '次要' }

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

function Measures({ measures }: { measures: PublicMeasure[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {AUDIENCES.map(([audience, title]) => {
        const mine = measures.filter((m) => m.audience === audience)
        if (mine.length === 0) return null
        const groupId = `measures-${audience}`
        return (
          <div key={audience} role="group" aria-labelledby={groupId} className="min-w-0 rounded-lg border border-border p-3">
            <h3 id={groupId} className="mb-2 text-sm font-semibold text-foreground">
              {title}
            </h3>
            {HORIZONS.map(([horizon, name]) => {
              const items = mine.filter((m) => m.horizon === horizon)
              if (items.length === 0) return null
              const listId = `${groupId}-${horizon}`
              return (
                <div key={horizon} className="mb-2 last:mb-0">
                  <p id={listId} className="text-xs text-muted-foreground">
                    {name}
                  </p>
                  <ul aria-labelledby={listId} className="space-y-2 text-sm">
                    {items.map((m) => (
                      <li key={m.action} className="text-foreground">
                        <span>{m.action}</span>
                        <span className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                          <span>{PRIORITY_LABELS[m.priority] ?? m.priority}</span>
                          {m.responsible_unit && <span>{`建議單位：${m.responsible_unit}`}</span>}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

/** 事件頁：公開版小報告（影響、建議、官方來源、改善進度）；不含任何原因推測（ADR-0015）。 */
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
        <PageIntro eyebrow="事件小報告" title="找不到這個事件">
          <p>這個事件不存在，或可能已經下架、改為只公開安全提醒。</p>
        </PageIntro>
        {back}
      </div>
    )
  }
  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="font-display text-3xl text-foreground">事件小報告</h1>
        <ErrorText>{error.message}</ErrorText>
        {back}
      </div>
    )
  }
  if (loading || !event) return <Muted>載入中…</Muted>

  const report = event.report
  return (
    <article className="space-y-6">
      {back}
      <PageIntro eyebrow="事件小報告" title={event.title}>
        {event.summary && <p>{event.summary}</p>}
      </PageIntro>

      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="success">已查證</Badge>
        <Badge>{event.label}</Badge>
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

      {!report && <Muted>這個事件尚未產生公開版小報告。</Muted>}
      {report && report.impacts.length > 0 && (
        <Section id="report-impacts" title="可能的影響">
          <ul className="space-y-2 text-sm">
            {report.impacts.map((item) => (
              <li key={item.dimension} className="flex flex-wrap items-baseline gap-2">
                <span className="font-medium text-foreground">{label(DIMENSION_LABELS, item.dimension)}</span>
                {item.reason && <span className="min-w-0 text-muted-foreground">{item.reason}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {report && report.measures.length > 0 && (
        <Section id="report-measures" title="建議">
          <Measures measures={report.measures} />
        </Section>
      )}
      {event.sources.length > 0 && (
        <Section id="report-sources" title="官方來源">
          <ul className="space-y-1">
            {event.sources.map((s) => (
              <SourceItem key={`${s.name}-${s.url ?? ''}`} source={s} />
            ))}
          </ul>
        </Section>
      )}
      {event.progress.length > 0 && (
        <Section id="report-progress" title="改善進度">
          <ActionList actions={event.progress} />
        </Section>
      )}
    </article>
  )
}
