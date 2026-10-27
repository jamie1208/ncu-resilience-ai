import { useState, type FormEvent } from 'react'

import { Badge, Button, Card, Input, Select } from '@design'

import {
  listAlerts,
  listRecurrences,
  retractAlert,
  RETRACTABLE_STATES,
  type AlertItem,
  type AlertState,
  type Page,
  type RecurrenceGroup,
  type RecurrenceReason,
} from '../api'
import { ALERT_LABELS, ALERT_TONES, formatTime, label, STATUS_LABELS } from '../format'
import { Link } from '../Link'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, PageHeader, Section } from '../ui'

const STATES = Object.keys(ALERT_LABELS) as AlertState[]

const REASON_LABELS: Record<RecurrenceReason, string> = {
  within_window: '時間窗內再發生',
  after_improvement: '改善後再發生',
}

/** 個人通知中心：系統送給使用者本人的通知（ADR-0008），可撤回已通知的內容。 */
export function AlertCenter() {
  const [state, setState] = useState<AlertState | ''>('')
  const alerts = useLoad<Page<AlertItem>>(() => listAlerts({ state }), `alerts:${state}`)
  const items = alerts.data?.items ?? []

  function replace(updated: AlertItem) {
    alerts.setData((page) =>
      page ? { ...page, items: page.items.map((a) => (a.id === updated.id ? updated : a)) } : page,
    )
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="通知中心"
        description="只送給你本人的通知；通知內容由確定性規則產生，未核實的指控不會呈現為事實。"
        actions={
          <label className="flex items-center gap-2 text-sm font-medium text-foreground">
            <span className="shrink-0">狀態</span>
            <Select value={state} onChange={(e) => setState(e.target.value as AlertState | '')}>
              <option value="">全部</option>
              {STATES.map((s) => (
                <option key={s} value={s}>
                  {ALERT_LABELS[s]}
                </option>
              ))}
            </Select>
          </label>
        }
      />
      <Recurrences />
      <LoadState loading={alerts.loading} error={alerts.error} empty={items.length === 0} emptyText="目前沒有通知" />
      {items.length > 0 && (
        <ul className="space-y-3">
          {items.map((alert) => (
            <AlertRow key={alert.id} alert={alert} onChange={replace} />
          ))}
        </ul>
      )}
    </div>
  )
}

/** 再發生提醒：同地點、同類別的事件不只一次，整組列出供逐一檢查（P4-T10）。 */
function Recurrences() {
  const groups = useLoad<RecurrenceGroup[]>(listRecurrences, 'recurrences')
  if (groups.error) return <ErrorText>{`再發生提醒載入失敗：${groups.error}`}</ErrorText>
  const items = groups.data ?? []
  if (items.length === 0) return null
  return (
    <Section id="recurrences" title="再發生提醒">
      <div className="space-y-3">
        {items.map((group) => (
          <RecurrenceCard key={`${group.location_id}:${group.event_type}`} group={group} />
        ))}
      </div>
    </Section>
  )
}

function RecurrenceCard({ group }: { group: RecurrenceGroup }) {
  return (
    <Card as="article" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-foreground">
          {`${group.location_name ?? '地點未明'}・${group.event_type_label}：${group.events.length} 件`}
        </h3>
        {group.reasons.map((reason) => (
          <Badge key={reason} tone="warning">
            {label(REASON_LABELS, reason)}
          </Badge>
        ))}
      </div>
      <p className="text-sm text-foreground">{group.message}</p>
      <ul className="space-y-1">
        {group.events.map((event) => (
          <li key={event.id} className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">{event.occurred_on}</span>
            <Link
              to={`/admin/events/${encodeURIComponent(event.id)}`}
              className="text-sm text-accent underline underline-offset-4"
            >
              {event.title}
            </Link>
            <span>{label(STATUS_LABELS, event.status)}</span>
            {event.improved_on && <Badge tone="success">{`已改善（${event.improved_on}）`}</Badge>}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function AlertRow({ alert, onChange }: { alert: AlertItem; onChange: (a: AlertItem) => void }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const message = useErrorMessage()
  const canRetract = RETRACTABLE_STATES.includes(alert.state)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = await retractAlert(alert.id, reason)
      onChange({ ...alert, state: result.state, retract_reason: reason.trim() })
      setOpen(false)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card as="li" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge tone={ALERT_TONES[alert.state]}>{label(ALERT_LABELS, alert.state)}</Badge>
        <span className="font-mono">{`v${alert.version}`}</span>
        <span className="font-mono">{formatTime(alert.updated_at)}</span>
        <Link
          to={`/admin/events/${encodeURIComponent(alert.event_id)}`}
          className="text-sm text-accent underline underline-offset-4"
        >
          {alert.event_title}
        </Link>
        {alert.flags.includes('possible_allegation') && <Badge tone="warning">疑似指控，需人工判斷</Badge>}
      </div>
      <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground">{alert.body}</p>
      {alert.retract_reason && (
        <p className="text-xs text-muted-foreground">{`撤回原因：${alert.retract_reason}`}</p>
      )}
      {canRetract && !open && (
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          撤回
        </Button>
      )}
      {canRetract && open && (
        <form aria-label="撤回通知" className="space-y-2" onSubmit={submit}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>撤回原因</span>
            <Input
              value={reason}
              maxLength={200}
              placeholder="會寫入稽核紀錄，請勿填寫個資"
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {error && <ErrorText>{error}</ErrorText>}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={busy || reason.trim() === ''}>
              確認撤回
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              取消
            </Button>
          </div>
        </form>
      )}
    </Card>
  )
}
