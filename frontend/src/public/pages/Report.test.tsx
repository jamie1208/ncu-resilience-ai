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

const STATUS = 'GET /api/v1/public/reports/status'
const SUPPLEMENTS = 'POST /api/v1/public/reports/supplements'
const RECEIPT = 'ABCD-EFGH-IJKL-MNOP-QRST-UVWX'
const MORE = '補充：閃爍的是靠近樓梯那三支燈管，今天早上還是一樣'

function receiptBox() {
  return screen.getByRole('textbox', { name: '收據碼' })
}

async function lookUp(code = RECEIPT) {
  await userEvent.type(receiptBox(), code)
  await userEvent.click(screen.getByRole('button', { name: '查詢進度' }))
}

describe('Report 收據碼（P8-T08）', () => {
  it('送出後顯示只出現一次的收據碼，並提醒自行保存', async () => {
    mockApi({ [REPORTS]: { status: 202, data: { status: 'received', receipt_code: RECEIPT } } })
    render(<Report />)
    await userEvent.type(contentBox(), TEXT)
    await userEvent.click(submitButton())
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent(/已收到/)
    expect(within(status).getByText(RECEIPT)).toBeVisible()
    expect(status).toHaveTextContent(/只會顯示這一次/)
  })

  it('以標頭送出收據碼查詢進度，只顯示處理狀態', async () => {
    const { calls, fetchMock } = mockApi({ [STATUS]: { status: 200, data: { status: 'processing' } } })
    render(<Report />)
    await lookUp(` ${RECEIPT} `)
    const result = await screen.findByRole('status', { name: '查詢結果' })
    expect(result).toHaveTextContent('處理中')
    expect(calls).toHaveLength(1)
    expect(calls[0].path).not.toContain(RECEIPT)
    expect(calls[0].headers.get('X-Receipt-Code')).toBe(RECEIPT)
    expect(fetchMock.mock.calls[0][1]?.credentials).toBe('omit')
    expect(fetchMock.mock.calls[0][1]?.cache).toBe('no-store')
  })

  it.each([
    ['received', '已收到'],
    ['handled', '已處理'],
  ])('狀態 %s 顯示為「%s」', async (value, label) => {
    mockApi({ [STATUS]: { status: 200, data: { status: value } } })
    render(<Report />)
    await lookUp()
    expect(await screen.findByRole('status', { name: '查詢結果' })).toHaveTextContent(label)
  })

  it('找不到收據碼時顯示請確認，不顯示補件表單', async () => {
    mockApi({ [STATUS]: { status: 404, error: { code: 'NOT_FOUND', message: '找不到這張收據碼' } } })
    render(<Report />)
    await lookUp()
    expect(await screen.findByRole('alert')).toHaveTextContent(/找不到這張收據碼/)
    expect(screen.queryByRole('button', { name: '送出補件' })).not.toBeInTheDocument()
  })

  it('查詢被限速時說明每 10 分鐘最多查詢 20 次', async () => {
    mockApi({ [STATUS]: { status: 429, error: { code: 'RATE_LIMITED', message: '查詢太頻繁，請稍後再試' } } })
    render(<Report />)
    expect(screen.getByText(/為了防止有人亂猜收據碼，每 10 分鐘最多查詢 20 次/)).toBeVisible()
    await lookUp()
    expect(await screen.findByRole('alert')).toHaveTextContent('查詢太頻繁，每 10 分鐘最多查詢 20 次，請稍後再試。')
  })

  it('查到後可以補件，送出收據碼、內容與地點', async () => {
    const { calls } = mockApi({
      [STATUS]: { status: 200, data: { status: 'received' } },
      [SUPPLEMENTS]: { status: 202, data: { status: 'received' } },
    })
    render(<Report />)
    await lookUp()
    await screen.findByRole('status', { name: '查詢結果' })
    const form = screen.getByRole('form', { name: '補件' })
    const send = within(form).getByRole('button', { name: '送出補件' })
    expect(send).toBeDisabled()
    await userEvent.type(within(form).getByRole('textbox', { name: /補充說明/ }), `  ${MORE} `)
    await userEvent.type(within(form).getByRole('textbox', { name: /地點/ }), '工程五館')
    await userEvent.click(send)
    expect(await screen.findByRole('status', { name: '補件結果' })).toHaveTextContent(/已收到你的補件/)
    const sent = calls.find((c) => c.method === 'POST')
    expect(sent?.body).toEqual({ receipt_code: RECEIPT, content: MORE, location: '工程五館' })
    expect(within(form).getByRole('textbox', { name: /補充說明/ })).toHaveValue('')
  })

  it('補件次數達上限時說明', async () => {
    mockApi({
      [STATUS]: { status: 200, data: { status: 'processing' } },
      [SUPPLEMENTS]: { status: 409, error: { code: 'CONFLICT', message: '每張收據最多補件 5 次' } },
    })
    render(<Report />)
    await lookUp()
    const form = await screen.findByRole('form', { name: '補件' })
    await userEvent.type(within(form).getByRole('textbox', { name: /補充說明/ }), MORE)
    await userEvent.click(within(form).getByRole('button', { name: '送出補件' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent(/上限/)
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
