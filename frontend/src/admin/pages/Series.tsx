import { useState, type FormEvent } from 'react'

import { Badge, Button, Card, Input, Select } from '@design'

import {
  addSeriesAction,
  decideSeriesCause,
  dissolveSeries,
  getSeries,
  getSeriesTimeline,
  listSeries,
  renameSeries,
  splitFromSeries,
  transitionAction,
  type SeriesAnalysis,
  type SeriesCause,
  type SeriesDetail,
  type SeriesMeasure,
  type SeriesTimeline,
  type TimelineGranularity,
  type TimelineRange,
} from '../api'
import { formatTime, label, STATUS_LABELS } from '../format'
import { Link } from '../Link'
import { navigate } from '../router'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, Notice, PageHeader, Section } from '../ui'

const CATEGORY_LABELS: Record<string, string> = {
  facility: '設施', weather: '天氣', security: '治安', traffic: '交通',
  health: '健康', fire: '火災', academic: '學業', infosec: '資安',
}
const SOURCE_LABELS: Record<string, string> = {
  first_event: '第一起事件的標題', ai: 'AI 命名', manual: '人工命名',
}
const AUDIENCE_LABELS: Record<string, string> = { students: '同學', school: '學校', units: '相關單位' }
const HORIZON_LABELS: Record<string, string> = { short: '短期', mid: '中期', long: '長期' }
const PRIORITY_LABELS: Record<string, string> = { high: '優先', medium: '一般', low: '有空再做' }
const ACTION_LABELS: Record<string, string> = {
  discovered: '發現', handling: '處理中', recovered: '已恢復', improved: '已改進', dropped: '不追蹤',
}
const GRANULARITY_LABELS: Record<TimelineGranularity, string> = { day: '日', week: '週', month: '月' }
const RANGE_LABELS: Record<TimelineRange, string> = {
  '1M': '1 個月', '3M': '3 個月', '6M': '6 個月', '1Y': '1 年', all: '全部',
}
const PATTERN_ABSTAIN: Record<string, string> = {
  too_few_events: '系列只有 1 件，還找不出規律。',
  no_pattern: 'AI 沒有看到有證據支持的共同點。',
  no_valid_pattern: 'AI 提出的規律引用不正確或只有 1 件支持，已丟棄。',
}

/** 系列（P8-T05）：同一種問題的多起事件；時間軸、地點、規律、推測原因與改善追蹤。 */
export function Series({ seriesId }: { seriesId: string | null }) {
  return seriesId ? <SeriesPage seriesId={seriesId} /> : <SeriesList />
}

function count(n: number) {
  return n <= 1 ? '單一事件' : `${n} 件`
}

function SeriesList() {
  const list = useLoad(() => listSeries(), 'series')
  const items = list.data ?? []
  return (
    <div className="space-y-4">
      <PageHeader
        title="系列"
        description="同一種問題的事件會自動歸成一個系列（不限地點），用來看多常發生、改善後有沒有變少。"
      />
      <LoadState loading={list.loading && !list.data} error={list.error} empty={items.length === 0} emptyText="還沒有任何系列" />
      {items.length > 0 && (
        <ul className="space-y-2">
          {items.map((s) => (
            <Card as="li" key={s.id} className="flex flex-wrap items-center justify-between gap-2">
              <span className="space-y-1">
                <Link to={`/admin/series/${s.id}`} className="font-semibold text-foreground underline-offset-2 hover:underline">
                  {s.title}
                </Link>
                <span className="block text-xs text-muted-foreground">
                  {`${label(CATEGORY_LABELS, s.category)} · 最近加入 ${formatTime(s.latest_joined_at)}`}
                </span>
              </span>
              <span className="flex items-center gap-2">
                <Badge tone={s.title_source === 'manual' ? 'info' : 'neutral'}>{label(SOURCE_LABELS, s.title_source)}</Badge>
                <span className="font-mono text-sm text-foreground">{count(s.event_count)}</span>
              </span>
            </Card>
          ))}
        </ul>
      )}
    </div>
  )
}

