import { useId, useState } from 'react'

import { Badge, Button, Card, Select } from '@design'

import type { BadgeTone } from '../../design/contract'

import {
  DIMENSION_QUESTIONS,
  formatCoverage,
  formatMetric,
  formatScore,
  METRIC_LABELS,
  type Resilience as Report,
  type ResilienceDimension,
} from '../../lib/resilience'
import {
  getResilience,
  getTrendForecast,
  runTrendForecast,
  type CoverageLevel,
  type TrendCaveat,
  type TrendDriver,
  type TrendForecast,
  type TrendOutcome,
  type TrendSignal,
} from '../api'
import { formatPercent, formatTime, label } from '../format'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, PageHeader, Section } from '../ui'

const WINDOWS = [30, 90, 180, 365] as const

/**
 * 韌性成績單（P6-T05，規格 §15）：CRI 與四構面、樣本量、覆蓋率、時間窗與來源限制。
 * 統計全部事件（公開版只統計公開中的事件）；樣本不足的構面與總分顯示 N/A。
 */
export function Resilience() {
  const [windowDays, setWindowDays] = useState<number>(180)
  const report = useLoad(() => getResilience(windowDays), `resilience-${windowDays}`)
  const r = report.data

  return (
    <div className="space-y-6">
      <PageHeader
        title="韌性成績單"
        description="CRI = 0.25 發現 + 0.25 回應 + 0.25 恢復 + 0.25 調適；樣本不足的構面不計分，也不重新加權。"
        actions={
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            時間窗
            <Select value={String(windowDays)} onChange={(e) => setWindowDays(Number(e.target.value))}>
              {WINDOWS.map((d) => (
                <option key={d} value={d}>
                  {`近 ${d} 天`}
                </option>
              ))}
            </Select>
          </label>
        }
      />
      <LoadState loading={report.loading && !r} error={report.error} />
      {r && <ReportBody report={r} />}
      <TrendForecastCard />
    </div>
  )
}

