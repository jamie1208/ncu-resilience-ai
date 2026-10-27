import { useState, type FormEvent, type ReactNode } from 'react'

import { Badge, Button, Card, Input, Select, Textarea } from '@design'

import { cn } from '../../lib/cn'
import {
  analyzeEvent,
  createAction,
  generateReport,
  getEvent,
  listEvents,
  VERIFY_STATUSES,
  verifyEvent,
  type DimensionScore,
  type EventDetail,
  type EventItem,
  type EventReport,
  type EventStatus,
  type MeasureAudience,
  type MeasureHorizon,
  type Page,
  type ReportCause,
  type ReportMeasure,
  type RiskAssessment,
  type VerifyStatus,
} from '../api'
import {
  DIMENSION_LABELS,
  formatTime,
  label,
  RISK_LABELS,
  riskTone,
  STATUS_LABELS,
  STATUS_TONES,
  SUPPORT_LABELS,
  SUPPORT_TONES,
} from '../format'
import { Link } from '../Link'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, Notice, PageHeader, Section } from '../ui'

const FILTER_STATUSES = Object.keys(STATUS_LABELS) as EventStatus[]
const RULE_LABELS: Record<string, string> = {
  critical_override: '人身安全覆寫',
  floor: '單一面向保底',
}
const AUDIENCES: [MeasureAudience, string][] = [
  ['students', '同學可以做'],
  ['school', '學校可以做'],
]
const HORIZONS: [MeasureHorizon, string][] = [
  ['short', '短期'],
  ['mid', '中期'],
  ['long', '長期'],
]
const PRIORITY_LABELS: Record<string, string> = { high: '優先', medium: '一般', low: '次要' }
const INVALID_LABELS: Record<string, string> = { event_false: '事件已被查證為不實，這份報告已失效。' }

/** 事件工作台：左側事件列表，右側事件詳細（來源對照、風險分項、證據、查證）。 */
export function EventWorkbench({ eventId }: { eventId: string | null }) {
  return (
    <div className="space-y-4">
      <PageHeader title="事件工作台" description="比對來源、檢視風險分項，記錄查證結果。" />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className={cn(eventId && 'hidden lg:block')}>
          <EventList selectedId={eventId} />
        </div>
        {eventId ? (
          <EventPanel key={eventId} eventId={eventId} />
        ) : (
          <Card className="hidden lg:block">
            <Muted>從左側選擇一個事件。</Muted>
          </Card>
        )}
      </div>
    </div>
  )
}

