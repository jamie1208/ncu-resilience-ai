import { useId, useState, type FormEvent, type ReactNode } from 'react'

import { Badge, Button, Card, Input, Textarea } from '@design'

import {
  createScenario,
  listScenarios,
  type CostTier,
  type ScenarioLimitation,
  type ScenarioMeasureSpec,
  type ScenarioRequest,
  type ScenarioRun,
} from '../api'
import { formatPercent, formatTime, label } from '../format'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, PageHeader, Section } from '../ui'

const MAX_MEASURES = 10
const MAX_SEED = 2 ** 31 - 1
const HYPOTHETICAL = '假設情境，非成效預測'

const COST_TIER_LABELS: Record<CostTier, string> = {
  low: '成本低',
  medium: '成本中',
  high: '成本高',
  unknown: '成本未知',
}

const LIMITATION_LABELS: Record<ScenarioLimitation, string> = {
  hypothetical_not_prediction: '假設情境，不是成效預測',
  independence_assumed: '假設各措施效果互相獨立',
  unknown_effects: '有措施效果未知，未納入計算',
  cost_unknown: '有措施成本未知',
  small_baseline: '基準件數少於 5，結果不穩定',
  unsupported_assumption: '有措施未提供假設依據',
}

/** 件數取一位小數。 */
function formatCount(value: number): string {
  return value.toFixed(1)
}

function measureNames(run: ScenarioRun): Record<string, string> {
  return Object.fromEntries(run.input.measures.map((m) => [m.measure_id, m.name]))
}

/**
 * What-if 模擬（P7-T03，規格 F09）：填寫基準件數與措施的效果假設，數值由引擎計算（P7-T02），
 * S10 只解釋假設與限制。結果一律標示「假設情境，非成效預測」。只在管理端。
 */
