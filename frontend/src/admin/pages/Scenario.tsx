import { useEffect, useId, useState, type FormEvent, type ReactNode } from 'react'

import { Badge, Button, Card, Input, Select, Textarea } from '@design'

import {
  createScenario,
  evaluateImpact,
  getScenarioBaseline,
  getSeries,
  listImpactEvaluations,
  listScenarios,
  listSeries,
  type ImpactCause,
  type ImpactEvaluation,
  type SeriesMeasure,
  type CostTier,
  type ScenarioBaseline,
  type ScenarioLimitation,
  type ScenarioMeasureSpec,
  type ScenarioRequest,
  type ScenarioRun,
} from '../api'
import { formatPercent, formatTime, label } from '../format'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, Notice, PageHeader, Section } from '../ui'

const MAX_MEASURES = 10
const MAX_SEED = 2 ** 31 - 1
const HYPOTHETICAL = '假設情境，非成效預測'
const BASELINE_DELAY_MS = 300

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

type Mode = 'impact' | 'custom'

const MODES: { mode: Mode; label: string; description: string; note: string }[] = [
  {
    mode: 'impact',
    label: '依建議評估',
    description: '從系列分析的改善建議勾選要採用的，AI 估計件數可能少多少，再對照改善前後的樣子。',
    note: '改善百分比是 AI 依現況估計的區間，沒有校準依據，只用來比較不同選擇；不代表措施實際會有的成效，也不能當作決策依據。',
  },
  {
    mode: 'custom',
    label: '自訂措施',
    description: '件數 = 基準 × Π(1 − 措施效果)；效果是你填的假設區間，引擎依固定種子計算範圍與敏感度。',
    note: '結果完全取決於你填寫的效果假設，只用來比較不同假設下的情境；不代表措施實際會有的成效，也不能當作決策依據。',
  },
]

function seriesFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get('series')
}

/**
 * What-if 模擬（P7-T03，規格 F09）。兩個分頁：
 * - 依建議評估（P8-T15，ADR-0019）：勾選系列分析（S12）的改善建議，S14 估計改善百分比區間（AI 估計，
 *   非實證）與改善前後情境，件數由程式換算；從系列分析頁的「評估效益」進入（`?series=<id>`）。
 * - 自訂措施（P7-T03）：填寫基準件數與措施的效果假設，數值由引擎計算（P7-T02），S10 只解釋假設與
 *   限制；基準件數依時間窗、類別、地點自動帶入（P8-T09），可手改，手改值優先。
 * 結果一律標示「假設情境，非成效預測」。只在管理端。
 */
export function Scenario() {
  const [initialSeries] = useState(seriesFromUrl)
  const [mode, setMode] = useState<Mode>(initialSeries ? 'impact' : 'custom')
  const current = MODES.find((m) => m.mode === mode) ?? MODES[1]
  return (
    <div className="space-y-6">
      <PageHeader title="What-if 模擬" description={current.description} />
      <HypotheticalNote text={current.note} />
      <ModeTabs mode={mode} onChange={setMode} />
      <div role="tabpanel" id={`scenario-panel-${mode}`} aria-labelledby={`scenario-tab-${mode}`}>
        {mode === 'impact' ? <ImpactScenario initialSeries={initialSeries} /> : <CustomScenario />}
      </div>
    </div>
  )
}

