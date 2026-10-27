import { useState, type FormEvent } from 'react'

import { Badge, Button, Card, Input, Select, Textarea } from '@design'

import {
  ACTION_TRANSITIONS,
  getAction,
  listActions,
  PROOF_REQUIRED,
  transitionAction,
  updateAction,
  type ActionState,
  type ActionTransition,
  type ResponseActionDetail,
  type ResponseActionItem,
} from '../api'
import { formatTime, label } from '../format'
import { Link } from '../Link'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, PageHeader } from '../ui'

const ACTION_LABELS: Record<ActionState, string> = {
  discovered: '已發現',
  handling: '處理中',
  recovered: '已恢復',
  improved: '已改進',
  dropped: '不追蹤',
}

const ACTION_TONES: Partial<Record<ActionState, 'info' | 'success' | 'warning'>> = {
  handling: 'info',
  recovered: 'success',
  improved: 'success',
}

const STATES = Object.keys(ACTION_LABELS) as ActionState[]

/** 期限只取台北日期（YYYY-MM-DD）。 */
function dueDate(iso: string | null): string {
  return iso ? formatTime(iso).slice(0, 10) : ''
}

/** 表單的日期視為台北當日結束。 */
function dueAt(date: string): string | null {
  return date ? `${date}T23:59:59+08:00` : null
}

/** 改善追蹤（F10）：發現 → 處理 → 恢復 → 改進，每次變更都留下歷史與佐證。 */
export function ResponseActions() {
  const [state, setState] = useState<ActionState | ''>('')
  const actions = useLoad<ResponseActionItem[]>(() => listActions({ state }), `actions:${state}`)
  const items = actions.data ?? []

  function replace(updated: ResponseActionDetail) {
    actions.setData((list) =>
      list ? list.map((a) => (a.id === updated.id ? { ...a, ...updated, event: a.event } : a)) : list,
    )
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="改善追蹤"
        description="從事件小報告的校方措施或手動新增；負責人只是你自己的紀錄，系統不會寄信給任何單位。"
        actions={
          <label className="flex items-center gap-2 text-sm font-medium text-foreground">
            <span className="shrink-0">狀態</span>
            <Select value={state} onChange={(e) => setState(e.target.value as ActionState | '')}>
              <option value="">全部</option>
              {STATES.map((s) => (
                <option key={s} value={s}>
                  {ACTION_LABELS[s]}
                </option>
              ))}
            </Select>
          </label>
        }
      />
      <LoadState
        loading={actions.loading}
        error={actions.error}
        empty={items.length === 0}
        emptyText="目前沒有追蹤中的改善措施；可在事件小報告的校方措施旁加入。"
      />
      {items.length > 0 && (
        <ul className="space-y-3">
          {items.map((action) => (
            <ActionRow key={action.id} action={action} onChange={replace} />
          ))}
        </ul>
      )}
    </div>
  )
}

type Panel = { kind: 'transition'; to: ActionState } | { kind: 'edit' } | null