export function Scenario() {
  const history = useLoad(listScenarios, 'scenarios')
  const [selected, setSelected] = useState<ScenarioRun | null>(null)

  function created(run: ScenarioRun) {
    setSelected(run)
    history.setData((list) => [run, ...(list ?? [])].slice(0, 50))
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="What-if 模擬"
        description="件數 = 基準 × Π(1 − 措施效果)；效果是你填的假設區間，引擎依固定種子計算範圍與敏感度。"
      />
      <HypotheticalNote />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <Card className="min-w-0">
          <ScenarioForm onCreated={created} />
        </Card>
        <div className="min-w-0 space-y-6">
          {selected ? (
            <ScenarioResult key={selected.id} run={selected} />
          ) : (
            <Card>
              <Muted>填寫參數後執行模擬，或從下方「最近的模擬」開啟。</Muted>
            </Card>
          )}
        </div>
      </div>
      <Card>
        <Section id="scenario-history" title="最近的模擬">
          <LoadState
            loading={history.loading && !history.data}
            error={history.error}
            empty={history.data?.length === 0}
            emptyText="還沒有模擬紀錄"
          />
          {history.data && history.data.length > 0 && (
            <ul className="divide-y divide-border">
              {history.data.map((run) => (
                <li key={run.id}>
                  <button
                    type="button"
                    aria-pressed={run.id === selected?.id}
                    onClick={() => setSelected(run)}
                    className="flex min-h-11 w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-2 py-2 text-left text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:bg-muted"
                  >
                    <span className="font-mono text-xs text-muted-foreground">{formatTime(run.created_at)}</span>
                    <span className="order-last w-full min-w-0 break-words text-foreground sm:order-none sm:w-auto sm:flex-1">
                      {run.input.measures.map((m) => m.name).join('、')}
                    </span>
                    {run.output.estimate ? (
                      <span className="font-mono text-xs text-foreground">
                        {`${run.output.baseline} → ${formatCount(run.output.estimate.point)} 件`}
                      </span>
                    ) : (
                      <Badge tone="warning">無法估計</Badge>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </Card>
    </div>
  )
}

/** 頁面頂端的醒目標示：不加動畫，兩套設計都用警示語意色。 */
function HypotheticalNote() {
  const id = useId()
  return (
    <div role="note" aria-labelledby={id} className="rounded-lg border border-warning/40 bg-warning-muted px-4 py-3">
      <p id={id} className="text-base font-semibold text-warning">
        {HYPOTHETICAL}
      </p>
      <p className="mt-1 text-sm text-foreground">
        結果完全取決於你填寫的效果假設，只用來比較不同假設下的情境；不代表措施實際會有的成效，也不能當作決策依據。
      </p>
    </div>
  )
}

// ---- 參數表單 ----

interface MeasureDraft {
  key: number
  name: string
  low: string
  high: string
  cost: string
  rationale: string
}

function emptyMeasure(key: number): MeasureDraft {
  return { key, name: '', low: '', high: '', cost: '', rationale: '' }
}

class FormError extends Error {}

function parseNumber(text: string, message: string, { integer = false, min = 0, max = Infinity } = {}): number {
  const value = Number(text.trim())
  if (text.trim() === '' || !Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
    throw new FormError(message)
  }
  return value
}

function toMeasure(draft: MeasureDraft, index: number): ScenarioMeasureSpec {
  const prefix = `措施 ${index + 1}：`
  const name = draft.name.trim()
  if (!name) throw new FormError(`${prefix}請填寫名稱`)
  const hasLow = draft.low.trim() !== ''
  const hasHigh = draft.high.trim() !== ''
  if (hasLow !== hasHigh) throw new FormError(`${prefix}效果上下限要同時填寫或同時留空`)
  let effectLow: number | null = null
  let effectHigh: number | null = null
  if (hasLow) {
    const percent = { max: 100 }
    effectLow = Number((parseNumber(draft.low, `${prefix}效果下限要在 0–100 之間`, percent) / 100).toFixed(4))
    effectHigh = Number((parseNumber(draft.high, `${prefix}效果上限要在 0–100 之間`, percent) / 100).toFixed(4))
    if (effectLow > effectHigh) throw new FormError(`${prefix}效果下限不可大於上限`)
  }
  const cost = draft.cost.trim() ? parseNumber(draft.cost, `${prefix}成本要是非負整數`, { integer: true }) : null
  return { name, effect_low: effectLow, effect_high: effectHigh, cost, rationale: draft.rationale.trim() || null }
}

function ScenarioForm({ onCreated }: { onCreated: (run: ScenarioRun) => void }) {
  const message = useErrorMessage()
  const [baseline, setBaseline] = useState('')
  const [windowDays, setWindowDays] = useState('90')
  const [eventType, setEventType] = useState('')
  const [location, setLocation] = useState('')
  const [seed, setSeed] = useState('')
  const [measures, setMeasures] = useState<MeasureDraft[]>([emptyMeasure(0)])
  const [nextKey, setNextKey] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function update(key: number, change: Partial<MeasureDraft>) {
    setMeasures((list) => list.map((m) => (m.key === key ? { ...m, ...change } : m)))
  }

  function add() {
    setMeasures((list) => [...list, emptyMeasure(nextKey)])
    setNextKey((k) => k + 1)
  }

  function build(): ScenarioRequest {
    const body: ScenarioRequest = {
      baseline: parseNumber(baseline, '基準件數要是 0 以上的數字'),
      window_days: parseNumber(windowDays, '期間要是 1–730 天的整數', { integer: true, min: 1, max: 730 }),
      measures: measures.map(toMeasure),
    }
    if (seed.trim()) body.seed = parseNumber(seed, `種子要是 0–${MAX_SEED} 的整數`, { integer: true, max: MAX_SEED })
    if (eventType.trim()) body.event_type = eventType.trim()
    if (location.trim()) body.location = location.trim()
    return body
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    let body: ScenarioRequest
    try {
      body = build()
    } catch (err) {
      if (!(err instanceof FormError)) throw err
      setError(err.message)
      return
    }
    setError(null)
    setBusy(true)
    try {
      onCreated(await createScenario(body))
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form aria-label="模擬參數" noValidate onSubmit={submit} className="space-y-4">
      <h2 className="text-sm font-semibold text-foreground">模擬參數</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="基準件數">
          <Input inputMode="decimal" value={baseline} placeholder="例如 12" onChange={(e) => setBaseline(e.target.value)} />
        </Field>
        <Field label="期間（天）">
          <Input inputMode="numeric" value={windowDays} onChange={(e) => setWindowDays(e.target.value)} />
        </Field>
        <Field label="事件類型（選填）">
          <Input value={eventType} maxLength={32} placeholder="例如 security" onChange={(e) => setEventType(e.target.value)} />
        </Field>
        <Field label="地點（選填）">
          <Input value={location} maxLength={100} placeholder="請勿填寫個資" onChange={(e) => setLocation(e.target.value)} />
        </Field>
        <Field label="隨機種子（選填）">
          <Input inputMode="numeric" value={seed} placeholder="留空自動產生" onChange={(e) => setSeed(e.target.value)} />
        </Field>
      </div>
      <p className="text-xs text-muted-foreground">
        效果是件數降低比例的假設區間（%）；上下限都留空代表效果未知，該措施不納入計算。
      </p>
      <ol className="space-y-3">
        {measures.map((m, i) => (
          <li key={m.key}>
            <MeasureFields
              index={i}
              draft={m}
              onChange={(change) => update(m.key, change)}
              onRemove={measures.length > 1 ? () => setMeasures((list) => list.filter((x) => x.key !== m.key)) : null}
            />
          </li>
        ))}
      </ol>
      {error && <ErrorText>{error}</ErrorText>}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" disabled={measures.length >= MAX_MEASURES} onClick={add}>
          新增措施
        </Button>
        <Button type="submit" disabled={busy}>
          {busy ? '模擬中…' : '執行模擬'}
        </Button>
      </div>
    </form>
  )
}

function MeasureFields({ index, draft, onChange, onRemove }: {
  index: number
  draft: MeasureDraft
  onChange: (change: Partial<MeasureDraft>) => void
  onRemove: (() => void) | null
}) {
  const title = `措施 ${index + 1}`
  return (
    <fieldset className="space-y-2 rounded-lg border border-border p-3">
      <legend className="px-1 text-sm font-medium text-foreground">{title}</legend>
      <Field label="名稱">
        <Input value={draft.name} maxLength={100} placeholder="例如加裝路燈" onChange={(e) => onChange({ name: e.target.value })} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="效果下限（%）">
          <Input inputMode="decimal" value={draft.low} placeholder="例如 20" onChange={(e) => onChange({ low: e.target.value })} />
        </Field>
        <Field label="效果上限（%）">
          <Input inputMode="decimal" value={draft.high} placeholder="例如 40" onChange={(e) => onChange({ high: e.target.value })} />
        </Field>
      </div>
      <Field label="成本（新台幣，選填）">
        <Input inputMode="numeric" value={draft.cost} onChange={(e) => onChange({ cost: e.target.value })} />
      </Field>
      <Field label="依據（選填）">
        <Textarea
          rows={2}
          value={draft.rationale}
          maxLength={1000}
          placeholder="效果假設的來源，請勿填寫個資"
          onChange={(e) => onChange({ rationale: e.target.value })}
        />
      </Field>
      {onRemove && (
        <Button type="button" variant="ghost" size="sm" aria-label={`移除${title}`} onClick={onRemove}>
          移除
        </Button>
      )}
    </fieldset>
  )
}

function Field({ label: text, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-sm text-foreground">
      <span>{text}</span>
      {children}
    </label>
  )
}

// ---- 結果 ----

function ScenarioResult({ run }: { run: ScenarioRun }) {
  const out = run.output
  const est = out.estimate
  const names = measureNames(run)
  return (
    <>
      <Card>
        <Section id="scenario-result" title="模擬結果">
          <Badge tone="warning">{HYPOTHETICAL}</Badge>
          {est ? (
            <div className="mt-3">
              <p className="text-xs text-muted-foreground">估計件數（點估計）</p>
              <p className="font-mono text-4xl text-foreground">{formatCount(est.point)}</p>
            </div>
          ) : (
            <div className="mt-3 space-y-1">
              <p className="font-mono text-2xl text-warning">無法估計</p>
              <p className="text-sm text-foreground">所有措施的效果都未知，引擎無法估計；請至少為一項措施填寫效果假設。</p>
            </div>
          )}
          <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            <Fact term="情境" value={`基準 ${out.baseline} 件／${run.input.window_days} 天`} />
            {est && (
              <>
                <Fact term="效果全取上限／下限" value={`${formatCount(est.low)} – ${formatCount(est.high)} 件`} />
                <Fact term="抽樣 p05–p95" value={`${formatCount(est.p05)} – ${formatCount(est.p95)} 件`} />
                <Fact term="降低比例" value={formatPercent(est.reduction)} />
              </>
            )}
            <Fact
              term="總成本"
              value={
                out.total_cost === null
                  ? label(COST_TIER_LABELS, out.total_cost_tier)
                  : `NT$${out.total_cost.toLocaleString('zh-TW')}（${label(COST_TIER_LABELS, out.total_cost_tier)}）`
              }
            />
            <Fact term="重現" value={`種子 ${run.seed}、抽樣 ${out.samples} 次、引擎 ${run.engine_version}`} />
          </dl>
          {out.excluded_measure_ids.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-foreground">
              {out.excluded_measure_ids.map((id) => (
                <li key={id}>{`${names[id] ?? id}：效果未知，未納入計算`}</li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-muted-foreground">{`建立於 ${formatTime(run.created_at)}`}</p>
        </Section>
      </Card>
      {est && <Tornado run={run} point={est.point} />}
      <Explanation run={run} />
    </>
  )
}

/**
 * 龍捲風敏感度圖：一次只移動一項措施的效果（其他固定在中點），估計件數的變動範圍。
 * 只有一個數列，用強調色；每列都有文字數值，長條只是輔助（aria-hidden）。
 */
function Tornado({ run, point }: { run: ScenarioRun; point: number }) {
  const captionId = useId()
  const names = measureNames(run)
  const rows = run.output.measures
    .filter((m) => m.rank !== null && m.at_low !== null && m.at_high !== null)
    .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
  const values = rows.flatMap((m) => [m.at_low ?? point, m.at_high ?? point]).concat(point)
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const pos = (v: number) => `${((v - lo) / span) * 100}%`

  return (
    <Card>
      <figure aria-labelledby={captionId} className="min-w-0">
        <figcaption className="mb-3">
          <span id={captionId} className="block text-sm font-semibold text-foreground">
            敏感度（龍捲風圖）
          </span>
          <span className="mt-1 block text-xs text-muted-foreground">
            {`一次只移動一項措施的效果假設（其他固定在中點）時，估計件數的範圍；直線為點估計 ${formatCount(point)} 件。長條越長，結果越依賴這項假設。`}
          </span>
        </figcaption>
        <ol className="space-y-3">
          {rows.map((m) => {
            const low = m.at_high ?? point
            const high = m.at_low ?? point
            return (
              <li key={m.measure_id} className="grid gap-1 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)] sm:items-center sm:gap-3">
                <div className="min-w-0 text-sm">
                  <span className="break-words text-foreground">{names[m.measure_id] ?? m.measure_id}</span>
                  <span className="block font-mono text-xs text-muted-foreground">
                    {`${formatCount(low)} – ${formatCount(high)} 件 · 擺幅 ${formatCount(m.swing ?? 0)}`}
                  </span>
                </div>
                <div aria-hidden="true" className="relative h-6 rounded bg-muted">
                  <div
                    title={`效果取上限 ${formatCount(low)} 件；取下限 ${formatCount(high)} 件`}
                    className="absolute inset-y-1 min-w-1 rounded bg-accent"
                    style={{ left: pos(low), width: `calc(${pos(high)} - ${pos(low)})` }}
                  />
                  <div className="absolute inset-y-0 w-0.5 bg-foreground" style={{ left: pos(point) }} />
                </div>
              </li>
            )
          })}
        </ol>
        <p aria-hidden="true" className="mt-2 flex justify-between font-mono text-xs text-muted-foreground sm:ml-[calc(10rem+0.75rem)]">
          <span>{`${formatCount(lo)} 件`}</span>
          <span>{`${formatCount(hi)} 件`}</span>
        </p>
      </figure>
    </Card>
  )
}

function Explanation({ run }: { run: ScenarioRun }) {
  const ex = run.explanation
  const names = measureNames(run)
  const out = ex?.output ?? null
  return (
    <Card>
      <Section id="scenario-explanation" title="AI 解釋">
        {!ex && <Muted>效果全部未知，沒有呼叫 AI 解釋。</Muted>}
        {ex?.status === 'failed' && (
          <p className="text-sm text-warning">{`AI 解釋失敗，數值仍由引擎計算（${ex.error_type ?? '未知錯誤'}）。`}</p>
        )}
        {ex && ex.status !== 'failed' && !out && <Muted>{`AI 沒有提出解釋（${ex.abstain_reason ?? '證據不足'}）。`}</Muted>}
        {out && (
          <div className="space-y-3 text-sm">
            <p className="text-foreground">{out.summary}</p>
            {out.key_driver && (
              <p className="text-foreground">
                <span className="font-medium">{`最影響結果的假設：${names[out.key_driver] ?? out.key_driver}`}</span>
              </p>
            )}
            {out.assumptions.length > 0 && (
              <ul className="space-y-1">
                {out.assumptions.map((a) => (
                  <li key={a.measure_id}>
                    <span className="font-medium text-foreground">{`${names[a.measure_id] ?? a.measure_id}：`}</span>
                    <span className="text-foreground">{a.statement}</span>
                  </li>
                ))}
              </ul>
            )}
            {out.limitations.length > 0 && (
              <ul aria-label="限制" className="flex flex-wrap gap-2">
                {out.limitations.map((code) => (
                  <li key={code}>
                    <Badge tone="warning">{label(LIMITATION_LABELS, code)}</Badge>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-muted-foreground">{out.reason}</p>
            <p className="text-xs text-muted-foreground">{`scenario-simulator ${ex?.skill_version ?? ''}；模型只解釋假設，數字由引擎計算。`}</p>
          </div>
        )}
      </Section>
    </Card>
  )
}

function Fact({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="shrink-0 text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words text-foreground">{value}</dd>
    </div>
  )
}
