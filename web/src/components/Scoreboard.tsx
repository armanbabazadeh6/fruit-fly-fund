import type { ArmMeta, ArmSummary, Benchmark, MemoryTelemetry } from '../lib/types'
import { pctValue, signedPct, tone, usd } from '../lib/format'
import './Scoreboard.css'

interface ScoreboardProps {
  arms: ArmMeta[]
  summaries: Record<string, ArmSummary>
  benchmarks: { buy_and_hold: Benchmark; cash: Benchmark }
  initialCapital: string
  comparison: {
    leader: string
    equity_delta_usdc: number
    return_delta_pct: number
    single_season_note: string
  }
  barIndex: number
  provisional?: boolean
}

function memoryLine(memory: MemoryTelemetry | null) {
  if (!memory) return <span className="muted">no memory telemetry</span>
  if (!memory.enabled) {
    return (
      <span className="muted">
        updates frozen · {memory.plastic_edges?.toLocaleString() ?? '—'} eligible efficacies at
        baseline
      </span>
    )
  }
  if (memory.simulated) {
    return <span className="muted">simulated bias {usd(memory.bias ?? 0, 4)} (demo term)</span>
  }
  return (
    <span>
      {memory.changed_edges?.toLocaleString() ?? '—'} of{' '}
      {memory.plastic_edges?.toLocaleString() ?? '—'} KC→MBON efficacies changed · mean{' '}
      {memory.mean_efficacy?.toFixed(5)}× baseline
    </span>
  )
}

export function Scoreboard({
  arms,
  summaries,
  benchmarks,
  initialCapital,
  comparison,
  barIndex,
  provisional,
}: ScoreboardProps) {
  const [onArm, offArm] = arms
  const on = summaries[onArm.id]
  const off = summaries[offArm.id]
  const start = Number(initialCapital)

  return (
    <section className="scoreboard">
      {[onArm, offArm].map((arm, position) => {
        const summary = position === 0 ? on : off
        const live = summary.curve?.[barIndex] ?? start
        const livePct = start ? (live / start - 1) * 100 : 0
        return (
          <article
            className="score card"
            key={arm.id}
            style={{ '--accent': arm.accent, '--accent-soft': arm.accent_soft } as React.CSSProperties}
          >
            <header className="score-head">
              <div>
                <h2 className="score-name">{arm.name}</h2>
                <p className="score-role">
                  <span className={`role role-${arm.learning ? 'on' : 'off'}`}>{arm.role_label}</span>
                  <span className="score-tagline">{arm.tagline}</span>
                </p>
              </div>
              <div className="score-equity num">
                <span className="score-equity-value">{usd(live)}</span>
                <span className={`score-equity-delta ${tone(livePct)}`}>{signedPct(livePct, 3)}</span>
              </div>
            </header>

            <dl className="score-stats num">
              <div>
                <dt>fills</dt>
                <dd>{summary?.fills ?? 0}</dd>
              </div>
              <div>
                <dt>guard vetoes</dt>
                <dd>{summary?.vetoes ?? 0}</dd>
              </div>
              <div>
                <dt>fees paid</dt>
                <dd>{usd(summary?.fees_paid ?? '0', 4)}</dd>
              </div>
              <div>
                <dt>max drawdown</dt>
                <dd>{usd(summary?.max_drawdown_pct ?? 0, 3)}%</dd>
              </div>
              <div>
                <dt>bars holding</dt>
                <dd>{summary?.exposure_bars ?? 0}</dd>
              </div>
              <div>
                <dt>time in market</dt>
                <dd>{pctValue(summary?.deployment?.holding_fraction ?? 0, 1)}</dd>
              </div>
            </dl>

            <p className="score-memory">{memoryLine(summary?.final_memory ?? null)}</p>

            {summary?.halted && (
              <p className="score-halt chip chip-bad">halted: {summary.halted}</p>
            )}
            <p className="score-detail">{arm.detail}</p>
          </article>
        )
      })}

      <article className="score benchmark card">
        <h2 className="score-name">
          Benchmarks
          {provisional && <span className="chip chip-warn provenance-live">live figures</span>}
        </h2>
        <p className="score-role">
          <span className="score-tagline">
            Same bars, same 0.6% fee per side. Buy &amp; hold makes one unrestricted fill and obeys
            no order-size, cooldown or daily-order limit — the flies obey all three.
          </span>
        </p>
        <dl className="score-stats num">
          <div>
            <dt>{benchmarks.buy_and_hold.label}</dt>
            <dd>{signedPct(benchmarks.buy_and_hold.curve.length
              ? (benchmarks.buy_and_hold.curve[barIndex] /
                  Number(benchmarks.buy_and_hold.initial_capital) -
                  1) *
                100
              : 0, 3)}</dd>
          </div>
          <div>
            <dt>{benchmarks.cash.label}</dt>
            <dd className="flat">0.000%</dd>
          </div>
          <div>
            <dt>memory-on − memory-off</dt>
            <dd className={tone(comparison.return_delta_pct)}>
              {signedPct(comparison.return_delta_pct, 3)}
            </dd>
          </div>
          <div>
            <dt>equity gap</dt>
            <dd className={tone(comparison.equity_delta_usdc)}>
              {usd(comparison.equity_delta_usdc, 4)} USDC
            </dd>
          </div>
        </dl>
        <p className="score-detail benchmark-note">{comparison.single_season_note}</p>
      </article>
    </section>
  )
}
