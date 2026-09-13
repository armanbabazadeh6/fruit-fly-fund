/**
 * The recording contract, mirrored from `flyvsly/telemetry.py` and `flyvsly/arena.py`.
 *
 * Every number rendered in this app comes from one of these fields. Nothing is derived
 * that could be mistaken for a measurement, and `signal_source` decides how a panel is
 * allowed to describe itself: only `'neural'` may be called spikes or neural activity.
 */

export type Side = 'BUY' | 'SELL' | 'HOLD' | 'BLOCKED'

/** How a fly is posed, derived from its recorded telemetry (see `lib/mood.ts`). */
export type FlyMood = 'idle' | 'buy' | 'sell' | 'veto' | 'blocked' | 'halted'
export type SignalSource = 'neural' | 'procedural'
export type StimulusKind = 'none' | 'reward' | 'aversive'
export type ExecutionStatus = 'FILLED' | 'VETO' | 'HOLD' | 'BLOCKED'

export interface MemoryTelemetry {
  enabled: boolean
  simulated?: boolean
  model?: string
  plastic_edges?: number
  changed_edges: number | null
  mean_efficacy?: number
  minimum_efficacy?: number
  sha256?: string
  bias?: number
  note?: string
}

export interface NeuralSignal {
  signal_source: 'neural'
  label: string
  side: Side
  seconds: number
  left_hz: number
  right_hz: number
  difference_hz: number
  gate_spikes: number
  stimulus: StimulusKind
  stimulus_ms: number
  reward_spikes: number
  aversive_spikes: number
  KC_spikes: number
  total_spikes: number
  brain_ms: number
  compute_seconds: number
  spike_sha256: string
  input_sha256: string
  memory: MemoryTelemetry
  /** Spike counts of the recorded population, in the order run.population.ids describes. */
  population?: number[]
  /** Present only when a fitted readout decided this bar. */
  readout_score?: number
  readout_margin?: number
  /** The fixed rule's answer on the same spikes, kept as the audit trail. */
  decoder_side?: Side
}

export interface ProceduralSignal {
  signal_source: 'procedural'
  simulated: true
  label: string
  side: Side
  score: number
  threshold: number
  momentum: number
  volatility: number
  lookback: number
  simulated_memory_delta: number | null
  base_score: number
  compute_seconds: number
  stimulus: StimulusKind
  memory: MemoryTelemetry
}

export type Signal = NeuralSignal | ProceduralSignal

export function isNeural(signal: Signal | null | undefined): signal is NeuralSignal {
  return !!signal && signal.signal_source === 'neural'
}

export interface Explanation {
  kind: string
  rule: string
  measured: Record<string, number>
  steps: string[]
  result: string
  engineered_interface?: boolean
  note?: string
}

export interface OrderPlan {
  product: string
  side: 'BUY' | 'SELL'
  base_size: string
  limit_price: string
  fee_ceiling: string
  observed_bid: string
  observed_ask: string
  quote_timestamp: number
  client_order_id: string
  order_type: string
}

export interface Fill {
  mode: string
  status: string
  base: string
  quote: string
  fee: string
  price: string
}

export interface Execution {
  status: ExecutionStatus
  reason?: string
  plan?: OrderPlan | null
  fill?: Fill | null
  slippage_limit?: string
  fee_rate?: string
  explanation?: string[]
}

export interface Portfolio {
  cash: string
  positions: Record<string, string>
  equity: string
  return_pct: number
  fees_paid: string
  fills: number
  vetoes: number
  halted: string | null
}

export interface ArmObservation {
  signal: Signal | null
  decision: { side: Side; explanation: Explanation }
  execution: Execution
  portfolio: Portfolio
  stimulus: { kind: StimulusKind; delta_usdc: string }
  compute_seconds: number
}

export interface Observation {
  i: number
  t: number
  product: string
  market: { bid: string; ask: string; mid: number }
  frame_sha256?: string
  same_frame_both_arms?: boolean
  same_neural_input_both_arms?: boolean
  arms: Record<string, ArmObservation>
}

export interface DnpCellIds {
  left: string[]
  right: string[]
  gate: string[]
}

export interface BackendDescription {
  signal_source: SignalSource
  label: string
  simulated?: boolean
  neurons?: number
  directed_edges?: number
  plastic_edges?: number
  memory_updates_applied?: boolean
  decoder?: string
  decoder_cells?: DnpCellIds
  vision?: Record<string, unknown>
  memory_rule?: Record<string, unknown>
  model?: Record<string, unknown>
  rule?: string
  claims?: string
  parameters?: Record<string, number>
  /** The cells this arm's population vectors describe. Neural backends record it. */
  population?: PopulationDescription | null
}

/** Where an arm's brain started: reconstructed baseline, or restored from a checkpoint. */
export interface StartingWeights {
  kind: 'baseline' | 'trained' | 'reset'
  label: string
  file?: string | null
  sha256?: string | null
}

/** The cells the recorded population vector carries, recorded once per run. */
export interface PopulationDescription {
  schema: string
  size: number
  sample: number
  truncated: boolean
  groups: Record<string, number>
  ids: string[]
  types: string[]
  selection: string
}

