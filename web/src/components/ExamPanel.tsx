/**
 * The exam face: a run in which neither fly learns, so the only experimental variable left
 * is the brain each one carried in. Every figure below comes from the recording — nothing
 * is generated — and the copy says plainly what one paired season can and cannot support.
 *
 * The panel also carries a cheap audit trail: the checkpoint hashes the flies were restored
 * from, shortened to a length that can be checked by eye.
 */
import type { CSSProperties } from 'react'
import type { ArmMeta, ArmSummary } from '../lib/types'
import { shortHash, signedPct, tone, usd } from '../lib/format'
import { linePath, paddedExtent } from '../lib/series'
import './ExamPanel.css'

/** What kind of weights an arm was restored from, as recorded by `flyvsly.starting`. */
export interface StartingWeights {
  kind: string
  label: string
  sha256?: string | null
}

export interface ExamComparison {
  leader: string
  return_delta_pct: number
  equity_delta_usdc: number
  single_season_note: string
}

export interface ExamPanelProps {
  kind: 'competition' | 'exam' | 'reset'
  /** Memory-on first, memory-off second — the same order the arena reports. */
  arms: ArmMeta[]
  summaries: Record<string, ArmSummary>
  starting: Record<string, StartingWeights>
  comparison: ExamComparison
  startingCapital: string
}

const HEADINGS: Record<ExamPanelProps['kind'], { title: string; body: string }> = {
  exam: {
    title: 'This was an exam: no learning happened here',
    body:
      'Both flies ran with their weight updates frozen, so neither brain changed while it ' +
      'traded. The only difference between them is the brain each one carried in.',
  },
  reset: {
    title: 'This was a reset run: no learning happened here',
    body:
      'Both flies ran with their weight updates frozen. Before the run the learned efficacies ' +
      'were wiped back to the reconstructed baseline, so what separates them is the brain each ' +
      'one carried in — and whether any advantage lived in the efficacies that were erased.',
  },
  competition: {
    title: 'Learning was live: this is the default mode',
    body:
      'One fly updated its weights as it traded and the other was frozen. Any difference here ' +
      'is what the run itself did, not something the flies brought with them.',
  },
}

/** Only the kinds the backend can emit, so a recording cannot smuggle in a class name. */
const STARTING_KINDS: Record<string, true> = { baseline: true, trained: true, reset: true }

const SPARK = { width: 220, height: 38, pad: 3 }

/**
 * A single fly's equity as a sparkline, with the starting capital drawn as a dashed
 * reference so "above or below where it began" is readable at a glance. Curves of length
 * 0 (nothing recorded yet) and 1 (a dot, no line to draw) are both handled explicitly.
 */
function Sparkline({
  curve,
  accent,
  baseline,
}: {
  curve: number[]
  accent: string
  baseline: number | null
}) {
  const points = (curve ?? []).filter((value) => Number.isFinite(value))
  if (points.length === 0) {
    return <p className="exam-spark-empty muted">no equity curve recorded for this fly</p>
  }

  const reference = baseline !== null && Number.isFinite(baseline) ? baseline : null
  const [low, high] = paddedExtent(reference === null ? points : [...points, reference])
  const { width, height, pad } = SPARK
  const plotWidth = width - pad * 2
  const plotHeight = height - pad * 2
  const x = (index: number) =>
    points.length === 1 ? width / 2 : pad + (index / (points.length - 1)) * plotWidth
  // paddedExtent guarantees a non-zero span, so this division is safe for flat curves.
  const y = (value: number) => pad + ((high - value) / (high - low)) * plotHeight
  const first = points[0]
  const last = points[points.length - 1]

  return (
    <svg
      className="exam-spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`equity curve over ${points.length} bars, ${usd(first)} to ${usd(last)}`}
    >
      {reference !== null && (
        <line
          className="exam-spark-base"
          x1={0}
          x2={width}
          y1={y(reference)}
          y2={y(reference)}
        />
      )}
      {points.length === 1 ? (
        <circle cx={x(0)} cy={y(last)} r={2.6} fill={accent} />
      ) : (
        <path d={linePath(points, x, y)} fill="none" stroke={accent} strokeWidth={1.6} />
      )}
    </svg>
  )
}

