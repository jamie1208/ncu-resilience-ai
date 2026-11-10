import { useState, type FormEvent } from 'react'

import { Button, Card, Input, Textarea } from '@design'

import { ApiError } from '../../api/client'
import { fetchReportStatus, submitReport, submitSupplement, type ReportStatus } from '../api'
import { ErrorText, PageIntro } from '../ui'

const MIN_CHARS = 10
const MAX_CHARS = 2000
const MAX_LOCATION_CHARS = 100

const NOTES = [
  '回報的原文不會公開，只有站主能在後台看到；AI 整理並經公開規則判斷後，才可能以摘要或中性提醒的形式出現在網站上。',
  '本站不記錄 IP、裝置資訊，也不要求留下聯絡方式，所以無法回覆或追問你；送出後會拿到一組收據碼，可以用來查詢處理進度或補充說明。',
  '請勿填寫姓名、電話、學號等個人資料，也不要指控特定的人；這類內容不會被公開。',
]

function failure(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'RATE_LIMITED') return '回報太頻繁，請稍後再試。'
    if (err.code === 'VALIDATION_ERROR') return '內容格式不正確，請確認字數並移除特殊控制字元後再送出。'
  }
  return '目前無法送出，請稍後再試。'
}

const STATUS_TEXT: Record<ReportStatus, { label: string; note: string }> = {
  received: { label: '已收到', note: '回報已收到，還在等待系統整理。' },
  processing: { label: '處理中', note: '系統正在分析，或相關事件還在追蹤中。' },
  handled: { label: '已處理', note: '分析已完成，相關事件也已結束或不需要後續追蹤。' },
}

function lookupFailure(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'NOT_FOUND') return '找不到這張收據碼，請確認是否輸入正確。'
    if (err.code === 'RATE_LIMITED') return '查詢太頻繁，每 10 分鐘最多查詢 20 次，請稍後再試。'
  }
  return '目前無法查詢，請稍後再試。'
}

function supplementFailure(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'CONFLICT') return '這張收據的補件次數已達上限。'
    if (err.code === 'NOT_FOUND') return '找不到這張收據碼，請重新查詢。'
  }
  return failure(err)
}

/** 收據碼查詢與補件（P8-T08）：只顯示已收到／處理中／已處理，查到後可以補充說明。 */
function ReceiptLookup() {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [found, setFound] = useState<{ code: string; status: ReportStatus } | null>(null)
  const [content, setContent] = useState('')
  const [location, setLocation] = useState('')
  const [sending, setSending] = useState(false)
  const [supError, setSupError] = useState<string | null>(null)
  const [supSent, setSupSent] = useState(false)

  const length = content.trim().length
  const ready = length >= MIN_CHARS && length <= MAX_CHARS

  async function lookUp(e: FormEvent) {
    e.preventDefault()
    const value = code.trim()
    if (!value || busy) return
    setBusy(true)
    setError(null)
    setFound(null)
    setSupSent(false)
    setSupError(null)
    try {
      setFound({ code: value, status: await fetchReportStatus(value) })
    } catch (err) {
      setError(lookupFailure(err))
    } finally {
      setBusy(false)
    }
  }

  async function supplement(e: FormEvent) {
    e.preventDefault()
    if (!found || !ready || sending) return
    setSending(true)
    setSupError(null)
    setSupSent(false)
    try {
      const place = location.trim()
      await submitSupplement({ receipt_code: found.code, content: content.trim(), ...(place ? { location: place } : {}) })
      setContent('')
      setLocation('')
      setSupSent(true)
    } catch (err) {
      setSupError(supplementFailure(err))
    } finally {
      setSending(false)
    }
  }

  return (
    <section aria-labelledby="receipt-heading" className="max-w-2xl space-y-4">
      <h2 id="receipt-heading" className="text-lg font-semibold text-foreground">
        查詢進度與補件
      </h2>
      <p className="text-sm text-muted-foreground">輸入送出時拿到的收據碼。為了防止有人亂猜收據碼，每 10 分鐘最多查詢 20 次。</p>
      <form aria-label="查詢進度" className="flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={lookUp}>
        <label className="block flex-1 space-y-1 text-sm text-foreground">
          <span>收據碼</span>
          <Input
            aria-label="收據碼"
            value={code}
            maxLength={64}
            autoComplete="off"
            spellCheck={false}
            placeholder="XXXX-XXXX-XXXX-XXXX-XXXX-XXXX"
            className="font-mono"
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        <Button type="submit" variant="outline" disabled={!code.trim() || busy}>
          查詢進度
        </Button>
      </form>
      {error && <ErrorText>{error}</ErrorText>}
      {found && (
        <>
          <div
            role="status"
            aria-label="查詢結果"
            className="rounded-lg border border-border bg-muted px-4 py-3 text-sm text-foreground"
          >
            <p className="font-semibold">{STATUS_TEXT[found.status].label}</p>
            <p className="text-muted-foreground">{STATUS_TEXT[found.status].note}</p>
          </div>
          <form aria-label="補件" className="space-y-4" onSubmit={supplement}>
            <label className="block space-y-1 text-sm text-foreground">
              <span>
                補充說明（{MIN_CHARS}–{MAX_CHARS} 字）
              </span>
              <Textarea
                value={content}
                rows={4}
                maxLength={MAX_CHARS}
                onChange={(e) => setContent(e.target.value)}
              />
            </label>
            <label className="block space-y-1 text-sm text-foreground">
              <span>補充的地點（選填）</span>
              <Input value={location} maxLength={MAX_LOCATION_CHARS} onChange={(e) => setLocation(e.target.value)} />
            </label>
            {supError && <ErrorText>{supError}</ErrorText>}
            {supSent && (
              <p
                role="status"
                aria-label="補件結果"
                className="rounded-lg border border-border bg-muted px-4 py-3 text-sm text-foreground"
              >
                已收到你的補件，會和原本的回報一起整理；補件內容同樣不會公開。
              </p>
            )}
            <Button type="submit" disabled={!ready || sending}>
              送出補件
            </Button>
          </form>
        </>
      )}
    </section>
  )
}

