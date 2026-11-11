import { useState } from 'react'

import { Badge, Button, Card } from '@design'

import { listAnonymousReports, type InboxGroup, type InboxReport, type Page } from '../api'
import { formatTime, label, STATUS_LABELS, STATUS_TONES } from '../format'
import { Link } from '../Link'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, PageHeader } from '../ui'

const ANALYSIS_LABELS: Record<string, string> = {
  pending: '等待分析',
  queued: '排隊分析中',
  done: '分析完成',
  skipped: '超過時間窗，不分析',
  failed: '分析失敗',
}

const ANALYSIS_TONES: Record<string, 'success' | 'warning' | 'danger' | 'info'> = {
  queued: 'info',
  done: 'success',
  failed: 'danger',
}

/** 匿名回報收件匣（P8-T16）：原文只在後台；補件依收據排在原投稿之下。 */
export function AnonymousReports() {
  const first = useLoad(() => listAnonymousReports(), 'anonymous-reports')
  const [more, setMore] = useState<Page<InboxGroup>[]>([])
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState<string | null>(null)
  const message = useErrorMessage()

  const pages = first.data ? [first.data, ...more] : []
  const groups = pages.flatMap((p) => p.items)
  const nextCursor = pages.at(-1)?.nextCursor ?? null

  async function loadMore() {
    if (!nextCursor) return
    setLoadingMore(true)
    setMoreError(null)
    try {
      const page = await listAnonymousReports(nextCursor)
      setMore((current) => [...current, page])
    } catch (e) {
      setMoreError(message(e))
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="匿名回報"
        description="公開網站收到的匿名回報與補件。原文只在後台看得到，公開端只會出現經過遮蔽的 AI 摘要；這裡不顯示收據碼。"
      />
      <LoadState
        loading={first.loading && !first.data}
        error={first.error}
        empty={groups.length === 0}
        emptyText="還沒有收到匿名回報。"
      />
      {groups.length > 0 && (
        <div className="space-y-3">
          {groups.map((group) => (
            <GroupCard key={group.id} group={group} />
          ))}
        </div>
      )}
      {moreError && <ErrorText>{moreError}</ErrorText>}
      {nextCursor && !first.loading && (
        <Button variant="outline" size="sm" className="w-full" onClick={loadMore} disabled={loadingMore}>
          {loadingMore ? '載入中…' : '載入更多'}
        </Button>
      )}
    </div>
  )
}

function GroupCard({ group }: { group: InboxGroup }) {
  const supplements = group.reports.filter((r) => r.kind === 'supplement').length
  return (
    <Card data-group={group.id} className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-muted-foreground">
        <span>{supplements > 0 ? `${supplements} 則補件` : '沒有補件'}</span>
        <span>{`最近收到 ${formatTime(group.latest_submitted_at)}`}</span>
      </div>
      <ol aria-label="同一張收據的投稿" className="space-y-2">
        {group.reports.map((report) => (
          <ReportRow key={report.id} report={report} />
        ))}
      </ol>
      <div className="space-y-1">
        <p className="text-xs font-medium text-muted-foreground">所屬事件</p>
        {group.events.length === 0 ? (
          <p className="text-sm text-muted-foreground">還沒有歸入任何事件</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {group.events.map((event) => (
              <li key={event.id}>
                <Link
                  to={`/admin/events/${encodeURIComponent(event.id)}`}
                  className="inline-flex items-center gap-2 text-sm text-primary underline-offset-2 hover:underline"
                >
                  <span>{event.title}</span>
                  <Badge tone={STATUS_TONES[event.status]}>{label(STATUS_LABELS, event.status)}</Badge>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

function ReportRow({ report }: { report: InboxReport }) {
  const supplement = report.kind === 'supplement'
  return (
    <li
      className={supplement ? 'ml-4 space-y-1 border-l-2 border-border pl-3' : 'space-y-1'}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone={supplement ? 'info' : undefined}>{supplement ? '補件' : '原投稿'}</Badge>
        <span className="font-mono text-muted-foreground">{formatTime(report.submitted_at)}</span>
        {report.ingestion_status === 'not_ingested' ? (
          <Badge tone="warning">尚未擷取</Badge>
        ) : (
          <Badge tone={ANALYSIS_TONES[report.analysis_status ?? '']}>
            {label(ANALYSIS_LABELS, report.analysis_status ?? '')}
          </Badge>
        )}
      </div>
      <p className="whitespace-pre-wrap break-words text-sm text-foreground">{report.content}</p>
      {report.location && <p className="text-xs text-muted-foreground">{`地點：${report.location}`}</p>}
    </li>
  )
}
