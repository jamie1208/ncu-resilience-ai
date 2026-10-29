import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@design'

import { cn } from '../lib/cn'
import { clearCsrfToken, fetchMe, logout, type Me } from './api'
import { AuthContext } from './auth'
import { Link } from './Link'
import { AlertCenter } from './pages/AlertCenter'
import { EventWorkbench } from './pages/EventWorkbench'
import { LoginPage } from './pages/LoginPage'
import { Ops } from './pages/Ops'
import { Publications } from './pages/Publications'
import { Resilience } from './pages/Resilience'
import { ResponseActions } from './pages/ResponseActions'
import { SkillsDashboard } from './pages/SkillsDashboard'
import { matchRoute, navigate, usePath, type Route } from './router'
import { Disclaimer, Muted, PageHeader } from './ui'

// Cytoscape.js 體積大，只在開啟關聯圖時載入（不進公開網站與其他頁面的 bundle）
const RelationGraph = lazy(() => import('./pages/RelationGraph').then((m) => ({ default: m.RelationGraph })))

const NAV = [
  { to: '/admin/events', label: '事件工作台', page: 'events' },
  { to: '/admin/alerts', label: '通知中心', page: 'alerts' },
  { to: '/admin/actions', label: '改善追蹤', page: 'actions' },
  { to: '/admin/relations', label: '事件關聯', page: 'relations' },
  { to: '/admin/resilience', label: '韌性成績單', page: 'resilience' },
  { to: '/admin/publications', label: '公開內容', page: 'publications' },
  { to: '/admin/skills', label: 'Skills', page: 'skills' },
  { to: '/admin/ops', label: '運維', page: 'ops' },
] as const

type AuthState = { state: 'checking' } | { state: 'anonymous' } | { state: 'signed_in'; me: Me }

/**
 * 管理前端（`/admin` 以下，ADR-0015）：先以 /auth/me 確認登入，未登入一律顯示登入頁；
 * `/admin` 登入後導向事件工作台。
 */
export function AdminApp() {
  const path = usePath()
  const route = matchRoute(path)
  const [auth, setAuth] = useState<AuthState>({ state: 'checking' })

  useEffect(() => {
    let active = true
    fetchMe().then(
      (me) => active && setAuth({ state: 'signed_in', me }),
      () => active && setAuth({ state: 'anonymous' }),
    )
    return () => {
      active = false
    }
  }, [])

  const signedIn = auth.state === 'signed_in'
  useEffect(() => {
    if (signedIn && route.page === 'root') navigate('/admin/events', { replace: true })
  }, [signedIn, route.page])

  const expire = useCallback(() => {
    clearCsrfToken()
    setAuth({ state: 'anonymous' })
  }, [])
  const me = auth.state === 'signed_in' ? auth.me : null
  const authValue = useMemo(() => (me ? { me, expire } : null), [me, expire])

  if (auth.state === 'checking') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-muted">
        <Muted>載入中…</Muted>
      </div>
    )
  }
  if (!authValue) return <LoginPage onSignedIn={(signed) => setAuth({ state: 'signed_in', me: signed })} />

  async function signOut() {
    try {
      await logout()
    } catch {
      // session 已失效也一樣回到登入頁
    }
    setAuth({ state: 'anonymous' })
  }

  return (
    <AuthContext.Provider value={authValue}>
      <div className="min-h-screen bg-muted text-foreground">
        <header className="border-b border-border bg-card">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2 sm:px-6">
            <span className="font-display text-base">NCU Resilience AI</span>
            <nav aria-label="主要頁面" className="order-last w-full overflow-x-auto sm:order-none sm:w-auto">
              <ul className="flex gap-1">
                {NAV.map((item) => {
                  const current = route.page === item.page
                  return (
                    <li key={item.to}>
                      <Link
                        to={item.to}
                        aria-current={current ? 'page' : undefined}
                        className={cn(
                          'inline-flex h-11 items-center rounded-lg px-3 text-sm whitespace-nowrap',
                          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                          current
                            ? 'bg-muted font-semibold text-foreground'
                            : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                        )}
                      >
                        {item.label}
                      </Link>
                    </li>
                  )
                })}
              </ul>
            </nav>
            <div className="ml-auto flex items-center gap-2">
              <span className="font-mono text-xs text-muted-foreground">{authValue.me.username}</span>
              <Button variant="ghost" size="sm" onClick={signOut}>
                登出
              </Button>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
          <Page route={route} />
        </main>
        <footer className="mx-auto max-w-7xl px-4 pb-8 sm:px-6">
          <Disclaimer />
        </footer>
      </div>
    </AuthContext.Provider>
  )
}

function Page({ route }: { route: Route }) {
  switch (route.page) {
    case 'root':
      return <Muted>導向中…</Muted>
    case 'events':
      return <EventWorkbench eventId={route.eventId} />
    case 'alerts':
      return <AlertCenter />
    case 'actions':
      return <ResponseActions />
    case 'relations':
      return (
        <Suspense fallback={<Muted>載入中…</Muted>}>
          <RelationGraph />
        </Suspense>
      )
    case 'resilience':
      return <Resilience />
    case 'publications':
      return <Publications />
    case 'skills':
      return <SkillsDashboard />
    case 'ops':
      return <Ops />
    case 'not_found':
      return (
        <div className="space-y-3">
          <PageHeader title="找不到頁面" />
          <Link to="/admin/events" className="text-sm text-accent underline underline-offset-4">
            回事件工作台
          </Link>
        </div>
      )
  }
}
