import { Badge, Card } from '@design'

import { Link } from '../../admin/Link'
import { fetchProgress, type PublicProgress } from '../api'
import { ActionList, ListState, PageIntro } from '../ui'
import { usePaged } from '../useLoad'

/** 改善進度（P8-T06 起以系列為單位）：每個系列的改善措施與目前狀態，連到系列分析頁。 */
export function Progress() {
  const list = usePaged<PublicProgress>((cursor) => fetchProgress({ cursor }), 'progress')
  return (
    <div className="space-y-8">
      <PageIntro eyebrow="追蹤到底" title="改善進度">
        <p>事情不是報完就結束。這裡持續追蹤每個系列的改善措施，狀態一更新就同步；點系列名稱，看完整分析。</p>
      </PageIntro>
      {list.items.length > 0 && (
        <ul aria-label="改善進度" className="space-y-4">
          {list.items.map((item) => {
            const titleId = `progress-${item.id}`
            return (
              <Card as="li" key={item.id} aria-labelledby={titleId} className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge>{item.label}</Badge>
                  {item.occurrence_date && <span className="text-xs text-muted-foreground">{`最近一起 ${item.occurrence_date}`}</span>}
                </div>
                <h2 id={titleId} className="text-lg font-semibold text-foreground">
                  <Link
                    to={`/series/${encodeURIComponent(item.id)}`}
                    className="rounded hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {item.title}
                  </Link>
                </h2>
                <ActionList actions={item.progress} labelledBy={titleId} />
              </Card>
            )
          })}
        </ul>
      )}
      <ListState
        loading={list.loading}
        error={list.error}
        empty={list.items.length === 0}
        emptyText="目前還沒有公開的改善措施，有新進度會第一時間更新在這裡。"
        loadMore={list.loadMore}
      />
    </div>
  )
}
