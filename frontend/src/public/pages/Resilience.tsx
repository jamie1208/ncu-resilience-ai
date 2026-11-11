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
    <div className="space-y-6">
      <PageIntro eyebrow="校園韌性成績單" title="學校遇到狀況時，處理得好不好？">
        <p className="text-[17px]">
          「韌性」就是遇到問題後能不能很快站起來、而且下次做得更好。我們用四個問題替學校打分數：多快有人知道、
          多快開始處理、多久恢復正常、同樣的事有沒有一再發生，每題 0 到 100 分，資料不夠時直接顯示 N/A。
        </p>
        <p className="text-[15px] text-muted-foreground">換算分數時，幾天算好是本專題自己訂的目標，不是學校的官方標準。</p>
      </PageIntro>
      <aside
        role="note"
        aria-label="閱讀前請注意"
        className="rounded-[14px] border border-warning/30 bg-warning-muted px-4 py-3.5 text-[15px] leading-relaxed text-foreground"
      >
        <p>
          <strong>回報變多，不代表學校變危險。</strong>
          件數反映的是被張貼、被回報、被系統收到的事件；宣導變多或來源增加，件數也會跟著變多。
        </p>
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
      <section
        aria-labelledby="cri-overall"
        className="flex flex-wrap items-center gap-6 rounded-[20px] bg-foreground p-7 text-background"
      >
        <div className="flex flex-none flex-col gap-1">
          <h2 id="cri-overall" className="text-sm font-normal text-background/75">
            總分
          </h2>
          <p className="text-[64px] leading-none font-bold">
            <span>{formatScore(report.score)}</span>
            {report.score !== null && <span className="text-xl font-normal text-background/75"> / 100</span>}
          </p>
        </div>
        <div className="flex min-w-0 flex-[1_1_280px] flex-col gap-2">
          {report.score === null && (
            <p>
              <Badge tone="warning">資料不足，暫不計算總分</Badge>
            </p>
          )}
          <p className="text-[15px] leading-relaxed text-background/75">
            {`統計期間 ${report.window.start} 至 ${report.window.end}（${report.window.days} 天），共 ${report.event_count} 件事件。四個問題都有足夠資料時才計算總分。`}
          </p>
        </div>
      </section>

      <ul aria-label="四個問題" className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4">
        {report.dimensions.map((d) => (
          <li key={d.key} className="min-w-0">
            <DimensionCard dimension={d} minSamples={report.min_samples} />
          </li>
        ))}
      </ul>

      <Card as="section" aria-labelledby="cri-limits" className="space-y-2 rounded-2xl p-5">
        <h2 id="cri-limits" className="text-lg font-bold text-foreground">
          資料限制
        </h2>
        <ul className="list-disc space-y-1 pl-5 text-[15px] leading-relaxed text-foreground/80">
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
    <Card as="article" aria-labelledby={titleId} className="flex h-full flex-col gap-2.5 rounded-2xl p-5">
      <p className="text-[13px] text-muted-foreground">{d.label}</p>
      <h3 id={titleId} className="text-[19px] leading-snug font-bold text-foreground">
        {DIMENSION_QUESTIONS[d.key]}
      </h3>
      <span className="text-4xl font-bold text-foreground">{formatScore(d.score)}</span>
      {d.score !== null && (
        <span aria-hidden="true" className="block h-2 overflow-hidden rounded bg-muted">
          <span className="block h-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, d.score))}%` }} />
        </span>
      )}
      <p className="mt-auto border-t border-border pt-2.5 text-[13px] leading-relaxed text-muted-foreground">
        {`樣本 ${d.sample_size} 筆・覆蓋率 ${formatCoverage(d.coverage)}`}
        {d.score === null && `・樣本少於 ${minSamples} 筆，暫不計分`}
      </p>
    </Card>
  )
}
