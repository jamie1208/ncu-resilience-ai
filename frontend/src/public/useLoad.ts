/** 公開頁面的資料載入；不需要登入，錯誤只轉成可顯示的訊息。 */
import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import type { Page } from './api'

export function errorMessage(e: unknown): string {
  return e instanceof ApiError ? e.message : '發生未預期的錯誤'
}

export interface Loaded<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
}

/** 載入資料；`key` 改變時重新載入，過時請求的結果會被丟棄。 */
export function useLoad<T>(load: () => Promise<T>, key: string): Loaded<T> {
  const [state, setState] = useState<{ key: string | null; data: T | null; error: ApiError | null }>({
    key: null,
    data: null,
    error: null,
  })

  useEffect(() => {
    let active = true
    load().then(
      (data) => active && setState({ key, data, error: null }),
      (e: unknown) =>
        active &&
        setState({ key, data: null, error: e instanceof ApiError ? e : new ApiError('SERVER_ERROR', errorMessage(e), 0) }),
    )
    return () => {
      active = false
    }
    // load 由 key 代表；呼叫端每次渲染都會建立新的 load 函式
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const loading = state.key !== key
  return { data: loading ? null : state.data, error: loading ? null : state.error, loading }
}

export interface Paged<T> {
  items: T[]
  error: string | null
  loading: boolean
  /** 還有下一頁時的載入函式；沒有下一頁為 null。 */
  loadMore: (() => void) | null
}

/** cursor 分頁列表：`key` 改變時從第一頁重新載入，「載入更多」把下一頁接在後面。 */
export function usePaged<T>(load: (cursor: string | null) => Promise<Page<T>>, key: string): Paged<T> {
  const [state, setState] = useState<{
    key: string | null
    items: T[]
    next: string | null
    error: string | null
    busy: boolean
  }>({ key: null, items: [], next: null, error: null, busy: false })

  useEffect(() => {
    let active = true
    load(null).then(
      (page) => active && setState({ key, items: page.items, next: page.nextCursor, error: null, busy: false }),
      (e: unknown) => active && setState({ key, items: [], next: null, error: errorMessage(e), busy: false }),
    )
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const { next, busy } = state
  const more = useCallback(() => {
    if (!next) return
    setState((s) => ({ ...s, busy: true }))
    load(next).then(
      (page) =>
        setState((s) =>
          s.key === key ? { ...s, items: [...s.items, ...page.items], next: page.nextCursor, busy: false } : s,
        ),
      (e: unknown) => setState((s) => (s.key === key ? { ...s, error: errorMessage(e), busy: false } : s)),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, next])

  const loading = state.key !== key || busy
  const current = state.key === key
  return {
    items: current ? state.items : [],
    error: current ? state.error : null,
    loading,
    loadMore: current && next && !busy ? more : null,
  }
}
