/** 公開網站的路由（ADR-0015）：`/admin` 以外的路徑都屬於公開網站。 */

export type PublicRoute =
  | { page: 'home' }
  | { page: 'events' }
  | { page: 'event'; id: string }
  | { page: 'progress' }
  | { page: 'about' }
  | { page: 'report' }
  | { page: 'not_found' }

/** 管理端路徑：`/admin` 與其子路徑。 */
export function isAdminPath(path: string): boolean {
  return path === '/admin' || path.startsWith('/admin/')
}

/** 路徑 → 公開頁面。 */
export function matchPublicRoute(path: string): PublicRoute {
  const clean = path.replace(/\/+$/, '') || '/'
  if (clean === '/') return { page: 'home' }
  if (clean === '/events') return { page: 'events' }
  const event = /^\/events\/([^/]+)$/.exec(clean)
  if (event) return { page: 'event', id: decodeURIComponent(event[1]) }
  if (clean === '/progress') return { page: 'progress' }
  if (clean === '/about') return { page: 'about' }
  if (clean === '/report') return { page: 'report' }
  return { page: 'not_found' }
}
