import type { ReactNode } from 'react'

import { Badge, Card } from '@design'

import { getDashboard, getSourceHealth, listAuditLogs, listIngestionRuns } from '../api'
import {
  ALERT_LABELS,
  formatPercent,
  formatTime,
  HEALTH_LABELS,
  HEALTH_TONES,
  label,
  RISK_LABELS,
  riskTone,
  STATUS_LABELS,
} from '../format'
import { Link } from '../Link'
import { useLoad } from '../useLoad'
import { LoadState, PageHeader, Section } from '../ui'

/** 運維：儀表板摘要、來源健康、最近擷取與稽核紀錄；各區塊獨立載入，一塊失敗不影響其他。 */
export function Ops() {
  const dashboard = useLoad(getDashboard, 'dashboard')
  const health = useLoad(getSourceHealth, 'health')
  const runs = useLoad(() => listIngestionRuns(20), 'runs')
  const audit = useLoad(() => listAuditLogs(20), 'audit')
  const d = dashboard.data

  return (
    <div className="space-y-6">
      <PageHeader title="運維" description="來源健康、擷取紀錄與稽核；統計窗口為近 7 天。" />

      <Card>
        <Section id="ops-summary" title="摘要">
          <LoadState loading={dashboard.loading} error={dashboard.error} />
          {d && (
            <div className="grid gap-4 md:grid-cols-3">
              <dl className="space-y-1 text-sm">
                <dt className="text-xs text-muted-foreground">近 7 天新事件</dt>
                <dd className="font-mono text-2xl text-foreground">{d.events.created_last_7d}</dd>
              </dl>
              <Counts title="事件狀態" counts={d.events.by_status} labels={STATUS_LABELS} />
              <Counts title="通知狀態" counts={d.alerts.by_state} labels={ALERT_LABELS} />
              <div className="md:col-span-3">
                <h3 className="text-xs text-muted-foreground">未結案的高風險事件</h3>
                {d.events.high_risk_open.length === 0 ? (
                  <p className="text-sm text-muted-foreground">無</p>
                ) : (
                  <ul className="mt-1 space-y-1">
                    {d.events.high_risk_open.map((e) => (
                      <li key={e.id} className="flex items-center gap-2 text-sm">
                        <Badge tone={riskTone(e.level)}>{label(RISK_LABELS, e.level)}</Badge>
                        <Link
                          to={`/admin/events/${encodeURIComponent(e.id)}`}
                          className="text-accent underline underline-offset-4"
                        >
                          {e.title}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </Section>
      </Card>

      <Card>
        <Section id="ops-health" title="來源健康">
          <LoadState loading={health.loading} error={health.error} empty={health.data?.length === 0} />
          {health.data && health.data.length > 0 && (
            <Table head={['來源', '狀態', '最後成功', '連續失敗', '成功率', '死信']}>
              {health.data.map((s) => (
                <tr key={s.code}>
                  <Td>
                    <span className="block text-foreground">{s.name}</span>
                    <span className="font-mono text-xs text-muted-foreground">{s.code}</span>
                  </Td>
                  <Td>
                    <Badge tone={HEALTH_TONES[s.status]}>{label(HEALTH_LABELS, s.status)}</Badge>
                  </Td>
                  <Td mono>{formatTime(s.last_success_at)}</Td>
                  <Td mono>{String(s.consecutive_failures)}</Td>
                  <Td mono>{formatPercent(s.success_rate)}</Td>
                  <Td mono>{String(s.open_dead_letters)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Section>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="min-w-0">
          <Section id="ops-runs" title="最近擷取">
            <LoadState loading={runs.loading} error={runs.error} empty={runs.data?.items.length === 0} />
            {runs.data && runs.data.items.length > 0 && (
              <Table head={['來源', '狀態', '開始', '筆數']}>
                {runs.data.items.map((r) => (
                  <tr key={r.id}>
                    <Td mono>{r.source_code}</Td>
                    <Td>{r.status}</Td>
                    <Td mono>{formatTime(r.started_at)}</Td>
                    <Td mono>{JSON.stringify(r.counts)}</Td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>
        </Card>
        <Card className="min-w-0">
          <Section id="ops-audit" title="稽核紀錄">
            <LoadState loading={audit.loading} error={audit.error} empty={audit.data?.items.length === 0} />
            {audit.data && audit.data.items.length > 0 && (
              <Table head={['時間', '操作', '執行者', '對象']}>
                {audit.data.items.map((log) => (
                  <tr key={log.id}>
                    <Td mono>{formatTime(log.created_at)}</Td>
                    <Td mono>{log.action}</Td>
                    <Td mono>{log.actor}</Td>
                    <Td mono>{log.target ?? '—'}</Td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>
        </Card>
      </div>
    </div>
  )
}

function Counts({ title, counts, labels }: {
  title: string
  counts: Record<string, number>
  labels: Record<string, string>
}) {
  const entries = Object.entries(counts)
  return (
    <div>
      <h3 className="text-xs text-muted-foreground">{title}</h3>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">無</p>
      ) : (
        <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {entries.map(([key, n]) => (
            <div key={key} className="flex gap-1">
              <dt className="text-muted-foreground">{label(labels, key)}</dt>
              <dd className="font-mono text-foreground">{n}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

/** 小螢幕可水平捲動的表格（只捲表格本身，頁面不會水平捲動）。 */
function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full min-w-[32rem] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-muted-foreground">
            {head.map((h) => (
              <th key={h} scope="col" className="px-1 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">{children}</tbody>
      </table>
    </div>
  )
}

function Td({ children, mono = false }: { children: ReactNode; mono?: boolean }) {
  return <td className={mono ? 'px-1 py-2 font-mono text-xs text-foreground' : 'px-1 py-2'}>{children}</td>
}