function ModeTabs({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  return (
    <div role="tablist" aria-label="模擬方式" className="flex flex-wrap gap-2 border-b border-border">
      {MODES.map((m) => (
        <button
          key={m.mode}
          id={`scenario-tab-${m.mode}`}
          type="button"
          role="tab"
          aria-selected={m.mode === mode}
          aria-controls={`scenario-panel-${m.mode}`}
          onClick={() => onChange(m.mode)}
          className="-mb-px min-h-11 border-b-2 border-transparent px-3 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-selected:border-accent aria-selected:font-semibold aria-selected:text-foreground"
        >
          {m.label}
        </button>
      ))}
    </div>
  )
}

/** 自訂措施分頁（P7-T03）。 */
function CustomScenario() {
  const history = useLoad(listScenarios, 'scenarios')
  const [selected, setSelected] = useState<ScenarioRun | null>(null)

  function created(run: ScenarioRun) {
    setSelected(run)
    history.setData((list) => [run, ...(list ?? [])].slice(0, 50))
  }

  return (
    <div className="space-y-6">
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
            emptyText="還沒有模擬紀錄，跑一次試試看。"
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
function HypotheticalNote({ text }: { text: string }) {
  const id = useId()
  return (
    <div role="note" aria-labelledby={id} className="rounded-lg border border-warning/40 bg-warning-muted px-4 py-3">
      <p id={id} className="text-base font-semibold text-warning">
        {HYPOTHETICAL}
      </p>
      <p className="mt-1 text-sm text-foreground">{text}</p>
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

/** 自動基準的說明：依據的週數與件數，或沒有歷史資料、查詢失敗的提示。 */
function baselineHint(auto: ScenarioBaseline | null, failed: boolean, manual: boolean): string | null {
  if (failed) return '暫時算不出歷史基準，請手動填寫。'
  if (!auto) return null
  if (auto.baseline === null) return '沒有歷史資料可以推算，請手動填寫。'
  const basis = `依過去 ${auto.weeks} 週的 ${auto.observed} 件推算（${auto.history_start}～${auto.history_end}）`
  return manual ? `已手動修改；自動計算為 ${auto.baseline} 件，${basis}。` : `${basis}，可直接修改。`
}

/** 依時間窗、類別、地點查詢歷史基準（P8-T09）；輸入停下一小段時間才查，舊的回應不覆蓋新的。 */
function useAutoBaseline(windowDays: string, eventType: string, location: string) {
  const [auto, setAuto] = useState<ScenarioBaseline | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const days = Number(windowDays.trim())
    if (windowDays.trim() === '' || !Number.isInteger(days) || days < 1 || days > 730) return
    let current = true
    const timer = setTimeout(() => {
      getScenarioBaseline({ windowDays: days, eventType: eventType.trim(), location: location.trim() })
        .then((result) => {
          if (!current) return
          setAuto(result)
          setFailed(false)
        })
        .catch(() => {
          if (!current) return
          setAuto(null)
          setFailed(true)
        })
    }, BASELINE_DELAY_MS)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [windowDays, eventType, location])
  return { auto, failed }
}

function ScenarioForm({ onCreated }: { onCreated: (run: ScenarioRun) => void }) {
  const message = useErrorMessage()
  const [manualBaseline, setManualBaseline] = useState<string | null>(null)
  const [windowDays, setWindowDays] = useState('90')
  const [eventType, setEventType] = useState('')
  const [location, setLocation] = useState('')
  const [seed, setSeed] = useState('')
  const [measures, setMeasures] = useState<MeasureDraft[]>([emptyMeasure(0)])
  const [nextKey, setNextKey] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { auto, failed } = useAutoBaseline(windowDays, eventType, location)
  const autoValue = auto?.baseline ?? null
  const baseline = manualBaseline ?? (autoValue === null ? '' : String(autoValue))

  function update(key: number, change: Partial<MeasureDraft>) {
    setMeasures((list) => list.map((m) => (m.key === key ? { ...m, ...change } : m)))
  }

  function add() {
    setMeasures((list) => [...list, emptyMeasure(nextKey)])
    setNextKey((k) => k + 1)
  }

  function build(): ScenarioRequest {
    const window_days = parseNumber(windowDays, '期間要是 1–730 天的整數', { integer: true, min: 1, max: 730 })
    // 沒有手改時不送 baseline，由後端以同一規則計算並記錄依據（手改值優先）
    const body: ScenarioRequest =
      manualBaseline === null && autoValue !== null
        ? { window_days, measures: measures.map(toMeasure) }
        : { baseline: parseNumber(baseline, '基準件數要是 0 以上的數字'), window_days, measures: measures.map(toMeasure) }
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
          <Input inputMode="decimal" value={baseline} placeholder="例如 12" onChange={(e) => setManualBaseline(e.target.value)} />
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
      <BaselineHint
        text={baselineHint(auto, failed, manualBaseline !== null)}
        onReset={manualBaseline !== null && autoValue !== null ? () => setManualBaseline(null) : null}
      />
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

function BaselineHint({ text, onReset }: { text: string | null; onReset: (() => void) | null }) {
  if (!text) return null
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <p>{text}</p>
      {onReset && (
        <Button type="button" variant="ghost" size="sm" onClick={onReset}>
          改回自動計算
        </Button>
      )}
    </div>
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
            <Fact
              term="情境"
              value={`基準 ${out.baseline} 件／${run.input.window_days} 天${run.input.baseline_source === 'auto' ? '（依歷史自動計算）' : ''}`}
            />
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

// ---- 依建議評估（P8-T15，S14 impact-evaluator，ADR-0019） ----

const AI_ESTIMATE = 'AI 估計，非實證'
const IMPACT_FAILED = '這次評估不出來'

const AUDIENCE: Record<string, string> = { students: '同學', school: '學校', units: '相關單位' }
const KIND: Record<string, string> = { respond: '因應', investigate: '查證', prevent: '預防' }
const HORIZON: Record<string, string> = { short: '短期', mid: '中期', long: '長期' }

function code(index: number): string {
  return `m${index + 1}`
}

function codeIndex(measureId: string): number {
  return Number(measureId.slice(1)) - 1
}

function sortCodes(codes: Iterable<string>): string[] {
  return [...new Set(codes)].sort((a, b) => codeIndex(a) - codeIndex(b))
}

function ImpactScenario({ initialSeries }: { initialSeries: string | null }) {
  const [seriesId, setSeriesId] = useState<string | null>(initialSeries)
  return (
    <div className="space-y-6">
      <SeriesPicker value={seriesId} onChange={setSeriesId} />
      {seriesId ? <ImpactWorkspace key={seriesId} seriesId={seriesId} /> : (
        <Card>
          <Muted>先選一個系列，或從系列分析頁的「評估效益」進來。</Muted>
        </Card>
      )}
    </div>
  )
}

function SeriesPicker({ value, onChange }: { value: string | null; onChange: (id: string | null) => void }) {
  const list = useLoad(() => listSeries(), 'impact-series')
  const id = useId()
  return (
    <Card>
      <label htmlFor={id} className="block text-sm font-medium text-foreground">
        系列
      </label>
      <Select
        id={id}
        className="mt-1"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        disabled={list.loading && !list.data}
      >
        <option value="">請選擇系列</option>
        {value && !list.data?.some((s) => s.id === value) && <option value={value}>目前的系列</option>}
        {list.data?.map((s) => (
          <option key={s.id} value={s.id}>{`${s.title}（${s.event_count} 件）`}</option>
        ))}
      </Select>
      {list.error && <ErrorText>{list.error}</ErrorText>}
    </Card>
  )
}

function ImpactWorkspace({ seriesId }: { seriesId: string }) {
  const series = useLoad(() => getSeries(seriesId), `impact-detail-${seriesId}`)
  const history = useLoad(() => listImpactEvaluations(seriesId), `impact-history-${seriesId}`)
  const message = useErrorMessage()
  const [checked, setChecked] = useState<string[]>([])
  const [shown, setShown] = useState<ImpactEvaluation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const analysis = series.data?.analysis ?? null

  async function evaluate(codes: string[]) {
    if (!analysis || codes.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const result = await evaluateImpact({ series_id: seriesId, measure_ids: codes, analysis_id: analysis.id })
      setShown(result)
      history.setData((list) => [result, ...(list ?? []).filter((e) => e.id !== result.id)].slice(0, 50))
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    void evaluate(checked)
  }

  function addSuggestion(measureId: string) {
    const next = sortCodes([...checked, measureId])
    setChecked(next)
    void evaluate(next)
  }

  if (!series.data) {
    return <LoadState loading={series.loading} error={series.error} empty={false} emptyText="" />
  }
  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <Card className="min-w-0">
          {analysis === null || analysis.measures.length === 0 ? (
            <Muted>這個系列還沒有可用的改善建議，等系列分析完成後再來評估。</Muted>
          ) : (
            <form aria-label="選擇改善建議" onSubmit={submit} className="space-y-4">
              <fieldset className="space-y-2">
                <legend className="text-sm font-semibold text-foreground">{`${series.data.title}：要採用哪些建議？`}</legend>
                <p className="text-xs text-muted-foreground">只要勾選，不用填數字；AI 會依系列現況估計效果。</p>
                {analysis.measures.map((m, i) => (
                  <label
                    key={`${i}-${m.action}`}
                    className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 accent-accent"
                      checked={checked.includes(code(i))}
                      onChange={(e) =>
                        setChecked((list) =>
                          e.target.checked ? sortCodes([...list, code(i)]) : list.filter((c) => c !== code(i)),
                        )
                      }
                    />
                    <span className="min-w-0 space-y-1">
                      <span className="block break-words text-foreground">{m.action}</span>
                      <span className="flex flex-wrap gap-1 text-xs text-muted-foreground">
                        <Badge tone="neutral">{label(AUDIENCE, m.audience)}</Badge>
                        <span>{`${label(KIND, m.kind)} · ${label(HORIZON, m.horizon)}`}</span>
                        {m.tracked_action_id && <Badge tone="info">已在追蹤</Badge>}
                      </span>
                    </span>
                  </label>
                ))}
              </fieldset>
              {error && <ErrorText>{error}</ErrorText>}
              <Button type="submit" disabled={busy || checked.length === 0}>
                {busy ? '評估中…' : '評估效益'}
              </Button>
              {checked.length === 0 && <Muted>至少勾選一項建議。</Muted>}
            </form>
          )}
        </Card>
        <div className="min-w-0 space-y-6">
          {shown ? (
            <ImpactResultCard
              key={shown.id}
              evaluation={shown}
              measures={analysis?.measures ?? []}
              busy={busy}
              onAdd={shown.stale ? null : addSuggestion}
            />
          ) : (
            <Card>
              <Muted>勾選建議後按「評估效益」，或從下方「這個系列的評估」開啟。</Muted>
            </Card>
          )}
        </div>
      </div>
      <Card>
        <Section id="impact-history" title="這個系列的評估">
          <LoadState
            loading={history.loading && !history.data}
            error={history.error}
            empty={history.data?.length === 0}
            emptyText="還沒有評估紀錄，勾選建議試試看。"
          />
          {history.data && history.data.length > 0 && (
            <ul className="divide-y divide-border">
              {history.data.map((e) => (
                <li key={e.id}>
                  <button
                    type="button"
                    aria-pressed={e.id === shown?.id}
                    onClick={() => setShown(e)}
                    className="flex min-h-11 w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-2 py-2 text-left text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:bg-muted"
                  >
                    <span className="font-mono text-xs text-muted-foreground">{formatTime(e.created_at)}</span>
                    <span className="font-mono text-xs text-foreground">{e.selected.join('、')}</span>
                    {e.improvement ? (
                      <span className="font-mono text-xs text-foreground">{`${e.improvement.low}–${e.improvement.high}%`}</span>
                    ) : (
                      <Badge tone="warning">{IMPACT_FAILED}</Badge>
                    )}
                    {e.stale && <Badge tone="neutral">舊分析</Badge>}
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

function ImpactResultCard({ evaluation, measures, busy, onAdd }: {
  evaluation: ImpactEvaluation
  measures: SeriesMeasure[]
  busy: boolean
  onAdd: ((measureId: string) => void) | null
}) {
  const titleId = useId()
  const result = evaluation.result
  const counts = evaluation.counts
  const actions = (ids: string[]) => ids.map((m) => measures[codeIndex(m)]?.action ?? m)
  return (
    <section aria-labelledby={titleId}>
      <Card className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id={titleId} className="text-lg font-semibold text-foreground">評估結果</h2>
          <span className="font-mono text-xs text-muted-foreground">{formatTime(evaluation.created_at)}</span>
        </div>
        {evaluation.stale && <Notice>系列分析已經更新，這是依舊分析做的評估；想看最新的情況，請重新勾選再評估。</Notice>}
        <p className="text-sm text-muted-foreground">{`採用：${actions(evaluation.selected).join('、')}`}</p>
        {evaluation.status !== 'ok' || !evaluation.improvement || !result ? (
          <div className="space-y-1">
            <p className="text-base font-semibold text-warning">{IMPACT_FAILED}</p>
            <Muted>
              {evaluation.status === 'abstained'
                ? '系列的資料不足以判斷這些建議的效果。'
                : '這次 AI 的回答沒有通過檢查，可以稍後再試一次。'}
            </Muted>
          </div>
        ) : (
          <>
            <div>
              <p className="text-sm text-muted-foreground">件數可能減少</p>
              <p className="font-mono text-4xl font-bold text-foreground">
                {`${evaluation.improvement.low}–${evaluation.improvement.high}%`}
              </p>
              <Badge tone="warning">{AI_ESTIMATE}</Badge>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Scene
                title="改善前"
                text={result.before}
                count={counts.shown && counts.baseline !== null ? `約 ${formatCount(counts.baseline)} 件／${counts.window_days} 天` : null}
              />
              <Scene
                title="改善後"
                text={result.after}
                count={
                  counts.shown && counts.after_low !== null && counts.after_high !== null
                    ? `約 ${formatCount(counts.after_low)}–${formatCount(counts.after_high)} 件／${counts.window_days} 天`
                    : null
                }
              />
            </div>
            {!counts.shown && <Muted>這個系列的歷史件數還太少，不換算件數。</Muted>}
            <CauseList title="有處理到的原因" causes={result.covered_causes} empty="選的建議沒有直接處理任何原因。" />
            <CauseList title="還沒處理到的原因" causes={result.uncovered_causes} empty="原因都有處理到。" />
            {result.overlaps.length > 0 && (
              <Section id="impact-overlaps" title="效果可能重疊">
                <ul className="list-disc space-y-1 pl-5 text-sm text-foreground">
                  {result.overlaps.map((o) => (
                    <li key={o.measure_ids.join(',')}>{`${actions(o.measure_ids).join('、')}：${o.note}`}</li>
                  ))}
                </ul>
              </Section>
            )}
            <Section id="impact-risks" title="風險與限制">
              <ul className="list-disc space-y-1 pl-5 text-sm text-foreground">
                {result.risks.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </Section>
            {result.suggestion && (
              <Section id="impact-suggestion" title="可以考慮補上">
                <p className="text-sm text-foreground">{result.suggestion.action ?? result.suggestion.measure_id}</p>
                {result.suggestion.reason && <Muted>{result.suggestion.reason}</Muted>}
                {onAdd && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    disabled={busy}
                    onClick={() => result.suggestion && onAdd(result.suggestion.measure_id)}
                  >
                    加入再評估
                  </Button>
                )}
              </Section>
            )}
            {result.reason && <Muted>{`估計方式：${result.reason}`}</Muted>}
          </>
        )}
      </Card>
    </section>
  )
}

function Scene({ title, text, count }: { title: string; text: string | null; count: string | null }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {count && <p className="mt-1 font-mono text-sm text-foreground">{count}</p>}
      <p className="mt-2 text-sm text-foreground">{text}</p>
    </div>
  )
}

function CauseList({ title, causes, empty }: { title: string; causes: ImpactCause[]; empty: string }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {causes.length === 0 ? (
        <Muted>{empty}</Muted>
      ) : (
        <ul className="mt-1 space-y-1 text-sm">
          {causes.map((c) => (
            <li key={c.cause_id} className="flex flex-wrap items-center gap-2">
              <span className="text-foreground">{c.description}</span>
              <Badge tone={c.decision === 'confirmed' ? 'success' : 'warning'}>{c.label}</Badge>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
