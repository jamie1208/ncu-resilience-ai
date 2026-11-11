import type { ImpactEvaluation, ScenarioRun, SeriesDetail, SeriesSummary } from '../admin/api'

/** 三項措施（一項效果未知）的模擬：數值與 P7-T02 引擎一致（baseline 12，m1 0.2–0.4，m2 0.1–0.2）。 */
export const SCENARIO: ScenarioRun = {
  id: 'sc-1',
  event_id: null,
  input: {
    baseline: 12,
    window_days: 90,
    event_type: 'security',
    location: '工程二館',
    seed: 42,
    measures: [
      { measure_id: 'm1', name: '加裝路燈', effect_low: 0.2, effect_high: 0.4, cost: 300000, rationale: '他校案例' },
      { measure_id: 'm2', name: '增加夜間巡邏', effect_low: 0.1, effect_high: 0.2, cost: 20000, rationale: null },
      { measure_id: 'm3', name: '張貼告示', effect_low: null, effect_high: null, cost: null, rationale: null },
    ],
  },
  output: {
    engine_version: '1.0.0',
    seed: 42,
    samples: 2000,
    status: 'estimated',
    baseline: 12,
    estimate: { point: 7.14, low: 5.76, high: 8.64, p05: 6.31, p50: 7.13, p95: 8.02, reduction: 0.405 },
    measures: [
      { measure_id: 'm1', included: true, cost_tier: 'medium', at_low: 8.16, at_high: 6.12, swing: 2.04, rank: 1 },
      { measure_id: 'm2', included: true, cost_tier: 'low', at_low: 7.56, at_high: 6.72, swing: 0.84, rank: 2 },
      { measure_id: 'm3', included: false, cost_tier: 'unknown', at_low: null, at_high: null, swing: null, rank: null },
    ],
    excluded_measure_ids: ['m3'],
    total_cost: null,
    total_cost_tier: 'unknown',
  },
  engine_version: '1.0.0',
  seed: 42,
  explanation: {
    ai_run_id: 'run-1',
    status: 'succeeded',
    output: {
      status: 'ok',
      summary: '假設加裝路燈與增加巡邏，90 天內件數可能從 12 件降到約 7.14 件；這是假設情境，不是成效預測。',
      assumptions: [
        { measure_id: 'm1', statement: '假設路燈可降低 20%–40%，依據為他校案例，尚未在本校驗證。', evidence_ids: ['m1'] },
        { measure_id: 'm2', statement: '假設巡邏可降低 10%–20%，未提供假設依據。', evidence_ids: ['m2'] },
      ],
      key_driver: 'm1',
      limitations: ['hypothetical_not_prediction', 'independence_assumed', 'unknown_effects', 'cost_unknown', 'unsupported_assumption'],
      evidence_ids: ['m1', 'm2'],
      reason: '結果完全取決於輸入的效果假設，請當作討論用的假設情境。',
      abstain_reason: null,
    },
    abstain_reason: null,
    error_type: null,
    skill_version: '1.0.0',
  },
  created_by: 'admin:u-1',
  created_at: '2026-11-04T02:00:00Z',
}

/** 效果全部未知：無法估計，不呼叫 S10。 */
export const SCENARIO_NOT_ESTIMABLE: ScenarioRun = {
  ...SCENARIO,
  id: 'sc-2',
  input: { ...SCENARIO.input, measures: [SCENARIO.input.measures[2]] },
  output: {
    ...SCENARIO.output,
    status: 'not_estimable',
    estimate: null,
    measures: [SCENARIO.output.measures[2]],
    excluded_measure_ids: ['m3'],
  },
  explanation: null,
  created_at: '2026-11-03T02:00:00Z',
}

/** S10 失敗：引擎結果仍保存。 */
export const SCENARIO_EXPLAIN_FAILED: ScenarioRun = {
  ...SCENARIO,
  id: 'sc-3',
  explanation: {
    ai_run_id: 'run-3',
    status: 'failed',
    output: null,
    abstain_reason: null,
    error_type: 'provider_error',
    skill_version: '1.0.0',
  },
}

// ---- 依建議評估（P8-T15） ----

export const IMPACT_SERIES_SUMMARY: SeriesSummary = {
  id: 'ser-1',
  category: 'traffic',
  title: '停放機車遭碰撞',
  title_source: 'ai',
  event_count: 6,
  latest_joined_at: '2026-11-02T02:00:00+00:00',
  created_at: '2026-09-08T02:00:00+00:00',
}

