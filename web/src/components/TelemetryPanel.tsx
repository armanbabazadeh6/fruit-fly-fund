import type { ArmMeta, Observation } from '../lib/types'
import { isNeural } from '../lib/types'
import { compact, shortHash, usd } from '../lib/format'
import './TelemetryPanel.css'

interface TelemetryPanelProps {
  arm: ArmMeta
  observations: Observation[]
  index: number
}

const SPARK_HEIGHT = 46
const SPARK_WIDTH = 260

/** Spike counts per bar, or the procedural score, for the bars already observed. */
function sparkPoints(observations: Observation[], armId: string, index: number) {
  const values: number[] = []
  for (let i = 0; i <= index && i < observations.length; i += 1) {
    const signal = observations[i]?.arms?.[armId]?.signal
    if (!signal) continue
    values.push(signal.signal_source === 'neural' ? signal.total_spikes : signal.score * 1000)
  }
  if (values.length < 2) return { values, points: '' }
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const points = values
    .map((value, i) => {
      const x = (i / (values.length - 1)) * SPARK_WIDTH
      const y = SPARK_HEIGHT - ((value - min) / span) * (SPARK_HEIGHT - 4) - 2
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return { values, points }
}

export function TelemetryPanel({ arm, observations, index }: TelemetryPanelProps) {
  const observation = observations[index]
  const signal = observation?.arms?.[arm.id]?.signal ?? null
  const neural = isNeural(signal)
  const spark = sparkPoints(observations, arm.id, index)
  const thresholdHz = Number(
    observation?.arms?.[arm.id]?.decision?.explanation?.measured?.threshold_hz ?? 2,
  )

  return (
    <section className="panel telemetry" style={{ '--accent': arm.accent } as React.CSSProperties}>
      <div className="panel-head">
        <span className="panel-title">{arm.name} · signal telemetry</span>
        <span className={`chip ${neural ? 'chip-good' : 'chip-warn'}`}>
          {neural ? 'live connectome output' : signal ? 'simulated signal' : 'no signal'}
        </span>
      </div>

      {!signal && (
        <p className="telemetry-empty muted">
          No observation at this bar.{' '}
          {observation?.arms?.[arm.id]?.decision?.side === 'BLOCKED'
            ? 'The execution guard ran before any neural time was spent — see the decision panel.'
            : ''}
        </p>
      )}

      {neural && (
        <>
          <div className="telemetry-meters">
            <Meter label="right DNp20" value={signal.right_hz} max={60} hz />
            <Meter label="left DNp20" value={signal.left_hz} max={60} hz />
            <Meter
              label="right − left"
              value={Math.abs(signal.difference_hz)}
              max={60}
              hz
              note={`${signal.difference_hz >= 0 ? '+' : '−'}${Math.abs(signal.difference_hz).toFixed(3)} Hz vs ±${thresholdHz.toFixed(2)} Hz`}
              highlight={Math.abs(signal.difference_hz) >= thresholdHz}
            />
          </div>

          <dl className="telemetry-grid num">
            <Item label="gate DNpe017 spikes" value={compact(signal.gate_spikes)} />
            <Item label="KC spikes" value={compact(signal.KC_spikes)} />
            <Item label="reward DAN spikes" value={compact(signal.reward_spikes)} />
            <Item label="aversive DAN spikes" value={compact(signal.aversive_spikes)} />
            <Item label="total spikes" value={compact(signal.total_spikes)} />
            <Item
              label="stimulus"
              value={
                signal.stimulus === 'none'
                  ? 'none'
                  : `${signal.stimulus} · ${signal.stimulus_ms.toFixed(0)} ms`
              }
            />
            <Item label="neural time" value={`${signal.brain_ms.toFixed(0)} ms`} />
            <Item label="wall compute" value={`${signal.compute_seconds.toFixed(2)} s`} />
          </dl>

          <div className="telemetry-memory">
            <span className="telemetry-memory-label">memory rule</span>
            {signal.memory.enabled ? (
              <p>
                <strong>{signal.memory.changed_edges?.toLocaleString() ?? '—'}</strong> of{' '}
                {signal.memory.plastic_edges?.toLocaleString() ?? '—'} KC→MBON efficacies differ
                from baseline · mean {signal.memory.mean_efficacy?.toFixed(5)}× ·{' '}
                <span className="num muted">sha {shortHash(signal.memory.sha256, 8)}</span>
              </p>
            ) : (
              <p className="muted">
                Updates frozen by configuration: all {signal.memory.plastic_edges?.toLocaleString() ?? '—'}{' '}
                eligible efficacies stay at the reconstructed baseline for the whole season.
              </p>
            )}
          </div>

          <div className="telemetry-hashes num muted">
            spikes <span>{shortHash(signal.spike_sha256, 12)}</span> · input{' '}
            <span>{shortHash(signal.input_sha256, 12)}</span> · identical input this bar across
            both flies: {observation?.same_neural_input_both_arms ? 'yes' : 'not verified'}
          </div>
        </>
      )}

      {signal && !neural && (
        <>
          <div className="telemetry-grid num">
            <Item label="momentum score" value={signal.score.toFixed(3)} />
            <Item label="threshold" value={`±${signal.threshold.toFixed(2)}`} />
            <Item label="trailing return" value={`${(signal.momentum * 100).toFixed(3)}%`} />
            <Item label="realised volatility" value={`${(signal.volatility * 100).toFixed(3)}%`} />
            <Item label="lookback" value={`${signal.lookback} bars`} />
            <Item
              label="simulated memory bias"
              value={
                signal.simulated_memory_delta === null
                  ? 'not applied'
                  : signal.simulated_memory_delta.toFixed(4)
              }
            />
          </div>
          <div className="telemetry-memory">
            <span className="telemetry-memory-label">why this is labelled simulated</span>
            <p className="muted">
              The procedural backend runs no connectome and no synapses. It exists so the interface
              can be built and reviewed without the full simulation. Any resemblance to the
              memory rule is a stand-in, not a measurement.
            </p>
          </div>
        </>
      )}

      {spark.points && (
        <div className="telemetry-spark">
          <span className="telemetry-spark-label">
            {neural ? 'total spikes per bar' : 'score per bar (×1000)'}
          </span>
          <svg
            viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={neural ? 'Spike count per observed bar' : 'Procedural score per observed bar'}
          >
            <polyline points={spark.points} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
          </svg>
          <span className="telemetry-spark-range num muted">
            {spark.values.length} bars · peak {usd(Math.max(...spark.values), 0)}
          </span>
        </div>
      )}
    </section>
  )
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function Meter({
  label,
  value,
  max,
  hz,
  note,
  highlight,
}: {
  label: string
  value: number
  max?: number
  hz?: boolean
  note?: string
  highlight?: boolean
}) {
  const bound = max ?? Math.max(value, 1)
  const fill = Math.min(100, (value / bound) * 100)
  return (
    <div className={`meter ${highlight ? 'meter-hot' : ''}`}>
      <span className="meter-label">
        {label}
        <span className="num">
          {hz ? `${value.toFixed(3)} Hz` : usd(value, 0)}
        </span>
      </span>
      <span className="meter-track">
        <span className="meter-fill" style={{ width: `${fill}%` }} />
      </span>
      {note && <span className="meter-note num muted">{note}</span>}
    </div>
  )
}