function SeriesPage({ seriesId }: { seriesId: string }) {
  const detail = useLoad(() => getSeries(seriesId), `series:${seriesId}`)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const message = useErrorMessage()
  const data = detail.data

  async function act(work: () => Promise<unknown>, done: string, after?: () => void) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await work()
      setNotice(done)
      if (after) after()
      else detail.reload()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <Link to="/admin/series" className="text-sm text-muted-foreground underline-offset-2 hover:underline">
        ← 所有系列
      </Link>
      {error && <ErrorText>{error}</ErrorText>}
      {notice && <Notice>{notice}</Notice>}
      <LoadState loading={detail.loading && !data} error={detail.error} />
      {data && (
        <>
          <PageHeader
            title={data.title}
            description={`${label(CATEGORY_LABELS, data.category)} · ${count(data.event_count)} · ${label(SOURCE_LABELS, data.title_source)}`}
          />
          <Rename
            key={data.title}
            current={data.title}
            busy={busy}
            onSubmit={(title) => act(() => renameSeries(seriesId, title), '已改名，之後 AI 不會覆蓋這個名稱')}
          />
          <Timeline seriesId={seriesId} />
          <Analysis
            data={data}
            busy={busy}
            onTrack={(index, measure) =>
              act(() => addSeriesAction(seriesId, data.analysis!.id, index), `已加入改善追蹤：${measure.action}`)
            }
            onDecide={(cause, decision, basis) =>
              act(
                () => decideSeriesCause(seriesId, cause.cause_id, data.analysis!.id, decision, basis),
                decision === 'confirmed' ? '已標為人工確認' : '已改回推測',
              )
            }
          />
          <Actions
            data={data}
            busy={busy}
            onStart={(actionId) => act(() => transitionAction(actionId, 'handling'), '已改為處理中')}
          />
          <Events
            data={data}
            busy={busy}
            onSplit={(eventId, title) =>
              act(() => splitFromSeries(seriesId, eventId), `已把「${title}」拆出，之後不會再自動歸回`)
            }
            onDissolve={() =>
              act(() => dissolveSeries(seriesId), '已解散系列', () => navigate('/admin/series'))
            }
          />
        </>
      )}
    </div>
  )
}

function Rename({ current, busy, onSubmit }: { current: string; busy: boolean; onSubmit: (title: string) => void }) {
  const [title, setTitle] = useState(current)
  function submit(e: FormEvent) {
    e.preventDefault()
    if (title.trim() && title.trim() !== current) onSubmit(title.trim())
  }
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <label className="min-w-0 flex-1 space-y-1 text-sm">
        <span className="text-muted-foreground">系列名稱</span>
        <Input value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <Button type="submit" variant="outline" size="sm" disabled={busy || !title.trim() || title.trim() === current}>
        改名
      </Button>
    </form>
  )
}

// ---------- 時間軸 ----------

