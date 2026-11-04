import { useState } from 'react'

import { Badge, Card, Select } from '@design'

import {
  DIMENSION_QUESTIONS,
  formatCoverage,
  formatMetric,
  formatScore,
  METRIC_LABELS,
  type Resilience as Report,
  type ResilienceDimension,
} from '../../lib/resilience'
import { getResilience } from '../api'
import { useLoad } from '../useLoad'
import { LoadState, PageHeader, Section } from '../ui'

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
