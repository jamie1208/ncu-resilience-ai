import { useState, type FormEvent } from 'react'

import { Badge, Button, Card, Select, Textarea } from '@design'

import { cn } from '../../lib/cn'
import {
  analyzeEvent,
  getEvent,
  listEvents,
  VERIFY_STATUSES,
  verifyEvent,
  type DimensionScore,
  type EventDetail,
  type EventItem,
  type EventStatus,
  type Page,
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
