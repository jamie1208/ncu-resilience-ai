import { useState, type FormEvent } from 'react'

import { Button, Card, Input, Textarea } from '@design'

import { ApiError } from '../../api/client'
import { submitReport } from '../api'
import { ErrorText, PageIntro } from '../ui'

const MIN_CHARS = 10
const MAX_CHARS = 2000
const MAX_LOCATION_CHARS = 100

const NOTES = [
  '回報的原文不會公開，只有站主能在後台看到；AI 整理並經公開規則判斷後，才可能以摘要或中性提醒的形式出現在網站上。',
  '本站不記錄 IP、裝置資訊，也不要求留下聯絡方式，所以無法回覆或追問你。',
  '請勿填寫姓名、電話、學號等個人資料，也不要指控特定的人；這類內容不會被公開。',
]

function failure(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'RATE_LIMITED') return '回報太頻繁，請稍後再試。'
    if (err.code === 'VALIDATION_ERROR') return '內容格式不正確，請確認字數並移除特殊控制字元後再送出。'
  }
  return '目前無法送出，請稍後再試。'
}

/** 匿名回報（P5-T13，ADR-0015）：說明原文不公開、不記錄身分，只送出內容與選填地點。 */
export function Report() {
  const [content, setContent] = useState('')
  const [location, setLocation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState(false)

  const length = content.trim().length
  const ready = length >= MIN_CHARS && length <= MAX_CHARS

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!ready || busy) return
    setBusy(true)
    setError(null)
    setSent(false)
    try {
      const place = location.trim()
      await submitReport({ content: content.trim(), ...(place ? { location: place } : {}) })
      setContent('')
      setLocation('')
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
          <p role="status" className="rounded-lg border border-border bg-muted px-4 py-3 text-sm text-foreground">
            已收到你的回報，謝謝。系統會和其他來源一起分析，不會直接公開。
          </p>
        )}
        <Button type="submit" disabled={!ready || busy}>
          送出回報
        </Button>
      </form>
    </div>
  )
}