/** 匿名回報（P5-T13，ADR-0015）：說明原文不公開、不記錄身分，只送出內容與選填地點。 */
export function Report() {
  const [content, setContent] = useState('')
  const [location, setLocation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState(false)
  const [receipt, setReceipt] = useState<string | null>(null)

  const length = content.trim().length
  const ready = length >= MIN_CHARS && length <= MAX_CHARS

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!ready || busy) return
    setBusy(true)
    setError(null)
    setSent(false)
    setReceipt(null)
    try {
      const place = location.trim()
      const { receiptCode } = await submitReport({ content: content.trim(), ...(place ? { location: place } : {}) })
      setContent('')
      setLocation('')
      setReceipt(receiptCode)
      setSent(true)
    } catch (err) {
      setError(failure(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-8">
      <PageIntro eyebrow="回報" title="匿名回報">
        <p>看到校園裡的設施故障、安全疑慮或其他異常，可以在這裡告訴我們。</p>
      </PageIntro>

      <Card className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">送出前請先看</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-foreground">
          {NOTES.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
        <p className="text-sm leading-relaxed text-muted-foreground">
          這裡不是通報管道。遇到緊急狀況請直接撥打 110 或 119，或聯絡學校校安中心。
        </p>
      </Card>

      <form aria-label="匿名回報" className="max-w-2xl space-y-4" onSubmit={submit}>
        <label className="block space-y-1 text-sm text-foreground">
          <span>發生了什麼事（{MIN_CHARS}–{MAX_CHARS} 字）</span>
          <Textarea
            value={content}
            rows={6}
            maxLength={MAX_CHARS}
            placeholder="例如：工程五館二樓走廊的燈從昨晚開始一直閃爍"
            onChange={(e) => setContent(e.target.value)}
          />
        </label>
        <p className="text-right font-mono text-xs text-muted-foreground" aria-live="polite">
          {`${length} / ${MAX_CHARS}`}
        </p>
        <label className="block space-y-1 text-sm text-foreground">
          <span>地點（選填）</span>
          <Input
            value={location}
            maxLength={MAX_LOCATION_CHARS}
            placeholder="例如：工程五館"
            onChange={(e) => setLocation(e.target.value)}
          />
        </label>
        {error && <ErrorText>{error}</ErrorText>}
        {sent && (
          <div role="status" className="space-y-2 rounded-lg border border-border bg-muted px-4 py-3 text-sm text-foreground">
            <p>已收到你的回報，謝謝。系統會和其他來源一起分析，不會直接公開。</p>
            {receipt && (
              <>
                <p>
                  你的收據碼：
                  <code className="select-all font-mono font-semibold tracking-wide">{receipt}</code>
                </p>
                <p className="text-muted-foreground">
                  收據碼只會顯示這一次，本站只保存它的雜湊、無法幫你找回；請自行抄下或截圖，之後可以在下方查詢進度或補件。
                </p>
              </>
            )}
          </div>
        )}
        <Button type="submit" disabled={!ready || busy}>
          送出回報
        </Button>
      </form>

      <ReceiptLookup />
    </div>
  )
}
