import type { Recording } from '../lib/types'
import { duration, usd, pctValue, shortHash } from '../lib/format'
import './Provenance.css'

interface ProvenanceProps {
  recording: Recording
}

/**
 * The honesty panel. Everything a reader needs to judge the run rather than the cartoon:
 * what was simulated, on what hardware, under which frozen rules, and what would actually
 * be required before anyone could claim the memory rule helps.
 */
export function Provenance({ recording }: ProvenanceProps) {
  const { run, summary, arms } = recording
  const hardware = run.hardware as Record<string, unknown>
  const manifest = (hardware.data_manifest ?? {}) as Record<string, unknown>
  const fairness = run.starting_conditions?.fairness
  const identical = Object.entries(run.starting_conditions ?? {}).filter(
    ([key]) => key !== 'fairness',
  )

  return (
    <section className="panel provenance">
      <div className="panel-head">
        <span className="panel-title">How to read this</span>
        <span className={`chip ${run.engine === 'neural' ? 'chip-good' : 'chip-warn'}`}>
          {run.engine === 'neural' ? 'real simulation output' : 'procedural demo — not neural'}
        </span>
      </div>

      <div className="provenance-grid">
        <div>
          <h3>Two flies</h3>
          <ul>
            {arms.map((arm) => (
              <li key={arm.id}>
                <strong style={{ color: arm.accent }}>{arm.name}</strong> — {arm.role_label}.{' '}
                {arm.detail}
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3>Only one difference</h3>
          <p>
            Configuration proof: the two accounts differ in exactly{' '}
            <span className="num">{fairness?.differing_fields?.join(', ') ?? 'learning'}</span>, with
            the remaining <strong>{fairness?.identical_fields?.length ?? 0}</strong> fields frozen
            under hash <span className="num">{shortHash(fairness?.identical_fields_sha256, 12)}</span>.
            Both saw byte-identical charts this season:{' '}
            <strong>{run.inputs_identical_every_bar ? 'yes' : 'not verified'}</strong>.
          </p>
          <ul className="provenance-rules num">
            {identical.map(([key, value]) => (
              <li key={key}>
                <span>{key.replace(/_/g, ' ')}</span>
                <span>{String(value)}</span>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3>This run</h3>
          <ul className="provenance-rules num">
            <li>
              <span>season</span>
              <span>{run.season}</span>
            </li>
            <li>
              <span>bars</span>
              <span>
                {summary.bars} × {run.bar_seconds}s
              </span>
            </li>
            <li>
              <span>compute</span>
              <span>
                {duration(summary.duration_seconds)} ({summary.seconds_per_bar_mean?.toFixed(1)}s per
                bar, both flies together)
              </span>
            </li>
            <li>
              <span>clock</span>
              <span>{run.wall_mode}</span>
            </li>
            <li>
              <span>machine</span>
              <span>
                {String(hardware.machine ?? '?')} · {String(hardware.cpu_count ?? '?')} CPUs ·{' '}
                {String(hardware.python ?? '?')} · numpy {String(hardware.numpy ?? '?')}
              </span>
            </li>
            <li>
              <span>accelerator</span>
              <span>{String(hardware.gpu_acceleration ?? 'CPU only')}</span>
            </li>
            <li>
              <span>dataset</span>
              <span>
                {String(manifest.release ?? 'MaleCNS v1.0')} ·{' '}
                {Number(manifest.neurons ?? 0).toLocaleString()} neurons ·{' '}
                {Number(manifest.edges ?? 0).toLocaleString()} connections
              </span>
            </li>
          </ul>
        </div>

        <div>
          <h3>What this does not show</h3>
          <ul>
            <li>
              Memory updates are an experimental rule, not an assumed improvement. In this season
              memory-on ended at {usd(summary.arms.gordon?.final_equity ?? 0)} and memory-off at{' '}
              {usd(summary.arms.warren?.final_equity ?? 0)} — one path through one market, which is
              not evidence either way.
            </li>
            <li>
              The flies could only hold {pctValue(summary.arms.gordon?.deployment?.holding_fraction ?? 0, 1)}{' '}
              of the time in market, capped at {summary.arms.gordon?.deployment?.order_limit_usdc ?? '10'}{' '}
              USDC per order with a {summary.arms.gordon?.deployment?.cooldown_seconds ?? 60}s cooldown.
              Buy &amp; hold obeys none of that.
            </li>
            <li>
              Fees are charged on every fill at the configured rate, so a fly that churns pays for
              the privilege: {usd(summary.arms.gordon?.fees_paid ?? '0', 4)} and{' '}
              {usd(summary.arms.warren?.fees_paid ?? '0', 4)} USDC in this run.
            </li>
            <li>
              Upstream Stonkfly states plainly that profitable learning has not been demonstrated.
              This project does not change that claim and does not attempt to.
            </li>
          </ul>
        </div>

        <div>
          <h3>What would count as learning</h3>
          <p>
            Held-out chronological replay, independent starts, frozen-weight and
            shuffled-reinforcement controls, equal budgets, retention check, and loss of benefit
            after resetting the learned weights — plus beating cash and simple exposure baselines.
            Repeating seasons is a start, not an answer: see the paired spread across seasons in the
            header before reading anything into a single run.
          </p>
        </div>
      </div>

      <ul className="provenance-disclaimers">
        {recording.disclaimers.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </section>
  )
}
