/**
 * Live-run derivations.
 *
 * While a run is streaming there is no summary yet, so the scoreboard and the benchmark are
 * computed here from the same recorded fields the finished run would contain: bar closes,
 * fills, fees and marked-to-bid equity. This is arithmetic on recorded telemetry — no value
 * is invented, and the finished recording replaces every number here when it lands.
 */

import type {
  ArmMeta,
  ArmObservation,
  ArmSummary,
  Benchmark,
  Observation,
  Recording,
} from './types'

const BASE_INCREMENT = 1e-8

function floorTo(value: number, step: number): number {
  return Math.floor(value / step) * step
}

/** Buy & hold under the same fee as the flies: one unrestricted fill at the first ask. */
export function buyAndHold(bars: { bid: string; ask: string }[], capital: number, fee: number): number[] {
  if (!bars.length) return [capital]
  const ask = Number(bars[0].ask)
  const size = floorTo(capital / (1 + fee) / ask, BASE_INCREMENT)
  const cost = Math.round(size * ask * 1e8) / 1e8
  const paid = Math.round(cost * fee * 1e8) / 1e8
  const cash = capital - cost - paid
  return bars.map((bar) => cash + size * Number(bar.bid))
}

export function summarise(arms: ArmMeta[], observations: Observation[]): Record<string, ArmSummary> {
  const out: Record<string, ArmSummary> = {}
  for (const arm of arms) {
    const points = observations
      .map((observation) => observation.arms?.[arm.id])
      .filter((entry): entry is ArmObservation => Boolean(entry))
    const curve = points.map((entry) => Number(entry.portfolio.equity))
    const initial = Number(arm.starting_capital)
    const first = curve[0] ?? initial
    let peak = first
    let drawdown = 0
    for (const value of curve) {
      peak = Math.max(peak, value)
      if (peak > 0) drawdown = Math.max(drawdown, (peak - value) / peak)
    }
    const last = points[points.length - 1]
    out[arm.id] = {
      name: arm.name,
      final_equity: curve[curve.length - 1] ?? initial,
      return_pct: initial ? ((curve[curve.length - 1] ?? initial) / initial - 1) * 100 : 0,
      max_drawdown_pct: drawdown * 100,
      path_volatility: 0,
      curve,
      fills: last?.portfolio.fills ?? 0,
      vetoes: last?.portfolio.vetoes ?? 0,
      blocked_bars: points.filter((entry) => entry.execution.status === 'BLOCKED').length,
      fees_paid: last?.portfolio.fees_paid ?? '0',
      halted: last?.portfolio.halted ?? null,
      exposure_bars: points.filter(
        (entry) => Object.values(entry.portfolio.positions).some((size) => Number(size) > 0),
      ).length,
      trades: [],
      deployment: {
        bars: observations.length,
        bars_holding: 0,
        holding_fraction: 0,
        order_limit_usdc: '10',
        daily_order_limit: 24,
        cooldown_seconds: 60,
      },
      final_memory: last?.signal?.memory ?? null,
    }
  }
  return out
}

export function benchmarks(
  bars: { bid: string; ask: string }[],
  capital: number,
  fee: number,
): { buy_and_hold: Benchmark; cash: Benchmark } {
  return {
    buy_and_hold: {
      id: 'buy_and_hold',
      label: 'Buy & hold',
      detail: 'Computed live from the same bars and fee; the finished recording replaces this.',
      initial_capital: String(capital),
      curve: buyAndHold(bars, capital, fee),
      fills: 1,
    },
    cash: {
      id: 'cash',
      label: 'Cash',
      detail: 'Never trades.',
      initial_capital: String(capital),
      curve: bars.map(() => capital),
      fills: 0,
    },
  }
}

export interface LiveRun {
  runId: string
  engine: 'neural' | 'procedural'
  label: string
  seasonDescribe: string
  seasonProvenance: Record<string, unknown>
  seasonBars: { t: number; mid: number; bid: string; ask: string }[]
  arms: ArmMeta[]
  observations: Observation[]
  bars: number
  repeat: number
  repeats: number
  finished: boolean
  rules: Record<string, string | number>
  summary?: Recording['summary']
}

/** A live run dressed as a recording so every panel takes one input type. */
export function asRecording(live: LiveRun): Recording {
  const arms = live.arms
  const rules = live.rules
  const capital = arms[0] ? Number(arms[0].starting_capital) : 100
  const fee = Number(rules.paper_fee ?? 0.006)
  const summaries =
    live.summary?.arms ?? summarise(arms, live.observations)
  const benchmark = live.summary?.benchmarks ?? benchmarks(live.seasonBars, capital, fee)
  const on = summaries[arms[0]?.id ?? 'gordon']
  const off = summaries[arms[1]?.id ?? 'warren']
  const delta = (on?.final_equity ?? capital) - (off?.final_equity ?? capital)
  return {
    schema: 'flyvsly.recording/v1',
    run: {
      id: live.runId,
      created: live.seasonBars[0]?.t ?? 0,
      label: live.label,
      engine: live.engine,
      repeat: live.repeat,
      bars: Math.max(live.bars, 1),
      bar_seconds: 60,
      season: live.seasonDescribe,
      rules,
      starting_conditions: {
        fairness: {
          differing_fields: ['learning'],
          identical_fields: Object.keys(rules),
          identical_fields_sha256: '',
        },
      },
      hardware: {},
      duration_seconds: 0,
      seconds_per_bar_mean: 0,
      truncated: false,
      wall_mode: 'live stream, one completed bar per observation',
      inputs_identical_every_bar: live.observations.every(
        (observation) => observation.same_neural_input_both_arms !== false,
      ),
    },
    arms,
    season: {
      describe: live.seasonDescribe,
      provenance: live.seasonProvenance,
      bars: live.seasonBars,
    },
    observations: live.observations,
    summary: {
      bars: Math.max(live.bars, 1),
      initial_capital: String(capital),
      duration_seconds: 0,
      seconds_per_bar_mean: 0,
      truncated: false,
      arms: summaries,
      benchmarks: benchmark,
      comparison: {
        memory_on: arms[0]?.id ?? 'gordon',
        memory_off: arms[1]?.id ?? 'warren',
        equity_delta_usdc: delta,
        return_delta_pct: (on?.return_pct ?? 0) - (off?.return_pct ?? 0),
        leader: delta > 0 ? arms[0]?.id : delta < 0 ? arms[1]?.id : 'tie',
        single_season_note: live.finished
          ? 'One season cannot distinguish a consistent result from one lucky path.'
          : 'Streaming now: the running figures are provisional and no conclusion is possible from a partial season.',
      },
    },
    disclaimers: [
      'Paper trading only. This project places no real orders and holds no account credentials.',
      'Engineered reinforcement is not pain, pleasure or consciousness.',
      'The DNp20 decoder is a fixed engineered interface, not a discovered buy/sell neuron.',
      'Memory updates are not assumed to help.',
      'Rivalry commentary is entertainment, not analysis.',
      'Buy & hold faces no order-size, cooldown or daily-order limits; the flies do.',
    ],
  }
}
