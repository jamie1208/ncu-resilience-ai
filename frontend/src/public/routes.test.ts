import { describe, expect, it } from 'vitest'

import { isAdminPath, matchPublicRoute } from './routes'

describe('isAdminPath', () => {
  it.each(['/admin', '/admin/', '/admin/events', '/admin/events/abc'])('%s 是管理端', (path) => {
    expect(isAdminPath(path)).toBe(true)
  })

  it.each(['/', '/events', '/administrator', '/about'])('%s 不是管理端', (path) => {
    expect(isAdminPath(path)).toBe(false)
  })
})

describe('matchPublicRoute', () => {
  it('對應各公開頁面，忽略結尾斜線', () => {
    expect(matchPublicRoute('/')).toEqual({ page: 'home' })
    expect(matchPublicRoute('/events/')).toEqual({ page: 'events' })
    expect(matchPublicRoute('/events/a%20b')).toEqual({ page: 'event', id: 'a b' })
    expect(matchPublicRoute('/progress')).toEqual({ page: 'progress' })
    expect(matchPublicRoute('/about')).toEqual({ page: 'about' })
    expect(matchPublicRoute('/report/')).toEqual({ page: 'report' })
  })

  it('其他路徑都是找不到頁面', () => {
    expect(matchPublicRoute('/events/a/b')).toEqual({ page: 'not_found' })
    expect(matchPublicRoute('/login')).toEqual({ page: 'not_found' })
  })
})