function Timeline({ seriesId }: { seriesId: string }) {
  const [granularity, setGranularity] = useState<TimelineGranularity>('week')
  const [range, setRange] = useState<TimelineRange>('6M')
  const [location, setLocation] = useState('')
  const timeline = useLoad(
    () => getSeriesTimeline(seriesId, { granularity, range, location: location || null }),
    `timeline:${seriesId}:${granularity}:${range}:${location}`,
  )
  const data = timeline.data
  return (
    <Card className="space-y-3">
      <Section id="series-timeline" title="時間軸">
        <div className="mb-3 flex flex-wrap gap-3 text-sm">
          <label className="space-y-1">
            <span className="block text-muted-foreground">粒度</span>
            <Select value={granularity} onChange={(e) => setGranularity(e.target.value as TimelineGranularity)}>
              {(Object.keys(GRANULARITY_LABELS) as TimelineGranularity[]).map((g) => (
                <option key={g} value={g}>{GRANULARITY_LABELS[g]}</option>
              ))}
            </Select>
          </label>
          <label className="space-y-1">
            <span className="block text-muted-foreground">範圍</span>
            <Select value={range} onChange={(e) => setRange(e.target.value as TimelineRange)}>
              {(Object.keys(RANGE_LABELS) as TimelineRange[]).map((r) => (
                <option key={r} value={r}>{RANGE_LABELS[r]}</option>
              ))}
            </Select>
          </label>
          <label className="space-y-1">
            <span className="block text-muted-foreground">地點</span>
            <Select value={location} onChange={(e) => setLocation(e.target.value)}>
              <option value="">全部地點</option>
              {(data?.locations ?? []).map((l) => (
                <option key={l.name} value={l.name}>{`${l.name}（${l.event_count}）`}</option>
              ))}
            </Select>
          </label>
        </div>
        <LoadState loading={timeline.loading && !data} error={timeline.error} />
        {data && <Chart data={data} />}
        {data && data.markers.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            {data.markers.map((m) => (
              <li key={`${m.action_id}-${m.kind}`}>
                {`${m.kind === 'started' ? '開始處理' : '完成'}：${m.title}（${m.on}）`}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-muted-foreground">長條是每期件數，折線是 4 期移動平均；件數是被回報的數量，不等於實際發生次數。</p>
      </Section>
      {data && <Comparisons data={data} />}
    </Card>
  )
}

function Chart({ data }: { data: SeriesTimeline }) {
  const points = data.points
  const total = points.reduce((sum, p) => sum + p.count, 0)
  const max = Math.max(1, ...points.map((p) => Math.max(p.count, p.moving_average ?? 0)))
  const width = 600
  const height = 160
  const step = width / Math.max(points.length, 1)
  const y = (v: number) => height - (v / max) * (height - 10)
  const line = points
    .map((p, i) => (p.moving_average === null ? null : `${i * step + step / 2},${y(p.moving_average)}`))
    .filter(Boolean)
    .join(' ')
  const index = (day: string) => points.findIndex((p, i) => p.period_start <= day && (points[i + 1]?.period_start ?? '9999') > day)
  const unit = GRANULARITY_LABELS[data.granularity]
  return (
    <svg
      role="img"
      aria-label={`每${unit}件數，${data.start} 到 ${data.end}，共 ${total} 件`}
      viewBox={`0 0 ${width} ${height}`}
      className="h-40 w-full"
      preserveAspectRatio="none"
    >
      {points.map((p, i) => (
        <rect
          key={p.period_start}
          x={i * step + step * 0.15}
          y={y(p.count)}
          width={step * 0.7}
          height={height - y(p.count)}
          className="fill-accent"
        >
          <title>{`${p.period_start}：${p.count} 件`}</title>
        </rect>
      ))}
      {line && <polyline points={line} fill="none" strokeWidth={2} className="stroke-foreground" />}
      {data.markers.map((m) => {
        const i = index(m.on)
        if (i < 0) return null
        const x = i * step + step / 2
        return (
          <line
            key={`${m.action_id}-${m.kind}`}
            x1={x}
            x2={x}
            y1={0}
            y2={height}
            strokeDasharray={m.kind === 'started' ? '4 3' : undefined}
            strokeWidth={1.5}
            className={m.kind === 'started' ? 'stroke-warning' : 'stroke-success'}
          />
        )
      })}
    </svg>
  )
}

function Comparisons({ data }: { data: SeriesTimeline }) {
  if (data.comparisons.length === 0) return null
  return (
    <Section id="series-compare" title="改善前後比較">
      <ul className="space-y-2">
        {data.comparisons.map((c) => (
          <li key={c.action_id} className="rounded-lg border border-border px-3 py-2 text-sm">
            <p className="font-medium text-foreground">{`${c.title}（${c.start} 開始處理）`}</p>
            {c.status === 'ok' ? (
              <p className="text-foreground">
                {`前 8 週平均每週 ${c.before_weekly_average!.toFixed(1)} 件 → 之後平均每週 ${c.after_weekly_average!.toFixed(1)} 件（${c.change_ratio! <= 0 ? '少了' : '多了'} ${Math.abs(Math.round(c.change_ratio! * 100))}%）`}
              </p>
            ) : (
              <p className="text-foreground">{c.message}</p>
            )}
            <p className="text-xs text-muted-foreground">{`之前 ${c.before_events} 件、之後 ${c.after_events} 件（任一邊少於 5 件不計算比例）`}</p>
            <p className="text-xs text-muted-foreground">{c.caveat}</p>
          </li>
        ))}
      </ul>
    </Section>
  )
}

// ---------- 分析 ----------

function Analysis({ data, busy, onTrack, onDecide }: {
  data: SeriesDetail
  busy: boolean
  onTrack: (index: number, measure: SeriesMeasure) => void
  onDecide: (cause: SeriesCause, decision: 'confirmed' | 'hypothesized', basis?: string) => void
}) {
  const analysis = data.analysis
  if (!analysis) {
    const attempt = data.latest_attempt
    return (
      <Card>
        <Muted>
          {attempt
            ? `還沒有系列分析：最近一次（${formatTime(attempt.created_at)}）${attempt.status === 'failed' ? '分析失敗' : 'AI 判斷證據不足'}，有新事件加入時會再分析。`
            : '還沒有系列分析：有新事件加入後會自動分析（每個系列每天最多一次）。'}
        </Muted>
      </Card>
    )
  }
  const titles = new Map(data.events.map((e) => [e.id, e.title]))
  return (
    <div className="space-y-4">
      <Card className="space-y-2">
        {analysis.summary && <p className="text-sm text-foreground">{analysis.summary}</p>}
        <p className="text-xs text-muted-foreground">
          {`AI 分析於 ${formatTime(analysis.created_at)}`}
          {analysis.dropped > 0 && `；有 ${analysis.dropped} 項引用不正確，已丟棄`}
        </p>
        {analysis.bursts.length > 0 && (
          <ul aria-label="突增" className="space-y-1 text-sm">
            {analysis.bursts.map((b) => (
              <li key={b.burst_id}>{`${b.period_start} 那週 ${b.count} 件（平常約 ${b.expected.toFixed(1)} 件）：${b.explanation}`}</li>
            ))}
          </ul>
        )}
      </Card>
      <Card>
        <Section id="series-places" title="發生地點">
          {analysis.location_groups.length === 0 ? (
            <Muted>沒有地點資料。</Muted>
          ) : (
            <ul className="space-y-2">
              {analysis.location_groups.map((g) => (
                <li key={g.label} className="text-sm">
                  <span className="font-medium text-foreground">{g.label}</span>{' '}
                  <span className="font-mono text-xs text-muted-foreground">{`${g.total} 件中有 ${g.event_count} 件`}</span>
                  <span className="block text-xs text-muted-foreground">{g.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </Card>
      <Card>
        <Section id="series-patterns" title="AI 看到的規律">
          {analysis.patterns.length === 0 ? (
            <Muted>{PATTERN_ABSTAIN[analysis.pattern_abstain_reason ?? ''] ?? '沒有找到規律。'}</Muted>
          ) : (
            <ul className="space-y-3">
              {analysis.patterns.map((p) => (
                <li key={p.pattern_id} className="space-y-1 text-sm">
                  <Finding text={p.description} tag={p.label} confirmed={false} share={`${p.total} 件中有 ${p.event_count} 件`} />
                  <Quotes quotes={p.quotes.map((q) => q.quote)} events={p.event_ids.map((e) => titles.get(e) ?? e)} />
                  {p.mentions_party && <PartyNote />}
                </li>
              ))}
            </ul>
          )}
        </Section>
      </Card>
      <Card>
        <Section id="series-causes" title="可能原因">
          {analysis.causes.length === 0 ? (
            <Muted>證據中沒有原因的線索。</Muted>
          ) : (
            <ul className="space-y-3">
              {analysis.causes.map((c) => (
                <li key={c.cause_id} className="space-y-1 text-sm">
                  <Finding text={c.description} tag={c.label} confirmed={c.decision === 'confirmed'} share={`${c.total} 件中有 ${c.event_count} 件`} />
                  {c.to_verify && <p className="text-xs text-muted-foreground">{`待查證：${c.to_verify}`}</p>}
                  <Quotes quotes={c.quotes.map((q) => q.quote)} events={c.event_ids.map((e) => titles.get(e) ?? e)} />
                  {c.mentions_party && <PartyNote />}
                  <Decide cause={c} busy={busy} onDecide={onDecide} />
                </li>
              ))}
            </ul>
          )}
        </Section>
      </Card>
      <Measures analysis={analysis} busy={busy} onTrack={onTrack} />
    </div>
  )
}

function Finding({ text, tag, confirmed, share }: { text: string; tag: string; confirmed: boolean; share: string }) {
  return (
    <p className="flex flex-wrap items-center gap-2">
      <span className="text-foreground">{text}</span>
      <Badge tone={confirmed ? 'success' : 'warning'}>{tag}</Badge>
      <span className="font-mono text-xs text-muted-foreground">{share}</span>
    </p>
  )
}

function Quotes({ quotes, events }: { quotes: string[]; events: string[] }) {
  return (
    <div className="text-xs text-muted-foreground">
      {quotes.map((q) => (
        <span key={q} className="mr-2">{`「${q}」`}</span>
      ))}
      <span className="block">{`相關事件：${events.join('、')}`}</span>
    </div>
  )
}

function PartyNote() {
  return <p className="text-xs font-medium text-warning">提到特定個人、群體、店家或單位，公開前請確認</p>
}

function Decide({ cause, busy, onDecide }: {
  cause: SeriesCause
  busy: boolean
  onDecide: (cause: SeriesCause, decision: 'confirmed' | 'hypothesized', basis?: string) => void
}) {
  const [basis, setBasis] = useState('')
  if (cause.decision === 'confirmed') {
    return (
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => onDecide(cause, 'hypothesized')}>
        改回推測
      </Button>
    )
  }
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (basis.trim()) onDecide(cause, 'confirmed', basis)
      }}
    >
      <label className="min-w-0 flex-1 space-y-1 text-xs">
        <span className="text-muted-foreground">確認依據</span>
        <Input value={basis} maxLength={200} placeholder="例如：總務處回覆確認" onChange={(e) => setBasis(e.target.value)} />
      </label>
      <Button type="submit" variant="outline" size="sm" disabled={busy || !basis.trim()}>
        標為已確認
      </Button>
    </form>
  )
}

function Measures({ analysis, busy, onTrack }: {
  analysis: SeriesAnalysis
  busy: boolean
  onTrack: (index: number, measure: SeriesMeasure) => void
}) {
  return (
    <Card>
      <Section id="series-measures" title="改善建議">
        {analysis.measures.length === 0 ? (
          <Muted>沒有改善建議。</Muted>
        ) : (
          <ul className="space-y-2">
            {analysis.measures.map((m, i) => (
              <li key={`${i}-${m.action}`} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="space-y-1">
                  <span className="block text-foreground">{m.action}</span>
                  <span className="flex flex-wrap gap-1 text-xs text-muted-foreground">
                    <Badge tone="neutral">{label(AUDIENCE_LABELS, m.audience)}</Badge>
                    <span>{`${label(HORIZON_LABELS, m.horizon)} · ${label(PRIORITY_LABELS, m.priority)}`}</span>
                    {m.responsible_unit && <span>{`· 建議由${m.responsible_unit}處理`}</span>}
                  </span>
                </span>
                {m.tracked_action_id ? (
                  <Badge tone="info">已在追蹤</Badge>
                ) : m.audience !== 'students' ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    aria-label={`加入追蹤：${m.action}`}
                    onClick={() => onTrack(i, m)}
                  >
                    加入追蹤
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </Card>
  )
}

// ---------- 措施與事件 ----------

function Actions({ data, busy, onStart }: { data: SeriesDetail; busy: boolean; onStart: (actionId: string) => void }) {
  return (
    <Card>
      <Section id="series-actions" title="改善追蹤">
        {data.actions.length === 0 ? (
          <Muted>還沒有追蹤中的措施；可以從上面的建議一鍵加入。</Muted>
        ) : (
          <ul className="space-y-2">
            {data.actions.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                  <span className="text-foreground">{a.title}</span>
                  {a.owner && <span className="ml-2 text-xs text-muted-foreground">{a.owner}</span>}
                </span>
                <span className="flex items-center gap-2">
                  <Badge tone={a.overdue ? 'danger' : 'neutral'}>{`${label(ACTION_LABELS, a.state)}${a.overdue ? '（逾期）' : ''}`}</Badge>
                  {a.state === 'discovered' && (
                    <Button variant="ghost" size="sm" disabled={busy} onClick={() => onStart(a.id)}>
                      開始處理
                    </Button>
                  )}
                  <Link to="/admin/actions" className="text-xs text-muted-foreground underline">
                    更新狀態
                  </Link>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </Card>
  )
}

function Events({ data, busy, onSplit, onDissolve }: {
  data: SeriesDetail
  busy: boolean
  onSplit: (eventId: string, title: string) => void
  onDissolve: () => void
}) {
  const many = data.events.length > 1
  return (
    <Card>
      <Section id="series-events" title="這個系列的每一起">
        <ul className="divide-y divide-border">
          {data.events.map((e) => (
            <li key={e.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <span>
                <Link to={`/admin/events/${e.id}`} className="text-foreground underline-offset-2 hover:underline">
                  {e.title}
                </Link>
                <span className="ml-2 text-xs text-muted-foreground">
                  {`${e.occurrence_start_date ?? formatTime(e.created_at)} · ${label(STATUS_LABELS, e.status)}`}
                </span>
              </span>
              {many && (
                <Button variant="ghost" size="sm" disabled={busy} aria-label={`把「${e.title}」拆出系列`} onClick={() => onSplit(e.id, e.title)}>
                  拆出
                </Button>
              )}
            </li>
          ))}
        </ul>
        {many && (
          <div className="mt-3">
            <Button variant="outline" size="sm" disabled={busy} onClick={onDissolve}>
              解散系列
            </Button>
          </div>
        )}
      </Section>
    </Card>
  )
}
