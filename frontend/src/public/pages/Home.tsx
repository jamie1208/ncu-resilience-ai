import { useState } from 'react'

import { Button } from '@design'

import { fetchFeed, fetchNotices, type FeedItem } from '../api'
import { FeedCard, ListState, PageIntro } from '../ui'
import { usePaged } from '../useLoad'

type Filter = 'all' | 'notices'

const FILTERS: [Filter, string][] = [
  ['all', '全部'],
  ['notices', '只看安全提醒'],
]

/** 首頁：最新動態（已查證事件與安全提醒，新到舊），可切換成只看安全提醒。 */
export function Home() {
  const [filter, setFilter] = useState<Filter>('all')
  const feed = usePaged<FeedItem>(
    (cursor) => (filter === 'notices' ? fetchNotices({ cursor }) : fetchFeed({ cursor })),
    filter,
  )
  const listName = filter === 'notices' ? '安全提醒' : '最新動態'

  return (
    <div className="space-y-8">
      <PageIntro eyebrow="校園安全動態" title="最新動態與安全提醒">
        <p>
          整理中央大學校園內的公共安全、設施與交通事件。經官方來源證實的事件會公開小報告；
          未經查證的訊息只會以中性的安全提醒呈現，不代表事情確實發生。
        </p>
      </PageIntro>

      <div role="group" aria-label="篩選動態" className="flex flex-wrap gap-2">
        {FILTERS.map(([value, text]) => (
          <Button
            key={value}
            size="sm"
            variant={filter === value ? 'primary' : 'outline'}
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {text}
          </Button>
        ))}
      </div>

      {feed.items.length > 0 && (
        <ul aria-label={listName} className="space-y-4">
          {feed.items.map((item) => (
            <FeedCard key={item.id} item={item} />
          ))}
        </ul>
      )}
      <ListState
        loading={feed.loading}
        error={feed.error}
        empty={feed.items.length === 0}
        emptyText={filter === 'notices' ? '目前沒有安全提醒。' : '目前沒有公開的動態。'}
        loadMore={feed.loadMore}
      />
    </div>
  )
}
