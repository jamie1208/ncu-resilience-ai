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
import { matchPublicRoute, type PublicRoute } from './routes'
import { PageIntro, SiteFooter } from './ui'

const NAV = [
  { to: '/', label: '最新動態', pages: ['home'] },
  { to: '/events', label: '事件查詢', pages: ['events', 'event'] },
  { to: '/progress', label: '改善進度', pages: ['progress'] },
  { to: '/report', label: '匿名回報', pages: ['report'] },
  { to: '/about', label: '關於本站', pages: ['about'] },
] as const

function Page({ route }: { route: PublicRoute }) {
  switch (route.page) {
    case 'home':
      return <Home />
    case 'events':
      return <Events />
    case 'event':
      return <EventReport key={route.id} id={route.id} />
    case 'progress':
      return <Progress />
    case 'about':
      return <About />
    case 'report':
      return <Report />
    case 'not_found':
      return (
        <PageIntro eyebrow="404" title="找不到頁面">
          <p>
            這個網址沒有內容。
            <Link to="/" className="underline underline-offset-4">
              回到最新動態
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
  const route = matchPublicRoute(path)

  useEffect(() => {
    window.scrollTo?.(0, 0)
  }, [path])

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <Link to="/" className="font-display text-lg text-foreground">
            NCU Resilience AI
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
                    'inline-flex min-h-11 items-center rounded-lg px-3 text-sm transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    current ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                  )}
                >
                  {item.label}
                </Link>
              )
            })}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10 sm:px-6 sm:py-14">
        <Page route={route} />
      </main>
      <SiteFooter />
    </div>
  )
}
