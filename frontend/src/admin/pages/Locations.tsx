import { useState } from 'react'

import { Button, Card } from '@design'

import { listLocations, splitLocation, type LocationRef } from '../api'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, Notice, PageHeader } from '../ui'

/** 地點（P8-T02）：AI（S13）判斷的別名與包含關係；判斷錯時可拆開，拆開後不再自動合併。 */
export function Locations() {
  const list = useLoad(() => listLocations(), 'locations')
  const items = list.data ?? []
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const message = useErrorMessage()

  async function split(loc: LocationRef) {
    setBusy(loc.id)
    setError(null)
    setNotice(null)
    try {
      await splitLocation(loc.id)
      setNotice(`已拆開「${loc.name}」，之後不會再自動合併`)
      list.reload()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
    }
  }

  function rows(label: string, refs: LocationRef[]) {
    return (
      <ul aria-label={label} className="divide-y divide-border rounded-lg border border-border">
        {refs.map((ref) => (
          <li key={ref.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
            <span className="text-foreground">{ref.name}</span>
            <span className="flex items-center gap-3">
              <span className="font-mono text-xs text-muted-foreground">{`${ref.event_count} 件`}</span>
              <Button
                variant="outline"
                size="sm"
                disabled={busy !== null}
                aria-label={`把「${ref.name}」拆開`}
                onClick={() => void split(ref)}
              >
                拆開
              </Button>
            </span>
          </li>
        ))}
      </ul>
    )
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="地點"
        description="AI 判斷哪些名稱是同一個地方（別名），哪些在某個地方之內。別名的事件會算在一起；判斷錯了可以拆開。"
      />
      {error && <ErrorText>{error}</ErrorText>}
      {notice && <Notice>{notice}</Notice>}
      <LoadState loading={list.loading && !list.data} error={list.error} empty={items.length === 0} emptyText="還沒有任何地點，有事件進來後就會出現在這裡。" />
      {items.length > 0 && (
        <ul className="space-y-3">
          {items.map((loc) => (
            <Card as="li" key={loc.id} className="space-y-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-base font-semibold text-foreground">{loc.name}</h2>
                <span className="text-xs text-muted-foreground">{`${loc.event_count} 件事件（含別名）`}</span>
              </div>
              {loc.aliases.length > 0 && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">別名（算成同一個地方）</p>
                  {rows(`${loc.name}的別名`, loc.aliases)}
                </div>
              )}
              {loc.children.length > 0 && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">位在其中的地點</p>
                  {rows(`${loc.name}之內的地點`, loc.children)}
                  <Muted>上層地點只用來篩選，不併入件數；各地點的件數分開計算。</Muted>
                </div>
              )}
            </Card>
          ))}
        </ul>
      )}
    </div>
  )
}
