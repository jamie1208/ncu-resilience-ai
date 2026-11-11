import { Link } from '../../admin/Link'
import { fetchFeed, type PublicEventSummary } from '../api'
import { cn } from '../../lib/cn'
import { ErrorText, Muted, NewsCard, PageIntro } from '../ui'
import { categoryTone } from '../categories'
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
    <section aria-labelledby={id} className="space-y-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id={id} className="flex items-center gap-2.5 text-[22px] font-bold text-foreground">
          <span aria-hidden="true" className={cn('h-2.5 w-2.5 rounded-[3px]', categoryTone(section.type).solid)} />
          {section.label}
        </h2>
        <span className="text-[13px] text-muted-foreground">{`${section.items.length} 則 · 左右滑動`}</span>
      </div>
      <ul
        aria-label={`${section.label}快報`}
        tabIndex={0}
        className="-mx-4 flex snap-x snap-mandatory gap-4 overflow-x-auto px-4 pt-1 pb-3.5 [scrollbar-width:thin] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:mx-0 sm:px-0.5"
      >
        {section.items.map((item) => (
          <NewsCard key={item.id} item={item} />
        ))}
      </ul>
    </section>
  )
}

const ENTRY = 'group block rounded-[20px] p-6 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none motion-reduce:hover:translate-y-0'

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
      <PageIntro title="今天校園發生了什麼">
        <p>每件事一張快報，依類別分區，左右滑看更多。同類型的事件會累積成系列，點進快報就能看到整個系列的分析。</p>
        <p className="text-sm text-muted-foreground">標示「尚未查證」的內容是 AI 依公開來源整理的摘要，不代表事情確實發生。</p>
      </PageIntro>

      {error && <ErrorText>{error.message}</ErrorText>}
      {!error && loading && <Muted>載入中…</Muted>}
      {!error && !loading && rows.length === 0 && <Muted>目前沒有公開的快報，校園暫時風平浪靜。</Muted>}
      {rows.map((section) => (
        <CategoryRow key={section.type} section={section} />
      ))}

      <div className="grid gap-4 sm:grid-cols-2">
        <Link to="/resilience" className={cn(ENTRY, 'bg-foreground text-background')}>
          <span className="block text-[13px] text-background/75">校園韌性成績單</span>
          <span className="mt-2 block text-[22px] font-bold">學校遇到狀況時，處理得好不好？</span>
          <span className="mt-2 block text-sm leading-relaxed text-background/75">
            用四個問題打分數：多快知道、多快處理、多久恢復、有沒有記取教訓。
          </span>
          <span className="mt-2 block text-sm font-semibold text-background">看成績單 →</span>
        </Link>
        <Link to="/report" className={cn(ENTRY, 'border border-border bg-card text-foreground')}>
          <span className="block text-[13px] text-muted-foreground">匿名回報</span>
          <span className="mt-2 block text-[22px] font-bold">看到怪怪的事？匿名跟我們說</span>
          <span className="mt-2 block text-sm leading-relaxed text-muted-foreground">
            不用登入、不記錄你是誰。你寫的原文不會公開，網站只放 AI 整理後的摘要。
          </span>
          <span className="mt-1 block text-sm leading-relaxed font-medium text-danger">這裡不是報案管道，緊急狀況請撥 110 或 119。</span>
          <span className="mt-2 block text-sm font-semibold text-accent">我要回報 →</span>
        </Link>
      </div>
    </div>
  )
}