function ActionRow({
  action,
  onChange,
}: {
  action: ResponseActionItem
  onChange: (a: ResponseActionDetail) => void
}) {
  const [panel, setPanel] = useState<Panel>(null)
  const [history, setHistory] = useState<ActionTransition[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const message = useErrorMessage()
  const next = ACTION_TRANSITIONS[action.state]

  function done(updated: ResponseActionDetail) {
    onChange(updated)
    if (history) setHistory(updated.history)
    setPanel(null)
  }

  async function toggleHistory() {
    if (history) {
      setHistory(null)
      return
    }
    setError(null)
    try {
      setHistory((await getAction(action.id)).history)
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <Card as="li" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge tone={ACTION_TONES[action.state]}>{label(ACTION_LABELS, action.state)}</Badge>
        {action.overdue && <Badge tone="danger">已逾期</Badge>}
        {action.origin === 'measure' && <span>來自事件小報告</span>}
        {action.measure?.cause_basis === 'hypothesized' && <Badge tone="warning">針對推測原因</Badge>}
        <Link
          to={`/admin/events/${encodeURIComponent(action.event.id)}`}
          className="text-sm text-accent underline underline-offset-4"
        >
          {action.event.title}
        </Link>
      </div>
      <p className="text-sm font-medium text-foreground">{action.title}</p>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>{`負責：${action.owner ?? '未指定'}`}</span>
        <span>{`期限：${dueDate(action.due_at) || '未設定'}`}</span>
        <span className="font-mono">{`更新 ${formatTime(action.updated_at)}`}</span>
      </div>
      {action.proof && (
        <p className="text-sm whitespace-pre-wrap text-foreground">{`佐證：${action.proof}`}</p>
      )}
      {error && <ErrorText>{error}</ErrorText>}

      {panel === null && (
        <div className="flex flex-wrap gap-2">
          {next.map((to) => (
            <Button key={to} variant="outline" size="sm" onClick={() => setPanel({ kind: 'transition', to })}>
              {`改為${ACTION_LABELS[to]}`}
            </Button>
          ))}
          <Button variant="ghost" size="sm" onClick={() => setPanel({ kind: 'edit' })}>
            修改負責人與期限
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void toggleHistory()}>
            {history ? '收起歷史' : '查看歷史'}
          </Button>
        </div>
      )}
      {panel?.kind === 'transition' && (
        <TransitionForm action={action} to={panel.to} onDone={done} onCancel={() => setPanel(null)} />
      )}
      {panel?.kind === 'edit' && <EditForm action={action} onDone={done} onCancel={() => setPanel(null)} />}
      {history && <History rows={history} />}
    </Card>
  )
}

function TransitionForm({
  action,
  to,
  onDone,
  onCancel,
}: {
  action: ResponseActionItem
  to: ActionState
  onDone: (a: ResponseActionDetail) => void
  onCancel: () => void
}) {
  const [note, setNote] = useState('')
  const [proof, setProof] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const message = useErrorMessage()
  const needsProof = PROOF_REQUIRED.includes(to)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onDone(await transitionAction(action.id, to, { note, proof }))
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form aria-label="變更狀態" className="space-y-2" onSubmit={submit}>
      <p className="text-sm text-foreground">{`改為「${ACTION_LABELS[to]}」`}</p>
      {needsProof && (
        <label className="block space-y-1 text-sm text-foreground">
          <span>佐證</span>
          <Textarea
            value={proof}
            maxLength={2000}
            rows={2}
            placeholder="例如公告內容或現場確認的說明，請勿填寫個資"
            onChange={(e) => setProof(e.target.value)}
          />
        </label>
      )}
      <label className="block space-y-1 text-sm text-foreground">
        <span>備註</span>
        <Input value={note} maxLength={200} placeholder="選填，請勿填寫個資" onChange={(e) => setNote(e.target.value)} />
      </label>
      {error && <ErrorText>{error}</ErrorText>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || (needsProof && proof.trim() === '')}>
          確認
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          取消
        </Button>
      </div>
    </form>
  )
}

function EditForm({
  action,
  onDone,
  onCancel,
}: {
  action: ResponseActionItem
  onDone: (a: ResponseActionDetail) => void
  onCancel: () => void
}) {
  const [owner, setOwner] = useState(action.owner ?? '')
  const [date, setDate] = useState(dueDate(action.due_at))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const message = useErrorMessage()

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onDone(await updateAction(action.id, { owner: owner.trim() || null, due_at: dueAt(date) }))
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form aria-label="修改負責人與期限" className="grid gap-2 sm:grid-cols-2" onSubmit={submit}>
      <label className="block space-y-1 text-sm text-foreground">
        <span>負責人</span>
        <Input value={owner} maxLength={100} placeholder="例如總務處" onChange={(e) => setOwner(e.target.value)} />
      </label>
      <label className="block space-y-1 text-sm text-foreground">
        <span>期限</span>
        <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      {error && (
        <div className="sm:col-span-2">
          <ErrorText>{error}</ErrorText>
        </div>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" size="sm" disabled={busy}>
          儲存
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          取消
        </Button>
      </div>
    </form>
  )
}

function History({ rows }: { rows: ActionTransition[] }) {
  if (rows.length === 0) return <Muted>沒有狀態變更紀錄</Muted>
  return (
    <ol aria-label="狀態變更歷史" className="space-y-1 border-l-2 border-border pl-3 text-xs text-muted-foreground">
      {rows.map((r) => (
        <li key={`${r.created_at}-${r.to_state}`}>
          <span className="font-mono">{formatTime(r.created_at)}</span>
          <span className="text-foreground">
            {` ${r.from_state ? `${ACTION_LABELS[r.from_state]} → ` : ''}${ACTION_LABELS[r.to_state]}`}
          </span>
          {r.note && <span>{` · ${r.note}`}</span>}
          {r.proof && <span>{` · 佐證：${r.proof}`}</span>}
        </li>
      ))}
    </ol>
  )
}
