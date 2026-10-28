import { useState } from 'react'

import { Button } from '@design'

import { fetchCategories, fetchEvents, type PublicEventSummary } from '../api'
import { ErrorText, FeedCard, ListState, PageIntro } from '../ui'
import { useLoad, usePaged } from '../useLoad'

/** 事件分類查詢：依類別列出已查證、公開內容的事件。 */
export function Events() {
  const [type, setType] = useState<string | null>(null)
  const categories = useLoad(fetchCategories, 'categories')
  const events = usePaged<PublicEventSummary>((cursor) => fetchEvents({ type, cursor }), type ?? '')
  const selected = categories.data?.find((c) => c.event_type === type)

  return (
    <div className="space-y-8">
      <PageIntro eyebrow="事件查詢" title="事件分類查詢">
        <p>這裡只列出經官方來源或人工查證證實的事件；未經查證的訊息請看首頁的安全提醒。</p>
      </PageIntro>

      {categories.error && <ErrorText>{categories.error.message}</ErrorText>}
      {categories.data && (
        <div role="group" aria-label="事件類別" className="flex flex-wrap gap-2">
          <Button size="sm" variant={type === null ? 'primary' : 'outline'} aria-pressed={type === null} onClick={() => setType(null)}>
            全部
          </Button>
          {categories.data.map((c) => (
            <Button
              key={c.event_type}
              size="sm"
              variant={type === c.event_type ? 'primary' : 'outline'}
              aria-pressed={type === c.event_type}
              onClick={() => setType(c.event_type)}
            >
              {`${c.label} ${c.content}`}
            </Button>
          ))}
        </div>
      )}

      {events.items.length > 0 && (
        <ul aria-label="公開事件" className="space-y-4">
          {events.items.map((item) => (
            <FeedCard key={item.id} item={item} />
          ))}
        </ul>
      )}
      <ListState
        loading={events.loading}
        error={events.error}
        empty={events.items.length === 0}
        emptyText={type ? '這個類別目前沒有已查證的公開事件。' : '目前沒有已查證的公開事件。'}
        loadMore={events.loadMore}
      />
      {selected && selected.safety_notice > 0 && !events.loading && (
        <p className="text-sm text-muted-foreground">{`這個類別另有 ${selected.safety_notice} 則安全提醒，可在首頁查看。`}</p>
      )}
    </div>
  )
}
