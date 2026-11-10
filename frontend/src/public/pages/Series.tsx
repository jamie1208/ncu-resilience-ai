import { useState, type ReactNode } from 'react'

import { Badge, Button, Card } from '@design'

import { formatTime, label } from '../../admin/format'
import { Link } from '../../admin/Link'
import { cn } from '../../lib/cn'
import {
  fetchSeries,
  type PublicComparison,
  type PublicFinding,
  type PublicMeasure,
  type PublicSeries,
  type PublicTimelinePoint,
} from '../api'
import { ActionList, CategoryIcon, ErrorText, Muted, PageIntro, VerificationTag } from '../ui'
import { useLoad } from '../useLoad'

const AUDIENCES: [string, string][] = [
  ['students', '給同學'],
  ['school', '給學校'],
  ['units', '給相關單位'],
]
const HORIZON_LABELS: Record<string, string> = { short: '短期', mid: '中期', long: '長期' }
const PRIORITY_LABELS: Record<string, string> = { high: '優先', medium: '一般', low: '之後再做' }

type Range = '1M' | '3M' | '6M' | '1Y' | 'all'
const RANGES: [Range, string, number | null][] = [
  ['1M', '1 個月', 5],
  ['3M', '3 個月', 13],
  ['6M', '6 個月', 26],
  ['1Y', '1 年', 52],
  ['all', '全部', null],
]
const CONFIRMED = '已確認'

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

/** 規律與原因的標籤：「已確認」為實心，「推測，尚未查證」為虛線框，文字一律顯示。 */
function FindingTag({ text }: { text: string }) {
  return <VerificationTag verification={text === CONFIRMED ? 'verified' : 'unverified'} text={text} />
}