function ReportBody({ report }: { report: Report }) {
  return (
    <>
      <Card>
        <Section id="cri-overall" title="總分">
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <p className="font-mono text-4xl text-foreground">{formatScore(report.score)}</p>
            {report.score === null && <Badge tone="warning">資料不足，不計算總分</Badge>}
          </div>
          <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            <Fact term="時間窗" value={`${report.window.start} 至 ${report.window.end}（${report.window.days} 天）`} />
            <Fact term="樣本量" value={`事件 ${report.event_count} 件、改善措施 ${report.action_count} 項`} />
            <Fact term="計分門檻" value={`每項指標至少 ${report.min_samples} 筆樣本`} />
            <Fact term="權重" value={`各構面權重 ${report.weights.join('、')}`} />
          </dl>
        </Section>
      </Card>

      <ul aria-label="四個構面" className="grid gap-4 md:grid-cols-2">
        {report.dimensions.map((d) => (
          <li key={d.key} className="min-w-0">
            <DimensionCard dimension={d} minSamples={report.min_samples} />
          </li>
        ))}
      </ul>

      <Card>
        <Section id="cri-limits" title="資料限制">
          <ul className="list-disc space-y-1 pl-5 text-sm text-foreground">
            {report.limitations.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </Section>
      </Card>
    </>
  )
}

function DimensionCard({ dimension: d, minSamples }: { dimension: ResilienceDimension; minSamples: number }) {
  const titleId = `cri-${d.key}`
  return (
    <Card as="article" aria-labelledby={titleId} className="h-full space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id={titleId} className="text-base font-semibold text-foreground">
          {d.label}
        </h2>
        <span className="font-mono text-2xl text-foreground">{formatScore(d.score)}</span>
      </div>
      <p className="text-sm text-muted-foreground">{DIMENSION_QUESTIONS[d.key]}</p>
      {d.reason === 'insufficient_samples' && (
        <p className="text-sm text-warning">{`樣本不足（少於 ${minSamples} 筆），不計分`}</p>
      )}
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-muted-foreground">
            <th scope="col" className="py-1 font-medium">指標</th>
            <th scope="col" className="py-1 font-medium">數值</th>
            <th scope="col" className="py-1 font-medium">分數</th>
            <th scope="col" className="py-1 font-medium">樣本／應列入</th>
            <th scope="col" className="py-1 font-medium">覆蓋率</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {d.metrics.map((m) => (
            <tr key={m.key}>
              <td className="py-1">{METRIC_LABELS[m.key] ?? m.key}</td>
              <td className="py-1 font-mono text-xs">{formatMetric(m)}</td>
              <td className="py-1 font-mono text-xs">{formatScore(m.score)}</td>
              <td className="py-1 font-mono text-xs">{`${m.sample_size}／${m.eligible}`}</td>
              <td className="py-1 font-mono text-xs">{formatCoverage(m.coverage)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

function Fact({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="text-foreground">{value}</dd>
    </div>
  )
}

// ---------- 趨勢解讀（P7-T06，S09 trend-forecaster） ----------

const EVENT_TYPE_LABELS: Record<string, string> = {
  facility: '設施',
  weather: '天氣',
  security: '治安',
  traffic: '交通',
  health: '健康',
  fire: '火災',
  academic: '學務',
  infosec: '資安',
}

const COVERAGE_LABELS: Record<CoverageLevel, string> = { full: '完整', partial: '部分缺漏', low: '大部分缺資料' }
const COVERAGE_TONES: Record<CoverageLevel, BadgeTone> = { full: 'success', partial: 'warning', low: 'danger' }

const DRIVER_LABELS: Record<TrendDriver, string> = {
  incident_cluster: '多起不同事件',
  repeated_reports: '同一件事被拆成多個事件',
  coverage_change: '來源組成或擷取缺漏改變',
  seasonal: '季節或學期時程',
  unexplained: '證據看不出原因',
}

const CAVEAT_LABELS: Record<TrendCaveat, string> = {
  volume_not_event_count: '聲量≠事件數',
  limited_coverage: '資料覆蓋不完整',
  short_history: '序列少於 8 期',
  small_counts: '異常週期件數少於 10',
}

const OUTCOME_TEXT: Partial<Record<TrendOutcome, string>> = {
  no_anomaly: '沒有偵測到突增，未呼叫模型。',
  insufficient_data: '資料覆蓋率過低，不做解讀，也未呼叫模型。',
  abstained: 'S09 判斷證據不足，未提出解釋。',
  failed: 'S09 解讀失敗；下方仍列出程式計算的數字。',
}

const FREQ_LABELS = { week: '每週', month: '每月' } as const

function signalName(s: Pick<TrendSignal, 'event_type' | 'location'>): string {
  return `${label(EVENT_TYPE_LABELS, s.event_type)}・${s.location ?? '全校'}`
}

/**
 * 趨勢解讀：數字（件數、突增、覆蓋率）由程式計算，S09 只解釋被標為異常的訊號。
 * 只手動觸發；一律標註聲量≠事件數與資料覆蓋率。只在管理端。
 */
function TrendForecastCard() {
  const latest = useLoad(getTrendForecast, 'trend-forecast')
  const message = useErrorMessage()
  const [freq, setFreq] = useState<'week' | 'month'>('week')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setRunning(true)
    setError(null)
    try {
      const result = await runTrendForecast(freq)
      latest.setData(() => result)
    } catch (e) {
      setError(message(e))
    } finally {
      setRunning(false)
    }
  }

  const f = latest.data
  return (
    <Card>
      <Section id="trend-forecast" title="趨勢解讀">
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              解讀週期
              <Select value={freq} onChange={(e) => setFreq(e.target.value as 'week' | 'month')}>
                <option value="week">每週</option>
                <option value="month">每月</option>
              </Select>
            </label>
            <Button type="button" disabled={running} onClick={run}>
              {running ? '解讀中…' : '執行趨勢解讀'}
            </Button>
            <Muted>截止日為昨日；只在手動執行時更新。</Muted>
          </div>
          {error && <ErrorText>{error}</ErrorText>}
          <LoadState loading={latest.loading && !f} error={latest.error} />
          {!latest.loading && !latest.error && !f && <Muted>尚未執行趨勢解讀。</Muted>}
          {f && <TrendForecastBody forecast={f} />}
        </div>
      </Section>
    </Card>
  )
}

function LimitsNote({ forecast: f }: { forecast: TrendForecast }) {
  const id = useId()
  const cov = f.coverage
  return (
    <div role="note" aria-labelledby={id} className="space-y-1 rounded-lg border border-warning/40 bg-warning-muted px-4 py-3 text-sm">
      <p id={id} className="font-semibold text-warning">
        解讀限制
      </p>
      <p className="text-foreground">聲量≠事件數：件數是被回報、被彙整成事件的數量，不等於實際發生次數。</p>
      <p className="flex flex-wrap items-center gap-2 text-foreground">
        <span>{`資料覆蓋率：${COVERAGE_LABELS[cov.level]}（${formatPercent(cov.ratio)}）`}</span>
        <Badge tone={COVERAGE_TONES[cov.level]}>{cov.level}</Badge>
        <span className="text-muted-foreground">{`${cov.start} 至 ${cov.end}`}</span>
      </p>
      {cov.note && <p className="text-foreground">{cov.note}</p>}
    </div>
  )
}

function TrendForecastBody({ forecast: f }: { forecast: TrendForecast }) {
  const output = f.forecast?.output ?? null
  const titles = Object.fromEntries(f.evidence.map((e) => [e.evidence_id, e.title]))
  const bySignal = Object.fromEntries(f.series.map((s) => [s.signal_id, s]))
  const outcomeText = OUTCOME_TEXT[f.outcome]

  return (
    <div className="space-y-4">
      <LimitsNote forecast={f} />
      <p className="text-sm text-muted-foreground">
        {`截至 ${f.as_of}（${f.start} 起，${FREQ_LABELS[f.freq]}）・${formatTime(f.created_at)} 執行`}
        {f.forecast && `・S09 ${f.forecast.skill_version}`}
      </p>
      {outcomeText && <p className="text-sm text-foreground">{outcomeText}</p>}

      {output && output.explanations.length > 0 && (
        <ul aria-label="異常解釋" className="space-y-3">
          {output.explanations.map((x) => {
            const s = bySignal[x.signal_id]
            return (
              <li key={x.signal_id} className="space-y-1 rounded-lg border border-border px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-foreground">{s ? signalName(s) : x.signal_id}</span>
                  <Badge tone="info">{DRIVER_LABELS[x.driver]}</Badge>
                  {x.basis === 'hypothesized' ? (
                    <Badge tone="warning">推測，尚待查證</Badge>
                  ) : (
                    <Badge tone="neutral">有事件佐證</Badge>
                  )}
                </div>
                <p className="text-foreground">{x.summary}</p>
                {x.evidence_ids.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {`依據：${x.evidence_ids.map((id) => titles[id] ?? id).join('、')}`}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {output && output.outlook.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-semibold text-foreground">值得留意（質性，不是件數預測）</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm text-foreground">
            {output.outlook.map((o) => (
              <li key={o.statement}>{o.statement}</li>
            ))}
          </ul>
        </div>
      )}

      {output && (
        <ul aria-label="限制標註" className="flex flex-wrap gap-2">
          {output.caveats.map((c) => (
            <li key={c}>
              <Badge tone="warning">{CAVEAT_LABELS[c]}</Badge>
            </li>
          ))}
        </ul>
      )}

      <table aria-label="件數序列" className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-muted-foreground">
            <th scope="col" className="py-1 font-medium">訊號</th>
            <th scope="col" className="py-1 font-medium">最近各期件數</th>
            <th scope="col" className="py-1 font-medium">突增（程式判定）</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {f.series.map((s) => (
            <tr key={s.signal_id}>
              <td className="py-1">{signalName(s)}</td>
              <td className="py-1 font-mono text-xs">
                {s.counts
                  .slice(-8)
                  .map((c) => c.count)
                  .join('・')}
              </td>
              <td className="py-1 font-mono text-xs">
                {s.anomaly
                  ? `${s.anomaly.observed}（期望 ${s.anomaly.expected.toFixed(1)}，p = ${Number(s.anomaly.p_value.toPrecision(2))}）`
                  : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