/** A fitted readout's own metrics, recorded with the run that used it. */
export interface ReadoutDescription {
  schema: string
  horizon: number
  features: number
  trained_on: string
  train: { bars: number; accuracy: number; base_rate: number }
  holdout: { bars: number; accuracy: number; base_rate: number }
  top_features: { index: number; weight: number; type?: string; id?: string }[]
  notes?: string
}

export interface ArmMeta {
  id: string
  name: string
  role: string
  role_label: string
  tagline: string
  accent: string
  accent_soft: string
  detail: string
  learning: boolean
  starting_capital: string
  settings: Record<string, unknown>
  settings_signature: string
  starting_weights?: StartingWeights | null
  starting_report?: Record<string, unknown> | null
  backend: BackendDescription
}

export interface Trade {
  i: number
  t: number
  side: 'BUY' | 'SELL'
  product: string
  base_size: string
  price: string
  quote_size: string
  fee: string
  reason: string
  equity_after: string
  memory_changed_edges: number | null
}

export interface Benchmark {
  id: string
  label: string
  detail: string
  initial_capital: string
  curve: number[]
  fills: number
  base_size?: string
  entry_price?: string
  entry_fee?: string
  residual_cash?: string
}

export interface ArmSummary {
  name: string
  final_equity: number
  return_pct: number
  max_drawdown_pct: number
  path_volatility: number
  curve: number[]
  fills: number
  vetoes: number
  blocked_bars: number
  fees_paid: string
  halted: string | null
  exposure_bars: number
  trades: Trade[]
  deployment: {
    bars: number
    bars_holding: number
    holding_fraction: number
    order_limit_usdc: string
    daily_order_limit: number
    cooldown_seconds: number
  }
  final_memory: MemoryTelemetry | null
}

export interface Recording {
  schema: string
  run: {
    id: string
    created: number
    label: string
    /** What the two flies differ in: live learning, or the brain they carried in. */
    kind?: 'competition' | 'exam' | 'reset'
    reinforcement_mode?: string
    rule_preset?: string
    population?: PopulationDescription | null
    readout?: ReadoutDescription | null
    engine: 'neural' | 'procedural'
    repeat: number
    bars: number
    bar_seconds: number
    season: string
    rules: Record<string, string | number>
    /**
     * Null on a salvaged recording: the checkpoint it was rebuilt from predates these fields,
     * so the panels that prove fairness have nothing to prove it with and say so.
     */
    starting_conditions:
      | (Record<string, unknown> & {
          fairness: {
            differing_fields: string[]
            identical_fields: string[]
            identical_fields_sha256: string
          }
        })
      | null
    hardware: Record<string, unknown> | null
    duration_seconds: number
    seconds_per_bar_mean: number
    truncated: boolean
    wall_mode: string
    inputs_identical_every_bar: boolean
    /** Present when the recording was rebuilt from a killed session's own files. */
    salvaged?: Record<string, unknown>
  }
  arms: ArmMeta[]
  season: {
    describe: string
    provenance: Record<string, unknown>
    bars: { t: number; mid: number; bid: string; ask: string }[]
  }
  observations: Observation[]
  summary: {
    bars: number
    initial_capital: string
    duration_seconds: number
    seconds_per_bar_mean: number
    truncated: boolean
    arms: Record<string, ArmSummary>
    benchmarks: { buy_and_hold: Benchmark; cash: Benchmark }
    comparison: {
      memory_on: string
      memory_off: string
      equity_delta_usdc: number
      return_delta_pct: number
      leader: string
      single_season_note: string
    }
  }
  disclaimers: string[]
}

export interface RecordingListing {
  id: string
  label: string
  engine: 'neural' | 'procedural'
  season: string
  market: string
  bars: number
  created: number
  duration_seconds: number
  seconds_per_bar_mean: number
  truncated: boolean
  inputs_identical_every_bar: boolean
  returns: { gordon: number | null; warren: number | null; buy_and_hold: number | null }
  memory_changed_edges: number | null
}

export interface ReportRow {
  id: string
  season: string
  repeat: number
  bars: number
  gordon_pct: number
  warren_pct: number
  buy_hold_pct: number
  delta_pct: number
  gordon_fills: number
  warren_fills: number
  changed_edges: number | null
  seconds_per_bar: number
}

export interface ReportGroup {
  label: string
  engine: string
  repeats: number
  runs: ReportRow[]
  mean_gordon_pct: number
  mean_warren_pct: number
  mean_buy_hold_pct: number
  mean_delta_pct: number
  delta_spread_pct: number
  delta_min_pct: number
  delta_max_pct: number
  memory_on_wins: number
  memory_off_wins: number
  ties: number
  mean_changed_edges: number | null
}

/** How the data on screen got here. Only `live` is a run in progress right now. */
export type DataMode = 'live' | 'recorded' | 'demo'

export interface Source {
  mode: DataMode
  label: string
  detail: string
  /** True only when every rendered signal is real simulation output. */
  neural: boolean
}
