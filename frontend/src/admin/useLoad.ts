import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import { useAuth } from './auth'

export interface Loaded<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
  /** 直接改寫已載入的資料（例如寫入成功後更新單筆）。 */
  setData: (update: (current: T | null) => T | null) => void
}

/** 錯誤訊息；401 交給 expire 處理（回登入頁）。 */
export function useErrorMessage(): (e: unknown) => string {
  const { expire } = useAuth()
  return useCallback(
    (e: unknown) => {
      if (e instanceof ApiError) {
        if (e.code === 'UNAUTHORIZED') expire()
        return e.message
      }
      return '發生未預期的錯誤'
    },
    [expire],
  )
}

/** 載入資料；`key` 改變時重新載入（保留舊資料直到新資料到達），過時請求的結果會被丟棄。 */
export function useLoad<T>(load: () => Promise<T>, key: string): Loaded<T> {
  const message = useErrorMessage()
  const [nonce, setNonce] = useState(0)
  const requestKey = `${key}#${nonce}`
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string | null }>({
    key: null,
    data: null,
    error: null,
  })

  useEffect(() => {
    let active = true
    load().then(
      (data) => active && setState({ key: requestKey, data, error: null }),
      (e: unknown) => active && setState({ key: requestKey, data: null, error: message(e) }),
    )
    return () => {
      active = false
    }
    // load 由 key 代表；呼叫端每次渲染都會建立新的 load 函式
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey, message])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  const setData = useCallback(
    (update: (current: T | null) => T | null) => setState((s) => ({ ...s, data: update(s.data) })),
    [],
  )
  const loading = state.key !== requestKey
  return { data: state.data, error: loading ? null : state.error, loading, reload, setData }
}
