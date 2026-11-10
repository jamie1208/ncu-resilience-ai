import { Card } from '@design'

import { Link } from '../../admin/Link'
import { fetchFeed, type PublicEventSummary } from '../api'
import { CategoryIcon, ErrorText, Muted, NewsCard, PageIntro } from '../ui'
import { useLoad } from '../useLoad'

/** 首頁一次讀取的快報數（公開 API 單頁上限）。 */
const FEED_LIMIT = 50

interface Section {
  type: string
  label: string
  items: PublicEventSummary[]
}

/** 依類別分區，區塊順序依各類別最新一則快報（新到舊），區內卡片新到舊。 */
function sections(items: PublicEventSummary[]): Section[] {
  const byType = new Map<string, Section>()
  for (const item of items) {
    const section = byType.get(item.event_type) ?? { type: item.event_type, label: item.label, items: [] }
    section.items.push(item)
    byType.set(item.event_type, section)
  }
  return [...byType.values()]
}

function CategoryRow({ section }: { section: Section }) {
  const id = `home-${section.type}`
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h2 id={id} className="flex items-center gap-3 text-xl font-semibold text-foreground">
        <CategoryIcon type={section.type} />
        <span>{section.label}</span>
        <span className="text-sm font-normal text-muted-foreground">{`${section.items.length} 則`}</span>
      </h2>
      <ul
        aria-label={`${section.label}快報`}
        tabIndex={0}
        className="-mx-4 flex snap-x snap-mandatory gap-4 overflow-x-auto px-4 pb-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:mx-0 sm:px-0"
      >
        {section.items.map((item) => (
          <NewsCard key={item.id} item={item} />
        ))}
      </ul>
    </section>
  )
}

function Entry({ to, title, children }: { to: string; title: string; children: string }) {
  return (
    <Card interactive className="space-y-2">
      <h2 className="text-lg font-semibold text-foreground">
        <Link to={to} className="rounded hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {title}
        </Link>
      </h2>
      <p className="text-sm text-muted-foreground">{children}</p>
    </Card>
  )
}

/**
 * 首頁（ADR-0020 決策 4）：依事件類別分區的橫向滑動快報卡片，每張卡片是一則快報（新到舊）；
 * 點卡片先看快報全文，再進系列分析頁。底部放校園韌性成績單與匿名回報入口。
 */
export function Home() {
  const { data, error, loading } = useLoad(() => fetchFeed({ limit: FEED_LIMIT }), 'home')
  const news = (data?.items ?? []).filter((i): i is PublicEventSummary => i.kind === 'content')
  const rows = sections(news)

  return (
    <div className="space-y-10">
      <PageIntro eyebrow="校園快報" title="最新校園快報">
        <p>
          整理中央大學校園內的公共安全、設施與交通事件。每則快報都附上查證狀態：標示「尚未查證」的內容是
          AI 依公開來源整理的摘要，不代表事情確實發生。
        </p>
      </PageIntro>

      {error && <ErrorText>{error.message}</ErrorText>}
      {!error && loading && <Muted>載入中…</Muted>}
      {!error && !loading && rows.length === 0 && <Muted>目前沒有公開的快報。</Muted>}
      {rows.map((section) => (
        <CategoryRow key={section.type} section={section} />
      ))}
      {rows.length > 0 && (
        <p>
          <Link to="/events" className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
            依類別查看全部快報
          </Link>
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Entry to="/resilience" title="校園韌性成績單">
          學校遇到狀況時，處理得好不好？看整體的處理速度與再發生情形。
        </Entry>
        <Entry to="/report" title="匿名回報">
          看到怪怪的事？匿名跟我們說。這裡不是報案管道，緊急狀況請撥 110 或 119。
        </Entry>
      </div>
    </div>
  )
}
