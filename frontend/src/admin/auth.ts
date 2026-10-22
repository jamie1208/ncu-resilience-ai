import { createContext, useContext } from 'react'

import type { Me } from './api'

export interface AuthValue {
  me: Me
  /** session 失效（任何請求回 401）時呼叫，回到登入頁。 */
  expire: () => void
}

export const AuthContext = createContext<AuthValue | null>(null)

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth 必須在 AuthContext 內使用')
  return value
}
