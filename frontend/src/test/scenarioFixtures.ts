import type { ScenarioRun } from '../admin/api'

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