function Chart({ points, markers }: { points: PublicTimelinePoint[]; markers: PublicSeries['timeline']['markers'] }) {
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
  const index = (day: string) =>
    points.findIndex((p, i) => p.period_start <= day && (points[i + 1]?.period_start ?? '9999') > day)
  const first = points[0]?.period_start ?? ''
  const last = points.at(-1)?.period_start ?? ''
  return (
    <svg
      role="img"
      aria-label={`每週件數，${first} 起到 ${last} 這週，共 ${total} 起`}
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
          <title>{`${p.period_start} 這週：${p.count} 起`}</title>
        </rect>
      ))}
      {line && <polyline points={line} fill="none" strokeWidth={2} className="stroke-foreground" />}
      {markers.map((m) => {
        const i = index(m.on)
        if (i < 0) return null
        const x = i * step + step / 2
        return (
          <line
            key={`${m.title}-${m.kind}-${m.on}`}
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

function Weekly({ series }: { series: PublicSeries }) {
  const [range, setRange] = useState<Range>('6M')
  const weeks = RANGES.find(([key]) => key === range)?.[2] ?? null
  const all = series.timeline.points
  const points = weeks === null ? all : all.slice(-weeks)
  const start = points[0]?.period_start ?? ''
  const markers = series.timeline.markers.filter((m) => m.on >= start)
  return (
    <Section id="series-weekly" title="每週件數">
      {series.event_count < 2 && <Muted>目前只有 1 起。</Muted>}
      <div role="group" aria-label="時間範圍" className="flex flex-wrap gap-2">
        {RANGES.map(([key, text]) => (
          <Button key={key} size="sm" variant={range === key ? 'primary' : 'outline'} aria-pressed={range === key} onClick={() => setRange(key)}>
            {text}
          </Button>
        ))}
      </div>
      {points.length > 0 ? <Chart points={points} markers={markers} /> : <Muted>這段期間沒有任何一起。</Muted>}
      <p className="text-xs text-muted-foreground">長條是每週件數，線是 4 週移動平均；虛線是措施開始處理，實線是完成。</p>
      {series.bursts.length > 0 && (
        <ul aria-label="突增週" className="space-y-1 text-sm">
          {series.bursts.map((b) => (
            <li key={b.period_start} className="text-foreground">
              {`${b.period_start} 這週有 ${b.count} 起，比平常（約 ${b.expected.toFixed(1)} 起）多。`}
            </li>
          ))}
          <li className="text-xs text-muted-foreground">突增由程式依過去的件數判斷，不是 AI 的判斷。</li>
        </ul>
      )}
    </Section>
  )
}

function Findings({ items, note }: { items: PublicFinding[]; note?: string | null }) {
  if (items.length === 0) return <Muted>{note ?? '目前資料還不夠，先不下結論。'}</Muted>
  return (
    <ul className="space-y-3">
      {items.map((f) => (
        <li key={f.description} className="space-y-1">
          <p className="flex flex-wrap items-center gap-2">
            <span className="text-foreground">{f.description}</span>
            <FindingTag text={f.label} />
          </p>
          <p className="font-mono text-xs text-muted-foreground">{`${f.total} 起中有 ${f.event_count} 起`}</p>
        </li>
      ))}
    </ul>
  )
}

function Measures({ measures }: { measures: PublicMeasure[] }) {
  const groups = AUDIENCES.map(([key, title]) => [title, measures.filter((m) => m.audience === key)] as const).filter(
    ([, items]) => items.length > 0,
  )
  if (groups.length === 0) return <Muted>目前還沒有改善建議。</Muted>
  return (
    <div className="space-y-4">
      {groups.map(([title, items]) => (
        <div key={title} className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          <ul className="space-y-2">
            {items.map((m) => (
              <li key={m.action} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-foreground">{m.action}</span>
                <Badge>{`${label(HORIZON_LABELS, m.horizon)} · ${label(PRIORITY_LABELS, m.priority)}`}</Badge>
                {m.responsible_unit && <span className="text-xs text-muted-foreground">{m.responsible_unit}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

function Comparison({ c }: { c: PublicComparison }) {
  return (
    <li className="space-y-1 rounded-lg border border-border px-3 py-2 text-sm">
      <p className="font-medium text-foreground">{`${c.title}（${c.start} 開始處理）`}</p>
      {c.status === 'ok' && c.before_weekly_average !== null && c.after_weekly_average !== null ? (
        <p className="text-foreground">
          {`開始前 8 週平均每週 ${c.before_weekly_average.toFixed(1)} 起；開始後平均每週 ${c.after_weekly_average.toFixed(1)} 起。`}
        </p>
      ) : (
        <p className="text-foreground">{c.message}</p>
      )}
      <p className="text-xs text-muted-foreground">{`開始前 ${c.before_events} 起、開始後 ${c.after_events} 起（任一邊少於 5 起就不比較）`}</p>
      <p className="text-xs text-muted-foreground">{c.caveat}</p>
    </li>
  )
}

/**
 * 公開系列分析頁（P8-T06，ADR-0018 決策 1-9、ADR-0020 決策 2）：總覽、累計件數與最近一起、每週件數、
 * 發生地點、AI 看到的規律、可能原因（一律帶「推測，尚未查證」或「已確認」標籤）、改善建議、改善追蹤、
 * 改善前後比較、這個系列的每一起；`from` 是剛剛看的那則快報，會在列表中標出。
 */
export function Series({ id, from }: { id: string; from: string | null }) {
  const { data: series, error, loading } = useLoad(() => fetchSeries(id), id)
  const back = (
    <Link to="/" className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
      ← 回最新快報
    </Link>
  )

  if (error?.code === 'NOT_FOUND') {
    return (
      <div className="space-y-6">
        <PageIntro eyebrow="系列分析" title="找不到這個系列">
          <p>這個系列不存在，或可能已經下架。</p>
        </PageIntro>
        {back}
      </div>
    )
  }
  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="font-display text-3xl text-foreground">系列分析</h1>
        <ErrorText>{error.message}</ErrorText>
        {back}
      </div>
    )
  }
  if (loading || !series) return <Muted>載入中…</Muted>

  const single = series.event_count < 2
  const latest = series.events.at(-1)
  return (
    <article className="space-y-6">
      {back}
      <div className="flex items-center gap-3">
        <CategoryIcon type={series.event_type} />
        <Badge>{series.label}</Badge>
      </div>
      <PageIntro eyebrow={single ? '單一事件' : '系列分析'} title={series.name}>
        {series.summary && <p>{series.summary}</p>}
      </PageIntro>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-muted-foreground">累計件數</dt>
        <dd className="text-foreground">{`${series.event_count} 起`}</dd>
        <dt className="text-muted-foreground">最近一起</dt>
        <dd className="text-foreground">{latest ? `${latest.occurrence_date ?? '日期未知'}・${latest.title}` : '無'}</dd>
        <dt className="text-muted-foreground">最後更新</dt>
        <dd className="text-foreground">{formatTime(series.published_at)}</dd>
      </dl>
      <p className="text-sm text-muted-foreground">
        規律與可能原因是 AI 依公開資料整理的推測，標示「推測，尚未查證」的內容不代表事實；件數由程式計算。
      </p>

      <Weekly series={series} />

      {series.location_groups.length > 0 && (
        <Section id="series-locations" title="發生地點">
          <ul className="space-y-1 text-sm">
            {series.location_groups.map((g) => (
              <li key={g.label} className="text-foreground">{`${g.label}：${g.total} 起中有 ${g.event_count} 起`}</li>
            ))}
          </ul>
        </Section>
      )}

      <Section id="series-patterns" title="AI 看到的規律">
        <Findings items={series.patterns} note={series.pattern_note ?? '目前還沒找到有足夠證據的規律，持續觀察中。'} />
      </Section>

      <Section id="series-causes" title="可能原因">
        <Findings items={series.causes} note="資料還不夠推測原因，持續觀察中。" />
      </Section>

      <Section id="series-measures" title="改善建議">
        <Measures measures={series.measures} />
      </Section>

      <Section id="series-progress" title="改善追蹤">
        {series.progress.length > 0 ? <ActionList actions={series.progress} /> : <Muted>目前還沒有追蹤中的改善措施，有進度會更新在這裡。</Muted>}
      </Section>

      {series.comparisons.length > 0 && (
        <Section id="series-compare" title="改善前後比較">
          <ul className="space-y-2">
            {series.comparisons.map((c) => (
              <Comparison key={`${c.title}-${c.start}`} c={c} />
            ))}
          </ul>
        </Section>
      )}

      <Section id="series-events" title="這個系列的每一起">
        <ol className="space-y-2">
          {series.events.map((e) => {
            const current = e.id === from
            return (
              <li
                key={e.id}
                aria-current={current ? 'true' : undefined}
                className={cn('flex flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-sm', current ? 'border border-accent bg-accent/10' : 'border border-border')}
              >
                <span className="font-mono text-xs text-muted-foreground">{`第 ${e.ordinal} 起`}</span>
                <Link to={`/events/${encodeURIComponent(e.id)}`} className="text-foreground underline underline-offset-4 hover:text-accent">
                  {e.title}
                </Link>
                <VerificationTag verification={e.verification} text={e.verification_label} />
                <span className="text-xs text-muted-foreground">{[e.occurrence_date, e.location].filter(Boolean).join('・')}</span>
                {current && <Badge tone="info">你剛剛看的</Badge>}
              </li>
            )
          })}
        </ol>
      </Section>
    </article>
  )
}
