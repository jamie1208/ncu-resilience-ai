import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi } from '../../test/mockApi'
import { PublicApp } from '../PublicApp'
import { Report } from './Report'

const REPORTS = 'POST /api/v1/public/reports'
const TEXT = '工程五館二樓走廊的燈一直閃爍，晚上經過有點危險'

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.unstubAllGlobals()
})

function contentBox() {
  return screen.getByRole('textbox', { name: /發生了什麼事/ })
}

function locationBox() {
  return screen.getByRole('textbox', { name: /地點/ })
}

function submitButton() {
  return screen.getByRole('button', { name: '送出回報' })
}

describe('Report', () => {
  it('說明原文不會公開、不保存 IP，並提醒緊急狀況撥打 110、119', () => {
    mockApi({})
    render(<Report />)
    expect(screen.getByRole('heading', { level: 1, name: '匿名回報' })).toBeInTheDocument()
    expect(screen.getByText(/原文不會公開/)).toBeVisible()
    expect(screen.getByText(/不記錄 IP/)).toBeVisible()
    expect(screen.getByText(/110/)).toBeVisible()
    expect(screen.getByText(/119/)).toBeVisible()
    expect(screen.getByText(/請勿填寫姓名、電話/)).toBeVisible()
  })

  it('內容少於 10 字時不能送出，並顯示字數', async () => {
    mockApi({})
    render(<Report />)
    expect(submitButton()).toBeDisabled()
    await userEvent.type(contentBox(), '燈壞了')
    expect(submitButton()).toBeDisabled()
    expect(screen.getByText('3 / 2000')).toBeInTheDocument()
    expect(contentBox()).toHaveAttribute('maxlength', '2000')
    expect(locationBox()).toHaveAttribute('maxlength', '100')
  })

  it('送出內容與地點（去除前後空白），不帶 cookie，成功後清空表單並顯示已收到', async () => {
    const { calls, fetchMock } = mockApi({ [REPORTS]: { status: 202, data: { status: 'received' } } })
    render(<Report />)
    await userEvent.type(contentBox(), `  ${TEXT}  `)
    await userEvent.type(locationBox(), ' 工程五館 ')
    await userEvent.click(submitButton())

    expect(await screen.findByRole('status')).toHaveTextContent(/已收到/)
    expect(calls).toHaveLength(1)
    expect(calls[0].method).toBe('POST')
    expect(calls[0].body).toEqual({ content: TEXT, location: '工程五館' })
    expect(calls[0].headers.get('Content-Type')).toBe('application/json')
    expect(fetchMock.mock.calls[0][1]?.credentials).toBe('omit')
    expect(contentBox()).toHaveValue('')
    expect(locationBox()).toHaveValue('')
  })

  it('沒有填地點時不送 location', async () => {
    const { calls } = mockApi({ [REPORTS]: { status: 202, data: { status: 'received' } } })
    render(<Report />)
    await userEvent.type(contentBox(), TEXT)
    await userEvent.type(locationBox(), '   ')
    await userEvent.click(submitButton())
    await screen.findByRole('status')
    expect(calls[0].body).toEqual({ content: TEXT })
  })

  it('被限速時顯示稍後再試，保留已填的內容', async () => {
    mockApi({ [REPORTS]: { status: 429, error: { code: 'RATE_LIMITED', message: '回報太頻繁，請稍後再試' } } })
    render(<Report />)
    await userEvent.type(contentBox(), TEXT)
    await userEvent.click(submitButton())
    expect(await screen.findByRole('alert')).toHaveTextContent(/稍後再試/)
    expect(contentBox()).toHaveValue(TEXT)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('格式錯誤時顯示需要修改內容', async () => {
    mockApi({ [REPORTS]: { status: 422, error: { code: 'VALIDATION_ERROR', message: '請求參數不正確' } } })
    render(<Report />)
    await userEvent.type(contentBox(), TEXT)
    await userEvent.click(submitButton())
    expect(await screen.findByRole('alert')).toHaveTextContent(/格式/)
  })
})

describe('PublicApp /report', () => {
  it('導覽列有匿名回報，頁尾標明非官方', async () => {
    mockApi({})
    window.history.pushState({}, '', '/report')
    render(<PublicApp />)
    expect(await screen.findByRole('heading', { level: 1, name: '匿名回報' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: '主要頁面' })
    expect(within(nav).getByRole('link', { name: '匿名回報' })).toHaveAttribute('aria-current', 'page')
    expect(within(screen.getByRole('contentinfo')).getByText(/個人專題，非國立中央大學官方系統/)).toBeVisible()
  })
})
