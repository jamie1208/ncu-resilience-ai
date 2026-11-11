import { useEffect } from 'react'

import { cn } from '../lib/cn'
import { Link } from '../admin/Link'
import { usePath } from '../admin/router'
import { About } from './pages/About'
import { EventReport } from './pages/EventReport'
import { Events } from './pages/Events'
import { Home } from './pages/Home'
import { Progress } from './pages/Progress'
import { Report } from './pages/Report'
import { Resilience } from './pages/Resilience'
import { Series } from './pages/Series'
import { matchPublicRoute, type PublicRoute } from './routes'
import { PageIntro, SiteFooter } from './ui'

/** 導覽列照示範頁只放三個分頁；事件查詢、改善進度、關於本站從頁尾與頁內連結進入。 */
const NAV = [
  { to: '/', label: '快報', pages: ['home', 'event', 'series', 'events'] },
  { to: '/resilience', label: '校園韌性', pages: ['resilience'] },
  { to: '/report', label: '匿名回報', pages: ['report'] },
] as const

function Page({ route }: { route: PublicRoute }) {
  switch (route.page) {
    case 'home':
      return <Home />
    case 'events':
      return <Events />
    case 'event':
      return <EventReport key={route.id} id={route.id} />
    case 'series':
      return <Series key={route.id} id={route.id} from={route.from} />
    case 'progress':
      return <Progress />
    case 'resilience':
      return <Resilience />
    case 'about':
      return <About />
    case 'report':
      return <Report />
    case 'not_found':
      return (
        <PageIntro eyebrow="404" title="找不到頁面">
          <p>
            這個網址目前沒有任何快報，可能是網址打錯了。
            <Link to="/" className="underline underline-offset-4">
              回到最新快報
            </Link>
          </p>
        </PageIntro>
      )
  }
}

/**
 * 公開網站（P5-T12、P5-T13，ADR-0015）：任何人都能瀏覽與匿名回報，只呼叫 `/api/v1/public/*`，
 * 不檢查登入、不連到管理端；每一頁頁尾標明非官方身分與緊急聯絡方式。
 */
export function PublicApp() {
  const path = usePath()
  const route = matchPublicRoute(path, window.location.search)

  useEffect(() => {
    window.scrollTo?.(0, 0)
  }, [path])

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 pt-3 pb-2">
          <Link
            to="/"
            className="inline-flex min-h-11 items-center gap-2.5 rounded-lg text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span
              aria-hidden="true"
              className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-linear-135 from-accent to-accent/70 text-accent-foreground"
            >
              <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 11a8 8 0 0 1 16 0" />
                <path d="M12 11v9" />
                <path d="M8 20h8" />
              </svg>
            </span>
            <span className="font-display text-xl">中大校園快報</span>
          </Link>
          <nav aria-label="主要頁面" className="flex flex-wrap gap-1">
            {NAV.map((item) => {
              const current = (item.pages as readonly string[]).includes(route.page)
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  aria-current={current ? 'page' : undefined}
                  className={cn(
                    'inline-flex min-h-11 items-center rounded-[10px] px-3.5 text-[15px] font-semibold transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    current ? 'bg-info-muted text-accent' : 'text-foreground/80 hover:bg-muted hover:text-foreground',
                  )}
                >
                  {item.label}
                </Link>
              )
            })}
          </nav>
        </div>
        <div className="mx-auto max-w-6xl px-4 pb-2.5">
          <span className="inline-block rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">個人專題，非校方官方系統</span>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pt-8 pb-16">
        <Page route={route} />
      </main>
      <SiteFooter />
    </div>
  )
}
