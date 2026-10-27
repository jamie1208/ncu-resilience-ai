/** 極簡的前端路由（History API）；路徑少，不另外安裝路由套件。 */
import { useSyncExternalStore } from 'react'

const EVENT = 'ncu:navigate'

function subscribe(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange)
  window.addEventListener(EVENT, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(EVENT, onChange)
  }
}

/** 目前路徑（不含 query）；路徑變更時重新渲染。 */
export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname)
}

/** 切換到站內路徑；`replace` 不新增瀏覽紀錄（用於導向）。 */
export function navigate(path: string, { replace = false } = {}): void {
  if (path === window.location.pathname) return
  if (replace) window.history.replaceState({}, '', path)
  else window.history.pushState({}, '', path)
  window.dispatchEvent(new Event(EVENT))
}

export type Route =
  | { page: 'root' }
  | { page: 'events'; eventId: string | null }
  | { page: 'alerts' }
  | { page: 'actions' }
  | { page: 'skills' }
  | { page: 'ops' }
  | { page: 'not_found' }

/** 路徑 → 頁面。 */
export function matchRoute(path: string): Route {
  const clean = path.replace(/\/+$/, '') || '/'
  if (clean === '/' || clean === '/admin') return { page: 'root' }
  if (clean === '/admin/events') return { page: 'events', eventId: null }
  const event = /^\/admin\/events\/([^/]+)$/.exec(clean)
  if (event) return { page: 'events', eventId: decodeURIComponent(event[1]) }
  if (clean === '/admin/alerts') return { page: 'alerts' }
  if (clean === '/admin/actions') return { page: 'actions' }
  if (clean === '/admin/skills') return { page: 'skills' }
  if (clean === '/admin/ops') return { page: 'ops' }
  return { page: 'not_found' }
}
