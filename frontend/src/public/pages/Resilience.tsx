import { Badge, Card } from '@design'

import {
  DIMENSION_QUESTIONS,
  formatCoverage,
  formatScore,
  type Resilience as Report,
  type ResilienceDimension,
} from '../../lib/resilience'
import { fetchResilience } from '../api'
import { ErrorText, Muted, PageIntro } from '../ui'
import { useLoad } from '../useLoad'

/**
 * 公開版校園韌性成績單（P6-T05，ADR-0015）：只統計已查證並公開的事件，只有彙總數字。
 * 醒目標明「回報變多不等於校園變危險」；樣本不足的構面與總分顯示 N/A，不補任何數字。
 */
export function Resilience() {
  const report = useLoad(fetchResilience, 'resilience')
  return (
    <div className="space-y-8">
      <PageIntro eyebrow="把點連成線" title="校園韌性成績單">
        <p>看校園遇到問題時，從發現、回應、恢復到改善做得如何。分數 0–100，資料不夠時直接顯示 N/A。</p>
      </PageIntro>
      <aside
        role="note"
        aria-label="閱讀前請注意"
        className="rounded-lg border border-warning/30 bg-warning-muted px-4 py-3 text-sm leading-relaxed text-foreground"
      >
        <p className="font-semibold">回報變多不等於校園變危險。</p>
        <p>件數反映的是被張貼、被回報、被系統收到的事件；宣導變多或來源增加，件數也會跟著變多。</p>
      </aside>
      {report.error && <ErrorText>{report.error.message}</ErrorText>}
      {report.loading && <Muted>載入中…</Muted>}
      {report.data && <ReportBody report={report.data} />}
    </div>
  )
}

function ReportBody({ report }: { report: Report }) {
  return (
    <>
      <Card as="section" aria-labelledby="cri-overall" className="space-y-3">
        <h2 id="cri-overall" className="text-lg font-semibold text-foreground">
          總分
        </h2>
        <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
          <p className="font-display text-5xl text-foreground">{formatScore(report.score)}</p>
          {report.score === null && <Badge tone="warning">資料不足，暫不計算總分</Badge>}
        </div>
        <p className="text-sm text-muted-foreground">
          {`統計期間 ${report.window.start} 至 ${report.window.end}（${report.window.days} 天），共 ${report.event_count} 件事件。四個構面都有足夠資料時才計算總分。`}
        </p>
      </Card>

      <ul aria-label="四個構面" className="grid gap-4 sm:grid-cols-2">
        {report.dimensions.map((d) => (
          <li key={d.key} className="min-w-0">
            <DimensionCard dimension={d} minSamples={report.min_samples} />
          </li>
        ))}
      </ul>

      <Card as="section" aria-labelledby="cri-limits" className="space-y-3">
        <h2 id="cri-limits" className="text-lg font-semibold text-foreground">
          資料限制
        </h2>
        <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-foreground">
          {report.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </Card>
    </>
  )
}

function DimensionCard({ dimension: d, minSamples }: { dimension: ResilienceDimension; minSamples: number }) {
  const titleId = `cri-${d.key}`
  return (
    <Card as="article" aria-labelledby={titleId} className="h-full space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <h3 id={titleId} className="text-base font-semibold text-foreground">
          {d.label}
        </h3>
        <span className="font-display text-3xl text-foreground">{formatScore(d.score)}</span>
      </div>
      <p className="text-sm text-foreground">{DIMENSION_QUESTIONS[d.key]}</p>
      <p className="text-xs text-muted-foreground">
        {`樣本 ${d.sample_size} 筆・覆蓋率 ${formatCoverage(d.coverage)}`}
        {d.score === null && `・樣本少於 ${minSamples} 筆，暫不計分`}
      </p>
    </Card>
  )
}
