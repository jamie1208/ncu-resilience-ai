import { useState, type FormEvent } from 'react'

import { Button, Card, Input } from '@design'

import { ApiError } from '../../api/client'
import { login, type Me } from '../api'
import { Disclaimer, ErrorText } from '../ui'

/** 登入頁（根路徑與未登入時的所有管理路徑）。密碼只送到後端，不保存在前端。 */
export function LoginPage({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const me = await login(username.trim(), password)
      onSignedIn(me)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '登入失敗')
      setPassword('')
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted px-4 py-10">
      <div className="w-full max-w-sm space-y-6">
        <div>
          <p className="font-mono text-xs text-muted-foreground">NCU Resilience AI</p>
          <h1 className="mt-1 font-display text-2xl text-foreground">登入管理後台</h1>
        </div>
        <Card>
          <form className="space-y-4" onSubmit={submit} aria-label="登入">
            <label className="block space-y-1 text-sm font-medium text-foreground">
              <span>帳號</span>
              <Input
                name="username"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label className="block space-y-1 text-sm font-medium text-foreground">
              <span>密碼</span>
              <Input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {error && <ErrorText>{error}</ErrorText>}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? '登入中…' : '登入'}
            </Button>
          </form>
        </Card>
        <Disclaimer />
      </div>
    </div>
  )
}
