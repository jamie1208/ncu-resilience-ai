import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import App from './App'

function at(path: string) {
  window.history.pushState({}, '', path)
}

afterEach(() => {
  at('/')
})

describe('App', () => {
  it('公開首頁清楚顯示非官方免責聲明與 110／119 提示', () => {
    at('/')
    render(<App />)
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
    expect(screen.getByText(/非校方官方系統/)).toBeVisible()
    expect(screen.getByText(/110/)).toBeVisible()
    expect(screen.getByText(/119/)).toBeVisible()
  })

  it('/admin 顯示管理後台', () => {
    at('/admin')
    render(<App />)
    expect(screen.getByRole('heading', { level: 1, name: /管理後台/ })).toBeInTheDocument()
  })

  it('/admin/ 底下的子路徑也導向管理後台', () => {
    at('/admin/events')
    render(<App />)
    expect(screen.getByRole('heading', { level: 1, name: /管理後台/ })).toBeInTheDocument()
  })
})