/** 系列：兩個推測原因（停車格太靠近車道、照明不足）與三項建議。 */
export const IMPACT_SERIES: SeriesDetail = {
  ...IMPACT_SERIES_SUMMARY,
  events: [],
  removals: [],
  analysis: {
    id: 'a-1',
    status: 'ok',
    title: '停放機車遭碰撞',
    summary: '這個系列持續追蹤停放機車被碰撞的狀況。',
    bursts: [],
    location_groups: [],
    pattern_status: 'abstained',
    pattern_abstain_reason: 'no_pattern',
    patterns: [],
    causes: [
      { cause_id: 'c1', description: '可能是停車格太靠近車道。', label: '推測，尚未查證', to_verify: null,
        event_ids: [], event_count: 4, total: 6, quotes: [], mentions_party: false, decision: 'hypothesized' },
      { cause_id: 'c2', description: '疑似夜間照明不足。', label: '推測，尚未查證', to_verify: null,
        event_ids: [], event_count: 2, total: 6, quotes: [], mentions_party: false, decision: 'hypothesized' },
    ],
    measures: [
      { action: '把停車格往內移，和車道拉開距離。', audience: 'school', horizon: 'mid', priority: 'high',
        kind: 'prevent', cause_id: 'c1', responsible_unit: '總務處', tracked_action_id: null },
      { action: '查看停車區與車道的距離是否足夠。', audience: 'school', horizon: 'short', priority: 'medium',
        kind: 'investigate', cause_id: 'c1', responsible_unit: '總務處', tracked_action_id: null },
      { action: '在路段加裝路燈。', audience: 'units', horizon: 'mid', priority: 'medium',
        kind: 'prevent', cause_id: 'c2', responsible_unit: '總務處', tracked_action_id: null },
    ],
    dropped: 0,
    created_at: '2026-11-10T04:00:00+00:00',
  },
  latest_attempt: { status: 'ok', abstain_reason: null, created_at: '2026-11-10T04:00:00+00:00' },
  actions: [],
  legacy_cause_decisions: [],
}

const C1 = { cause_id: 'c1', description: '可能是停車格太靠近車道。', decision: 'hypothesized' as const, label: '推測，尚未查證' }
const C2 = { cause_id: 'c2', description: '疑似夜間照明不足。', decision: 'hypothesized' as const, label: '推測，尚未查證' }

/** 選 m1、m2：20–40%，基準 15 件／90 天 → 程式換算 9–12 件。 */
export const IMPACT_OK: ImpactEvaluation = {
  id: 'imp-1',
  series_id: 'ser-1',
  analysis_id: 'a-1',
  selected: ['m1', 'm2'],
  status: 'ok',
  abstain_reason: null,
  improvement: { low: 20, high: 40 },
  cap: 80,
  result: {
    before: '工程五館外的機車晚上常被擦撞，可能跟停車格太靠近車道有關。',
    after: '停車格往內移之後，如果原因真的是車道太窄，擦撞可望少一些。',
    reason: '所選建議處理主要的推測原因，但需要施工時間。',
    covered_causes: [C1],
    uncovered_causes: [C2],
    overlaps: [{ measure_ids: ['m1', 'm2'], note: '兩項都處理停車格與車道的距離。' }],
    risks: ['原因還是推測，效果可能比估計低。'],
    suggestion: { measure_id: 'm3', action: '在路段加裝路燈。', reason: '照明不足沒有被選的建議處理到。' },
    evidence_ids: ['E1', 'c1', 'm1'],
  },
  counts: {
    baseline: 15,
    observed: 10,
    history_weeks: 6,
    window_days: 90,
    category_baseline: 30,
    min_observed: 5,
    shown: true,
    after_low: 9,
    after_high: 12,
  },
  stale: false,
  reused: false,
  ai_run_id: 'run-1',
  created_by: 'jamie',
  created_at: '2026-11-11T02:00:00+00:00',
}

/** 加入補強建議 m3 後再評估：兩個原因都處理到。 */
export const IMPACT_ALL: ImpactEvaluation = {
  ...IMPACT_OK,
  id: 'imp-2',
  selected: ['m1', 'm2', 'm3'],
  improvement: { low: 30, high: 55 },
  result: {
    ...IMPACT_OK.result!,
    covered_causes: [C1, C2],
    uncovered_causes: [],
    suggestion: null,
  },
  counts: { ...IMPACT_OK.counts, after_low: 6.8, after_high: 10.5 },
}

/** 輸出重試後仍未通過驗證。 */
export const IMPACT_INVALID: ImpactEvaluation = {
  ...IMPACT_OK,
  id: 'imp-3',
  selected: ['m2'],
  status: 'invalid',
  abstain_reason: 'invalid_output',
  improvement: null,
  result: null,
  counts: { ...IMPACT_OK.counts, after_low: null, after_high: null },
}
