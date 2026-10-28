import { useState, type FormEvent } from 'react'

import { Badge, Button, Card, Input, Select } from '@design'

import {
  listPublications,
  restorePublication,
  takeDownPublication,
  type PublicationItem,
  type PublicationState,
} from '../api'
import {
  formatTime,
  label,
  PUBLICATION_LABELS,
  PUBLICATION_LEVEL_LABELS,
  PUBLICATION_TONES,
} from '../format'
import { Link } from '../Link'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, Notice, PageHeader } from '../ui'

const STATES = Object.keys(PUBLICATION_LABELS) as PublicationState[]

/** 公開內容列表（P5-T14）：公開網站目前與曾經公開的內容，可一鍵下架或交回公開規則重新判斷。 */
export function Publications() {
  const [state, setState] = useState<PublicationState | ''>('')
  const list = useLoad(() => listPublications({ state }), `publications:${state}`)
  const items = list.data ?? []

  function replace(updated: PublicationItem) {
    list.setData((rows) => rows?.map((p) => (p.id === updated.id ? updated : p)) ?? rows)
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="公開內容"
        description="公開與否由公開規則決定；你可以隨時下架，下架後公開網站立即不再顯示。"
        actions={
          <label className="flex items-center gap-2 text-sm font-medium text-foreground">
            <span className="shrink-0">狀態</span>
            <Select value={state} onChange={(e) => setState(e.target.value as PublicationState | '')}>
              <option value="">全部</option>
              {STATES.map((s) => (
                <option key={s} value={s}>
                  {PUBLICATION_LABELS[s]}
                </option>
              ))}
            </Select>
          </label>
        }
      />
      <LoadState loading={list.loading} error={list.error} empty={items.length === 0} emptyText="目前沒有公開內容" />
      {items.length > 0 && (
        <ul className="space-y-3">
          {items.map((pub) => (
            <Card as="li" key={pub.id} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Link
                  to={`/admin/events/${encodeURIComponent(pub.event_id)}`}
                  className="text-sm text-accent underline underline-offset-4"
                >
                  {pub.event_title}
                </Link>
              </div>
              <PublicationControls publication={pub} onChange={replace} />
            </Card>
          ))}
        </ul>
      )}
    </div>
  )
}

/** 一筆公開內容的狀態、預覽與下架／重新上架操作；事件頁與公開內容列表共用。 */
export function PublicationControls({
  publication: pub,
  onChange,
}: {
  publication: PublicationItem
  onChange: (p: PublicationItem) => void
}) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const message = useErrorMessage()
  const canTakeDown = pub.state !== 'taken_down'

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await action()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    void run(async () => {
      const result = await takeDownPublication(pub.id, reason)
      onChange({
        ...pub,
        state: result.state,
        taken_down_at: new Date().toISOString(),
        takedown_reason: reason.trim() || null,
      })
      setOpen(false)
      setReason('')
    })
  }

  function restore() {
    void run(async () => {
      const result = await restorePublication(pub.id)
      onChange({ ...pub, state: result.state, taken_down_at: null, takedown_reason: null })
      setNotice(result.state === 'published' ? '公開規則判定可以公開，已重新上架' : '公開規則判定目前不公開')
    })
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge tone={PUBLICATION_TONES[pub.state]}>{label(PUBLICATION_LABELS, pub.state)}</Badge>
        <span>{label(PUBLICATION_LEVEL_LABELS, pub.level)}</span>
        <span className="font-mono">{`v${pub.version}`}</span>
        <span className="font-mono">{formatTime(pub.updated_at)}</span>
        {pub.needs_review && <Badge tone="warning">疑似指控，需人工判斷</Badge>}
      </div>
      {pub.preview && <p className="text-sm leading-relaxed text-foreground">{pub.preview}</p>}
      {pub.state === 'taken_down' && (
        <Muted>
          {`下架於 ${formatTime(pub.taken_down_at)}${pub.takedown_reason ? `：${pub.takedown_reason}` : ''}`}
        </Muted>
      )}
      {notice && <Notice>{notice}</Notice>}
      {error && !open && <ErrorText>{error}</ErrorText>}
      {canTakeDown && !open && (
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          下架
        </Button>
      )}
      {canTakeDown && open && (
        <form aria-label="下架公開內容" className="space-y-2" onSubmit={submit}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>下架原因（選填）</span>
            <Input
              value={reason}
              maxLength={200}
              placeholder="會寫入稽核紀錄，請勿填寫個資"
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {error && <ErrorText>{error}</ErrorText>}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={busy}>
              確認下架
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              取消
            </Button>
          </div>
        </form>
      )}
      {pub.state === 'taken_down' && (
        <Button variant="outline" size="sm" onClick={restore} disabled={busy}>
          重新上架
        </Button>
      )}
    </div>
  )
}