export function ExamPanel({
  kind,
  arms,
  summaries,
  starting,
  comparison,
  startingCapital,
}: ExamPanelProps) {
  // Recordings published before the exam mode existed carry no `kind`; they are competitions.
  const heading = HEADINGS[kind] ?? HEADINGS.competition
  const capital = Number(startingCapital)
  const baseline = startingCapital !== '' && Number.isFinite(capital) ? capital : null
  const onName = arms[0]?.name ?? 'the first fly'
  const offName = arms[1]?.name ?? 'the second fly'
  const leader = arms.find((arm) => arm.id === comparison.leader)?.name ?? null
  const audited = arms.filter((arm) => starting[arm.id]?.sha256)
  const anySparkline = arms.some(
    (arm) => (summaries[arm.id]?.curve ?? []).filter((value) => Number.isFinite(value)).length > 0,
  )

  return (
    <section className="panel exam">
      <div className="panel-head">
        <span className="panel-title">What this run is</span>
        <span className="chip">{kind}</span>
      </div>

      <div className="exam-headline">
        <h2>{heading.title}</h2>
        <p>{heading.body}</p>
      </div>

      <div className="exam-grid">
        {arms.map((arm) => {
          const summary = summaries[arm.id]
          const start = starting[arm.id]
          const startKind = start && STARTING_KINDS[start.kind] ? start.kind : 'other'
          return (
            <article
              className="exam-fly"
              key={arm.id}
              style={{ '--accent': arm.accent } as CSSProperties}
            >
              <div className="exam-fly-head">
                <span className="exam-dot" aria-hidden="true" />
                <span className="exam-fly-name">{arm.name}</span>
                {start ? (
                  <span
                    className={`chip exam-start exam-start-${startKind}`}
                    title={start.label}
                  >
                    {start.label}
                  </span>
                ) : (
                  <span className="chip exam-start">starting weights not recorded</span>
                )}
              </div>
              <p className="exam-role muted">{arm.role_label}</p>
              {summary ? (
                <dl className="exam-stats">
                  <div>
                    <dt>Return</dt>
                    <dd className={`num ${tone(summary.return_pct)}`}>
                      {signedPct(summary.return_pct)}
                    </dd>
                  </div>
                  <div>
                    <dt>Equity</dt>
                    <dd className="num">{usd(summary.final_equity)}</dd>
                  </div>
                  <div>
                    <dt>Fills</dt>
                    <dd className="num">{summary.fills}</dd>
                  </div>
                </dl>
              ) : (
                <p className="muted exam-nosummary">no summary recorded for this fly yet</p>
              )}
              <Sparkline curve={summary?.curve ?? []} accent={arm.accent} baseline={baseline} />
            </article>
          )
        })}
      </div>

      {anySparkline && (
        <p className="exam-spark-key muted">dashed line: starting capital</p>
      )}

      <div className="exam-compare">
        <div className="exam-compare-head">
          <span className="panel-title">Paired difference</span>
          <span className="chip">
            {leader ? `${leader} ahead` : 'no leader (tie)'}
            {` · ${kind}`}
          </span>
        </div>
        <dl className="exam-pair">
          <div>
            <dt>Return, {onName} minus {offName}</dt>
            <dd className={`num ${tone(comparison.return_delta_pct)}`}>
              {signedPct(comparison.return_delta_pct)}
            </dd>
          </div>
          <div>
            <dt>Equity, {onName} minus {offName}</dt>
            <dd className={`num ${tone(comparison.equity_delta_usdc)}`}>
              {usd(comparison.equity_delta_usdc)} USDC
            </dd>
          </div>
        </dl>
        <p className="exam-meaning">
          {kind === 'competition'
            ? 'This is the difference live weight updates made over one season, paired on the ' +
              'same market path. It is one path: it cannot separate a consistent effect from ' +
              'the outcome of a single sequence of bars.'
            : 'This is the only thing an exam isolates — what the two starting brains carried ' +
              'in. It is still one season on one market path, so it cannot show that either ' +
              'brain is better; only that these two runs ended differently.'}
        </p>
        <p className="exam-note muted">{comparison.single_season_note}</p>
      </div>

      {audited.length > 0 && (
        <p className="exam-audit muted">
          Checkpoint audit trail:{' '}
          {audited
            .map((arm) => `${arm.name} ${shortHash(starting[arm.id]?.sha256)}`)
            .join(' · ')}
        </p>
      )}
    </section>
  )
}
