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
  Trade,
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

/**
 * The fills one arm has booked so far, rebuilt from the same observation fields the recorder
 * writes into a finished recording's trade list. `equity_after` is the ledger's own marked
 * equity for that bar and `reason` is the last explanation step the decision itself recorded:
 * both are carried across, not recomputed, so a streaming table and the finished one agree.
 */
function bookedTrades(observations: Observation[], armId: string): Trade[] {
  const trades: Trade[] = []
  for (const observation of observations) {
    const entry = observation.arms?.[armId]
    const plan = entry?.execution.plan
    const fill = entry?.execution.fill
    if (!entry || entry.execution.status !== 'FILLED' || !plan || !fill) continue
    const steps = entry.decision?.explanation?.steps ?? []
    trades.push({
      i: observation.i,
      t: observation.t,
      side: plan.side,
      product: plan.product,
      base_size: fill.base,
      price: fill.price,
      quote_size: fill.quote,
      fee: fill.fee,
      reason: steps[steps.length - 1] ?? '',
      equity_after: entry.portfolio.equity,
      memory_changed_edges: entry.signal?.memory?.changed_edges ?? null,
    })
  }
  return trades
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
    const exposure = points.filter((entry) =>
      Object.values(entry.portfolio.positions).some((size) => Number(size) > 0),
    ).length
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
      exposure_bars: exposure,
      trades: bookedTrades(observations, arm.id),
      deployment: {
        bars: observations.length,
        bars_holding: exposure,
        holding_fraction: observations.length ? exposure / observations.length : 0,
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

/** The newest `live_progress` report: what the exchange has closed, and how far behind it ran. */
export interface LiveProgress {
  /** Bars the exchange has already closed that the growing season can see. */
  available: number
  /** Seconds the newest decision ran behind the close it was deciding. */
  lagSeconds: number
}

export interface LiveRun {
  runId: string
  engine: 'neural' | 'procedural'
  label: string
  seasonDescribe: string
  seasonProvenance: Record<string, unknown>
  /** Bars the season announced up front. Empty for a live session, which has none yet. */
  seasonBars: { t: number; mid: number; bid: string; ask: string }[]
  arms: ArmMeta[]
  observations: Observation[]
  /** Wall seconds each decided bar took, for the progress estimate. */
  barDurations: number[]
  /** When the session started, epoch seconds; 0 when unknown. */
  startedWall: number
  /** Session wall seconds at the newest bar, so a live view can state its own age. */
  elapsedSeconds: number
  /** The market interval between bars, as the hub announced it. */
  barSeconds: number
  /** Bars the hub announced for the whole run; 0 when it announced none, as live does. */
  announcedBars: number
  progress: LiveProgress | null
  repeat: number
  repeats: number
  finished: boolean
  rules: Record<string, string | number>
  summary?: Recording['summary']
}

/**
 * The price path the session has actually traded.
 *
 * A fixed season arrives with its bars and those are used exactly as recorded. A live season
 * does not: the hub announces `bars: []` and grows one bar at a time, so the series is
 * rebuilt from the quote each observation recorded — the same bid, ask and mid the flies
 * were shown, in the order the exchange closed them. Nothing is interpolated or forecast,
 * and the path only ever gains a point at its end.
 */
export function marketBars(live: LiveRun): LiveRun['seasonBars'] {
  if (live.seasonBars.length) return live.seasonBars
  return live.observations.map((observation) => ({
    t: observation.t,
    mid: Number(observation.market.mid),
    bid: observation.market.bid,
    ask: observation.market.ask,
  }))
}

/**
 * The hub's season description with its completed-bar count brought up to date.
 *
 * A live season is described once, when it opens, so the count inside that string is the one
 * at that moment — "live, 0 completed bars" — and would go stale as the session trades.
 * `live_progress` reports the same measurement (bars the exchange has closed), so the string
 * is restated rather than left contradicting the panels beside it. A session that has not
 * reported yet, and every fixed season, is returned untouched.
 */
export function seasonDescription(live: LiveRun): string {
  if (!live.progress) return live.seasonDescribe
  return live.seasonDescribe.replace(
    / · live, \d+ completed bars$/,
    ` · live, ${live.progress.available} completed bars`,
  )
}

/** A live run dressed as a recording so every panel takes one input type. */
export function asRecording(live: LiveRun): Recording {
  const arms = live.arms
  const rules = live.rules
  const capital = arms[0] ? Number(arms[0].starting_capital) : 100
  const fee = Number(rules.paper_fee ?? 0.006)
  // The run's length is the bars that exist right now: a live season announced none, and a
  // later bar only ever appends. Everything drawn from it therefore grows rather than resets.
  const bars = Math.max(live.announcedBars, live.observations.length, 1)
  const seasonBars = marketBars(live)
  const summaries =
    live.summary?.arms ?? summarise(arms, live.observations)
  const benchmark = live.summary?.benchmarks ?? benchmarks(seasonBars, capital, fee)
  // Both are measured, not assumed: the session's own wall time and the mean decision time
  // the hub reported bar by bar. A live session that has not decided anything yet reports 0.
  const secondsPerBar = live.barDurations.length
    ? live.barDurations.reduce((sum, value) => sum + value, 0) / live.barDurations.length
    : 0
  const on = summaries[arms[0]?.id ?? 'gordon']
  const off = summaries[arms[1]?.id ?? 'warren']
  const delta = (on?.final_equity ?? capital) - (off?.final_equity ?? capital)
  return {
    schema: 'flyvsly.recording/v1',
    run: {
      id: live.runId,
      created: seasonBars[0]?.t ?? 0,
      label: live.label,
      engine: live.engine,
      repeat: live.repeat,
      bars,
      bar_seconds: live.barSeconds,
      season: seasonDescription(live),
      rules,
      starting_conditions: {
        fairness: {
          differing_fields: ['learning'],
          identical_fields: Object.keys(rules),
          identical_fields_sha256: '',
        },
      },
      hardware: {},
      duration_seconds: live.elapsedSeconds,
      seconds_per_bar_mean: secondsPerBar,
      truncated: false,
      wall_mode: 'live stream, one completed bar per observation',
      inputs_identical_every_bar: live.observations.every(
        (observation) => observation.same_neural_input_both_arms !== false,
      ),
    },
    arms,
    season: {
      describe: seasonDescription(live),
      provenance: live.seasonProvenance,
      bars: seasonBars,
    },
    observations: live.observations,
    summary: {
      bars,
      initial_capital: String(capital),
      duration_seconds: live.elapsedSeconds,
      seconds_per_bar_mean: secondsPerBar,
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