function EventList({ selectedId }: { selectedId: string | null }) {
  const [status, setStatus] = useState<EventStatus | ''>('')
  const first = useLoad(() => listEvents({ status }), `events:${status}`)
  const [more, setMore] = useState<{ key: string; pages: Page<EventItem>[] }>({ key: '', pages: [] })
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState<string | null>(null)
  const message = useErrorMessage()

  // 篩選改變時捨棄之前載入的後續頁
  const extra = more.key === status ? more.pages : []
  const pages = first.data ? [first.data, ...extra] : []
  const items = pages.flatMap((p) => p.items)
  const nextCursor = pages.at(-1)?.nextCursor ?? null

  async function loadMore() {
    if (!nextCursor) return
    setLoadingMore(true)
    setMoreError(null)
    try {
      const page = await listEvents({ status, cursor: nextCursor })
      setMore({ key: status, pages: [...extra, page] })
    } catch (e) {
      setMoreError(message(e))
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <Card className="space-y-3">
      <label className="flex items-center gap-2 text-sm font-medium text-foreground">
        <span className="shrink-0">狀態</span>
        <Select value={status} onChange={(e) => setStatus(e.target.value as EventStatus | '')}>
          <option value="">全部</option>
          {FILTER_STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </Select>
      </label>
      <LoadState
        loading={first.loading}
        error={first.error}
        empty={items.length === 0}
        emptyText="沒有符合條件的事件"
      />
      {items.length > 0 && (
        <ul className="divide-y divide-border">
          {items.map((event) => (
            <li key={event.id}>
              <Link
                to={`/admin/events/${encodeURIComponent(event.id)}`}
                aria-current={event.id === selectedId ? 'true' : undefined}
                className={cn(
                  'block rounded-lg px-2 py-3 hover:bg-muted',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  event.id === selectedId && 'bg-muted',
                )}
              >
                <span className="block font-medium text-foreground">{event.title}</span>
                <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge tone={STATUS_TONES[event.status]}>{label(STATUS_LABELS, event.status)}</Badge>
                  {event.risk ? (
                    <Badge tone={riskTone(event.risk.level)}>
                      {`${label(RISK_LABELS, event.risk.level)} · ${event.risk.score.toFixed(1)}`}
                    </Badge>
                  ) : (
                    <Badge>未評估</Badge>
                  )}
                  <span>{`證據 ${event.evidence_count}`}</span>
                  <span className="font-mono">{formatTime(event.created_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {moreError && <ErrorText>{moreError}</ErrorText>}
      {nextCursor && !first.loading && (
        <Button variant="outline" size="sm" className="w-full" onClick={loadMore} disabled={loadingMore}>
          {loadingMore ? '載入中…' : '載入更多'}
        </Button>
      )}
    </Card>
  )
}

function EventPanel({ eventId }: { eventId: string }) {
  const detail = useLoad(() => getEvent(eventId), eventId)
  const event = detail.data

  return (
    <div className="min-w-0 space-y-4">
      <Link to="/admin/events" className="inline-flex h-11 items-center text-sm text-muted-foreground lg:hidden">
        ← 回事件列表
      </Link>
      <LoadState loading={detail.loading && !event} error={detail.error} />
      {event && (
        <>
          <Card className="space-y-2">
            <h2 className="text-lg font-semibold text-foreground">{event.title}</h2>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge tone={STATUS_TONES[event.status]}>{label(STATUS_LABELS, event.status)}</Badge>
              <span className="font-mono">{event.type}</span>
              {event.occurrence_start_date && <span>{`發生日 ${event.occurrence_start_date}`}</span>}
              <span>{`更新 ${formatTime(event.updated_at)}`}</span>
            </div>
          </Card>
          <SummaryCard event={event} />
          <ReportCard event={event} />
          <ActionsCard event={event} />
          <SourceComparison event={event} />
          <RiskBreakdown assessments={event.risk_assessments} />
          <VerifyForm
            event={event}
            onVerified={(status) => detail.setData((d) => (d ? { ...d, status } : d))}
          />
        </>
      )}
    </div>
  )
}

/** 事件說明：AI 依來源整理的摘要（只當純文字顯示），標明出處來源與更新時間。 */
function SummaryCard({ event }: { event: EventDetail }) {
  const summary = event.summary
  const source = event.evidence.find((ev) => ev.source_item_id === summary?.source_item_id)
  return (
    <Card>
      <Section id="summary-heading" title="事件說明">
        {!summary ? (
          <Muted>尚未產生事件說明。</Muted>
        ) : (
          <div className="space-y-2">
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground">{summary.text}</p>
            <p className="text-xs text-muted-foreground">
              {'AI 依來源整理'}
              {source && <span className="font-mono">{` · ${source.source_code}`}</span>}
              {summary.updated_at && <span className="font-mono">{` · ${formatTime(summary.updated_at)}`}</span>}
            </p>
          </div>
        )}
      </Section>
    </Card>
  )
}

/** 小報告內的子區塊（h3 標題形成 region）。 */
function SubSection({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="space-y-2">
      <h3 id={id} className="text-xs font-semibold text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  )
}

/** evidence_id（文章 id 的十六進位）對應到來源代碼，讓每一項都能回到「來源對照」。 */
function useSources(event: EventDetail) {
  const byHex = new Map(event.evidence.map((ev) => [ev.source_item_id.replaceAll('-', ''), ev.source_code]))
  return (ids: string[]) => `來源：${ids.map((i) => byHex.get(i) ?? i).join('、')}`
}

/**
 * 事件小報告（P4-T09）：可能的影響、值得查查看（推測，尚未查證）、已確認原因（附原句）、
 * 可以怎麼做（依同學／學校與短中長期分組）、建議轉給哪個單位（只供參考，系統不寄信）。
 * 所有文字來自 AI 與來源原句，只當純文字顯示。
 */
function ReportCard({ event }: { event: EventDetail }) {
  const report = event.report
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const message = useErrorMessage()
  const canGenerate = event.status !== 'false' && event.status !== 'archived'

  async function generate() {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await generateReport(event.id)
      setNotice('已排入產生小報告，完成後重新整理即可看到')
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <Section id="report-heading" title="事件小報告">
        <div className="space-y-4">
          {!report ? (
            <Muted>尚未產生事件小報告。</Muted>
          ) : (
            <ReportBody event={event} report={report} />
          )}
          {error && <ErrorText>{error}</ErrorText>}
          {notice && <Notice>{notice}</Notice>}
          {canGenerate && (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void generate()}>
              {report ? '重新產生小報告' : '產生小報告'}
            </Button>
          )}
        </div>
      </Section>
    </Card>
  )
}

function ReportBody({ event, report }: { event: EventDetail; report: EventReport }) {
  const sources = useSources(event)
  const causes = report.causes.items
  const hypothesized = causes.filter((c) => c.basis === 'hypothesized')
  const confirmed = causes.filter((c) => c.basis === 'confirmed')
  return (
    <div className="space-y-4">
      {!report.valid && (
        <p className="rounded-lg border border-border bg-muted p-3 text-sm text-foreground">
          {INVALID_LABELS[report.invalidated_reason ?? ''] ?? '這份報告已失效。'}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        {`AI 依來源整理 · ${report.trigger === 'manual' ? '手動產生' : '自動產生'}`}
        <span className="font-mono">{` · ${formatTime(report.created_at)}`}</span>
      </p>

      {report.impacts.items.length > 0 && (
        <SubSection id="report-impacts" title="可能的影響">
          <ul className="space-y-2 text-sm">
            {report.impacts.items.map((item) => (
              <li key={item.dimension} className="flex flex-wrap items-baseline gap-2">
                <span className="text-foreground">{label(DIMENSION_LABELS, item.dimension)}</span>
                <span className="font-mono text-foreground">{item.score}</span>
                {item.reason && <span className="min-w-0 text-muted-foreground">{item.reason}</span>}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">{sources(report.impacts.items[0].evidence_ids)}</p>
        </SubSection>
      )}

      {report.causes.status !== 'ok' ? (
        <SubSection id="report-causes" title="原因">
          <Muted>{report.causes.message}</Muted>
        </SubSection>
      ) : (
        <>
          {hypothesized.length > 0 && (
            <SubSection id="report-hypotheses" title="值得查查看">
              <ul className="space-y-3">
                {hypothesized.map((c) => (
                  <CauseItem key={c.cause_id} cause={c} sources={sources} />
                ))}
              </ul>
            </SubSection>
          )}
          {confirmed.length > 0 && (
            <SubSection id="report-confirmed" title="已確認原因">
              <ul className="space-y-3">
                {confirmed.map((c) => (
                  <CauseItem key={c.cause_id} cause={c} sources={sources} />
                ))}
              </ul>
            </SubSection>
          )}
        </>
      )}
      {report.missing_information.length > 0 && (
        <p className="text-xs text-muted-foreground">{`還不知道：${report.missing_information.join('、')}`}</p>
      )}

      <SubSection id="report-measures" title="可以怎麼做">
        {report.measures.status !== 'ok' ? (
          <Muted>{report.measures.message}</Muted>
        ) : (
          <MeasureGroups
            measures={report.measures.items}
            sources={sources}
            track={trackable(event, report) ? { eventId: event.id, reportId: report.id } : null}
          />
        )}
      </SubSection>

      {report.referrals.length > 0 && (
        <SubSection id="report-referrals" title="建議轉給哪個單位">
          <ul className="flex flex-wrap gap-2">
            {report.referrals.map((r) => (
              <li key={r.unit}>
                <Badge>{r.unit}</Badge>
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">只供你參考，系統不會寄信給任何單位。</p>
        </SubSection>
      )}
    </div>
  )
}

function CauseItem({ cause, sources }: { cause: ReportCause; sources: (ids: string[]) => string }) {
  const guess = cause.basis === 'hypothesized'
  return (
    <li className="rounded-lg border border-border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={guess ? 'warning' : 'success'}>{cause.label}</Badge>
        <span className="min-w-0 text-foreground">{cause.description}</span>
      </div>
      {!guess &&
        cause.quotes.map((q) => (
          <blockquote
            key={`${q.evidence_id}-${q.quote}`}
            className="mt-2 border-l-2 border-border pl-3 whitespace-pre-wrap text-foreground"
          >
            {q.quote}
          </blockquote>
        ))}
      {guess && cause.to_verify && <p className="mt-2 text-muted-foreground">{`待查：${cause.to_verify}`}</p>}
      <p className="mt-1 text-xs text-muted-foreground">{sources(cause.evidence_ids)}</p>
    </li>
  )
}

/** 有效報告、事件未被查證為不實或封存時，校方措施才能加入改善追蹤（P4-T05）。 */
function trackable(event: EventDetail, report: EventReport): boolean {
  return report.valid && event.status !== 'false' && event.status !== 'archived'
}

type Track = { eventId: string; reportId: string } | null

function MeasureGroups({
  measures,
  sources,
  track,
}: {
  measures: ReportMeasure[]
  sources: (ids: string[]) => string
  track: Track
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {AUDIENCES.map(([audience, title]) => {
        const mine = measures.filter((m) => m.audience === audience)
        if (mine.length === 0) return null
        const groupId = `report-measures-${audience}`
        return (
          <div key={audience} role="group" aria-labelledby={groupId} className="min-w-0 rounded-lg border border-border p-3">
            <h4 id={groupId} className="mb-2 text-sm font-semibold text-foreground">
              {title}
            </h4>
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
                        <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <span>{PRIORITY_LABELS[m.priority] ?? m.priority}</span>
                          {m.cause_basis === 'hypothesized' && <Badge tone="warning">針對推測原因</Badge>}
                          {m.responsible_unit && <span>{`建議單位：${m.responsible_unit}`}</span>}
                          <span>{sources(m.evidence_ids)}</span>
                        </span>
                        {track && audience === 'school' && (
                          <TrackButton track={track} index={measures.indexOf(m)} />
                        )}
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

/** 把一項校方措施加入改善追蹤；成功後改為連到改善追蹤頁。 */
function TrackButton({ track, index }: { track: NonNullable<Track>; index: number }) {
  const [busy, setBusy] = useState(false)
  const [added, setAdded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const message = useErrorMessage()

  async function add() {
    setBusy(true)
    setError(null)
    try {
      await createAction(track.eventId, { report_id: track.reportId, measure_index: index })
      setAdded(true)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-1 space-y-1">
      {added ? (
        <Link to="/admin/actions" className="text-xs text-accent underline underline-offset-4">
          已加入改善追蹤
        </Link>
      ) : (
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void add()}>
          加入改善追蹤
        </Button>
      )}
      {error && <ErrorText>{error}</ErrorText>}
    </div>
  )
}

/** 改善追蹤（P4-T05）：手動新增這個事件的改善措施；進度在改善追蹤頁更新。 */
function ActionsCard({ event }: { event: EventDetail }) {
  const [title, setTitle] = useState('')
  const [owner, setOwner] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const message = useErrorMessage()
  const closed = event.status === 'false' || event.status === 'archived'

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const body = owner.trim() ? { title: title.trim(), owner: owner.trim() } : { title: title.trim() }
      await createAction(event.id, body)
      setTitle('')
      setOwner('')
      setNotice('已新增，可到改善追蹤頁更新進度')
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <Section id="actions-heading" title="改善追蹤">
        <div className="space-y-3">
          <Link to="/admin/actions" className="text-sm text-accent underline underline-offset-4">
            到改善追蹤頁
          </Link>
          {closed ? (
            <Muted>事件已被查證為不實或已封存，不能新增改善措施。</Muted>
          ) : (
            <form aria-label="新增改善措施" className="grid gap-2 sm:grid-cols-[1fr_12rem_auto] sm:items-end" onSubmit={submit}>
              <label className="block space-y-1 text-sm text-foreground">
                <span>改善措施</span>
                <Input value={title} maxLength={200} placeholder="請勿填寫個資" onChange={(e) => setTitle(e.target.value)} />
              </label>
              <label className="block space-y-1 text-sm text-foreground">
                <span>負責人</span>
                <Input value={owner} maxLength={100} placeholder="選填" onChange={(e) => setOwner(e.target.value)} />
              </label>
              <Button type="submit" size="sm" disabled={busy || title.trim() === ''}>
                新增
              </Button>
            </form>
          )}
          {error && <ErrorText>{error}</ErrorText>}
          {notice && <Notice>{notice}</Notice>}
        </div>
      </Section>
    </Card>
  )
}

/** 來源對照：每則證據的來源、時間、支持類型與摘錄（摘錄是不可信的外部內容，只當純文字顯示）。 */
function SourceComparison({ event }: { event: EventDetail }) {
  return (
    <Card>
      <Section id="sources-heading" title="來源對照">
        {event.evidence.length === 0 ? (
          <Muted>沒有證據來源。</Muted>
        ) : (
          <ol className="space-y-3">
            {event.evidence.map((ev) => (
              <li key={ev.source_item_id} className="rounded-lg border border-border p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone={SUPPORT_TONES[ev.support_type]}>{label(SUPPORT_LABELS, ev.support_type)}</Badge>
                  <span className="font-mono text-foreground">{ev.source_code}</span>
                  <span className="font-mono text-muted-foreground">{formatTime(ev.published_at)}</span>
                </div>
                {ev.excerpt ? (
                  <blockquote className="mt-2 border-l-2 border-border pl-3 text-sm leading-relaxed whitespace-pre-wrap text-foreground">
                    {ev.excerpt}
                  </blockquote>
                ) : (
                  <p className="mt-2 text-sm text-muted-foreground">（沒有摘錄）</p>
                )}
                <a
                  href={ev.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-flex h-11 items-center text-sm text-accent underline underline-offset-4 break-all"
                >
                  開啟原文<span className="sr-only">（{ev.source_code}，另開新分頁）</span>
                </a>
              </li>
            ))}
          </ol>
        )}
      </Section>
    </Card>
  )
}

function dimensionScore(value: unknown): DimensionScore | null {
  if (typeof value !== 'object' || value === null) return null
  const { score, uncertainty } = value as Record<string, unknown>
  return typeof score === 'number' && typeof uncertainty === 'number' ? { score, uncertainty } : null
}

/** 風險分項：最新一次評估的五個面向（分數由規則引擎計算，模型只給分項）與歷史。 */
function RiskBreakdown({ assessments }: { assessments: RiskAssessment[] }) {
  const latest = assessments[0]
  const rules = Array.isArray(latest?.dimensions._rules) ? (latest.dimensions._rules as string[]) : []
  return (
    <Card>
      <Section id="risk-heading" title="風險分項">
        {!latest ? (
          <Muted>尚未評估風險。</Muted>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={riskTone(latest.level)}>{label(RISK_LABELS, latest.level)}</Badge>
              <span className="font-mono text-2xl text-foreground">{latest.score.toFixed(1)}</span>
              <span className="text-xs text-muted-foreground">
                {`規則版本 ${latest.policy_version} · ${formatTime(latest.created_at)}`}
              </span>
            </div>
            <dl className="space-y-2">
              {Object.entries(DIMENSION_LABELS).map(([key, name]) => {
                const dim = dimensionScore(latest.dimensions[key])
                return (
                  <div key={key} className="grid grid-cols-[5rem_minmax(0,1fr)_3rem] items-center gap-3 text-sm">
                    <dt className="text-foreground">{name}</dt>
                    <dd className="contents">
                      <div
                        role="meter"
                        aria-label={name}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={dim?.score ?? 0}
                        className="h-2 overflow-hidden rounded-full bg-muted"
                      >
                        <div
                          className={cn('h-full rounded-full', (dim?.score ?? 0) >= 80 ? 'bg-danger' : 'bg-accent')}
                          style={{ width: `${Math.min(100, Math.max(0, dim?.score ?? 0))}%` }}
                        />
                      </div>
                      <span className="text-right font-mono text-foreground" title={dim ? `不確定性 ${dim.uncertainty}` : undefined}>
                        {dim ? String(dim.score) : '—'}
                      </span>
                    </dd>
                  </div>
                )
              })}
            </dl>
            {rules.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {`套用規則：${rules.map((r) => `${RULE_LABELS[r] ?? r}（${r}）`).join('、')}`}
              </p>
            )}
            <div>
              <h3 className="mb-1 text-xs font-semibold text-muted-foreground">評估歷史</h3>
              <ol className="space-y-1 text-xs">
                {assessments.map((a) => (
                  <li
                    key={a.id}
                    aria-label={`評估 ${formatTime(a.created_at)}`}
                    className="flex flex-wrap items-center gap-2"
                  >
                    <span className="font-mono text-muted-foreground">{formatTime(a.created_at)}</span>
                    <Badge tone={riskTone(a.level)}>{`${label(RISK_LABELS, a.level)} · ${a.score.toFixed(1)}`}</Badge>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        )}
      </Section>
    </Card>
  )
}

function VerifyForm({ event, onVerified }: { event: EventDetail; onVerified: (status: EventStatus) => void }) {
  const initial = (VERIFY_STATUSES as readonly string[]).includes(event.status)
    ? (event.status as VerifyStatus)
    : 'unverified'
  const [status, setStatus] = useState<VerifyStatus>(initial)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const message = useErrorMessage()

  async function run(action: () => Promise<string>) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      setNotice(await action())
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    void run(async () => {
      const result = await verifyEvent(event.id, status, note)
      onVerified(result.status)
      setNote('')
      return `已更新為「${label(STATUS_LABELS, result.status)}」`
    })
  }

  return (
    <Card>
      <form aria-label="記錄查證結果" className="space-y-3" onSubmit={submit}>
        <h2 className="text-sm font-semibold text-foreground">查證</h2>
        <label className="block space-y-1 text-sm text-foreground">
          <span>查證結果</span>
          <Select value={status} onChange={(e) => setStatus(e.target.value as VerifyStatus)}>
            {VERIFY_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </Select>
        </label>
        <label className="block space-y-1 text-sm text-foreground">
          <span>備註（選填）</span>
          <Textarea
            value={note}
            maxLength={200}
            placeholder="會寫入稽核紀錄，請勿填寫個資"
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        {error && <ErrorText>{error}</ErrorText>}
        {notice && <Notice>{notice}</Notice>}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="sm" disabled={busy}>
            儲存
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={busy || event.status === 'archived'}
            onClick={() =>
              void run(async () => {
                await analyzeEvent(event.id)
                return '已排入分析佇列，完成後重新整理即可看到新的評估'
              })
            }
          >
            重新分析
          </Button>
        </div>
      </form>
    </Card>
  )
}
