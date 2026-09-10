/**
 * One fly, one bar: which measured signals and which programmed rules produced this
 * proposal, and what the execution guard then did with it.
 *
 * Everything rendered here is read from the recording. Labels that mention spikes or
 * neural time exist only inside the `isNeural` branch, so a procedural signal cannot
 * borrow neural language.
 */

import type { CSSProperties } from 'react'
import type { ArmMeta, ArmObservation, Side } from '../lib/types'
import { isNeural } from '../lib/types'
import { duration, shortHash, usd } from '../lib/format'
import './DecisionExplainer.css'

interface DecisionExplainerProps {
  arm: ArmMeta
  observation: ArmObservation | null
  bar: number
  rules: Record<string, string | number>
  fairness: {
    differing_fields: string[]
    identical_fields: string[]
    identical_fields_sha256: string
  }
  product: string
}

const SIDE_CLASS: Record<Side, string> = {
  BUY: 'dx-side dx-buy',
  SELL: 'dx-side dx-sell',
  HOLD: 'dx-side dx-hold',
  BLOCKED: 'dx-side dx-blocked',
}

const STATUS_CLASS: Record<string, string> = {
  FILLED: 'chip chip-good',
  VETO: 'chip chip-bad',
  BLOCKED: 'chip chip-warn',
  HOLD: 'chip',
}

interface MeasuredSpec {
  label: string
  unit: string
  render: (value: number) => string
}

/** Units are part of the honesty contract: a bare number would invite misreading. */
const MEASURED_SPECS: Record<string, MeasuredSpec> = {
  dnp20_left_hz: {
    label: 'Mean left DNp20 firing',
    unit: 'Hz',
    render: (v) => v.toFixed(3),
  },
  dnp20_right_hz: {
    label: 'Mean right DNp20 firing',
    unit: 'Hz',
    render: (v) => v.toFixed(3),
  },
  difference_hz: {
    label: 'Decoded difference (right − left)',
    unit: 'Hz',
    render: (v) => v.toFixed(3),
  },
  gate_spikes: {
    label: 'DNpe017 gate spikes',
    unit: 'spikes',
    render: (v) => String(Math.round(v)),
  },
  threshold_hz: { label: 'Programmed threshold', unit: 'Hz', render: (v) => v.toFixed(2) },
  seconds: { label: 'Observation window of neural time', unit: 's', render: (v) => v.toFixed(3) },
  momentum: { label: 'Trailing return over window', unit: '', render: (v) => `${(v * 100).toFixed(3)}%` },
  volatility: { label: 'Realised volatility of window', unit: '', render: (v) => `${(v * 100).toFixed(3)}%` },
  score: { label: 'Momentum score', unit: '', render: (v) => v.toFixed(3) },
  threshold: { label: 'Programmed score threshold', unit: '', render: (v) => v.toFixed(2) },
  lookback: { label: 'Observed bars in window', unit: 'bars', render: (v) => String(v) },
  gate: { label: 'Gate state', unit: '', render: (v) => String(v) },
}

/** Measured keys that only exist for a connectome signal; hidden otherwise. */
const NEURAL_ONLY_MEASURED: Record<string, true> = {
  dnp20_left_hz: true,
  dnp20_right_hz: true,
  difference_hz: true,
  gate_spikes: true,
  threshold_hz: true,
  seconds: true,
}

const RULE_LABELS: Record<string, string> = {
  products: 'Traded products',
  capital: 'Starting capital (USDC)',
  order_limit: 'Order cap (USDC)',
  loss_stop: 'Loss stop (USDC)',
  fee_reserve: 'Fee reserve (USDC)',
  slippage: 'Slippage cap',
  spread_limit: 'Spread limit',
  daily_orders: 'Orders per day',
  interval_seconds: 'Order cooldown (s)',
  max_quote_age: 'Max quote age (s)',
  neural_ms: 'Observation window (ms)',
  neural_bin_ms: 'Bin (ms)',
  pulse_ms: 'Pulse (ms)',
  pulse_current: 'Pulse current',
  reward_deadband: 'Reward deadband (USDC)',
  decoder_threshold_hz: 'Decoder threshold (Hz)',
  paper_fee: 'Fee per side',
}

/** Human label for a telemetry key; unknown keys get their snake_case spelled out. */
function labelFor(key: string): string {
  return (
    RULE_LABELS[key] ??
    key.replace(/_/g, ' ').replace(/\b[a-z]/g, (c) => c.toUpperCase())
  )
}

export function DecisionExplainer({
  arm,
  observation,
  bar,
  rules,
  fairness,
  product,
}: DecisionExplainerProps) {
  const style = { '--arm': arm.accent, '--arm-soft': arm.accent_soft } as CSSProperties
  const baseUnit = product.split('-')[0]

  const fairnessNote = (
    <footer className="dx-fair">
      <p>
        The only configured difference between the two flies is{' '}
        <span className="num dx-field">{fairness.differing_fields.join(', ')}</span>. Every
        other setting is frozen and identical:{' '}
        <span className="num">{fairness.identical_fields.length}</span> fields share the
        checksum <span className="num" title={fairness.identical_fields_sha256}>
          {shortHash(fairness.identical_fields_sha256)}
        </span>
        .
      </p>
    </footer>
  )

  const ruleTable = (
    <details className="dx-rules">
      <summary>Shared rules both flies obey</summary>
      <div className="dx-rule-grid">
        {Object.keys(rules)
          .sort()
          .map((key) => (
            <div className="dx-rule" key={key}>
              <span className="dx-rule-key">{labelFor(key)}</span>
              <span className="num dx-rule-value">{String(rules[key])}</span>
            </div>
          ))}
      </div>
    </details>
  )

  if (!observation) {
    return (
      <section className="panel dx" style={style}>
        <header className="panel-head">
          <span className="panel-title">{arm.name} — decision explainer</span>
          <span className="panel-sub">
            bar <span className="num">{bar}</span>
          </span>
        </header>
        <p className="dx-empty muted">
          No observation recorded for this fly at bar <span className="num">{bar}</span>. Nothing
          happened on this bar that the recording captured for {arm.name}.
        </p>
        {fairnessNote}
      </section>
    )
  }

  const signal = observation.signal
  const neural = isNeural(signal)
  const decision = observation.decision
  const explanation = decision.explanation
  const execution = observation.execution
  const plan = execution.plan
  const blocked = decision.side === 'BLOCKED'

  const measured = Object.entries(explanation.measured)
    .filter(([key]) => neural || !NEURAL_ONLY_MEASURED[key])
    .map(([key, value]) => {
      const spec = MEASURED_SPECS[key]
      return {
        key,
        label: spec ? spec.label : labelFor(key),
        unit: spec ? spec.unit : '',
        value: spec ? spec.render(value) : String(value),
      }
    })

  return (
    <section className="panel dx" style={style}>
      <header className="panel-head">
        <span className="panel-title">
          <span className="dx-arm-dot" aria-hidden="true" />
          {arm.name} — decision explainer
        </span>
        <span className="panel-sub">
          {arm.role_label} · bar <span className="num">{bar}</span>
        </span>
      </header>

      <div className="dx-body">
        <div className="dx-provenance">
          {neural ? (
            <span className="chip chip-good">connectome signal</span>
          ) : signal ? (
            <span className="chip chip-warn">simulated demo signal</span>
          ) : (
            <span className="chip">no signal recorded</span>
          )}
          {signal ? <span className="chip">{signal.label}</span> : null}
          <span className="chip">rule: {explanation.kind}</span>
        </div>

        <div className={SIDE_CLASS[decision.side]}>
          <span className="dx-side-label">{decision.side}</span>
          <span className="dx-side-note">
            {blocked ? (
              <>no proposal: the guard blocked bar <span className="num">{bar}</span></>
            ) : (
              <>proposal from the recorded signal at bar <span className="num">{bar}</span></>
            )}
          </span>
        </div>

        {blocked ? (
          <p className="dx-callout dx-callout-warn">
            The execution guard ran before any observation was taken, so this bar cost
            {' '}{arm.name} no measurement time at all.
          </p>
        ) : null}

        {signal === null ? (
          <p className="dx-empty muted">No signal was recorded for this bar.</p>
        ) : null}

        <section className="dx-block">
          <h4 className="dx-h">Programmed rule</h4>
          <blockquote className="dx-rule-quote">{explanation.rule}</blockquote>
        </section>

        <section className="dx-block">
          <h4 className="dx-h">
            How the measured values produced <span className="num">{explanation.result}</span>
          </h4>
          {explanation.steps.length ? (
            <ol className="dx-steps">
              {explanation.steps.map((step, index) => (
                <li key={index}>{step}</li>
              ))}
            </ol>
          ) : (
            <p className="muted dx-empty">No steps recorded for this bar.</p>
          )}
        </section>

        <section className="dx-block">
          <h4 className="dx-h">Measured</h4>
          {measured.length ? (
            <table className="dx-measured">
              <thead>
                <tr>
                  <th scope="col">quantity</th>
                  <th scope="col">value</th>
                  <th scope="col">unit</th>
                </tr>
              </thead>
              <tbody>
                {measured.map((row) => (
                  <tr key={row.key}>
                    <td>{row.label}</td>
                    <td className="num dx-num">{row.value}</td>
                    <td className="muted">{row.unit || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted dx-empty">
              {blocked
                ? 'No measured values: the guard ran before any observation was taken.'
                : 'No measured values were recorded for this bar.'}
            </p>
          )}
        </section>

        <section className="dx-block">
          <h4 className="dx-h">Execution</h4>
          <div className="dx-exec-head">
            <span className={STATUS_CLASS[execution.status] ?? 'chip'}>{execution.status}</span>
            <span className="muted dx-exec-product">{product}</span>
          </div>
          {execution.explanation?.length ? (
            <ul className="dx-exec-lines">
              {execution.explanation.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          ) : (
            <p className="muted dx-empty">No execution notes recorded.</p>
          )}
          {plan ? (
            <dl className="dx-plan">
              <div>
                <dt>base size</dt>
                <dd className="num">
                  {plan.base_size} {baseUnit}
                </dd>
              </div>
              <div>
                <dt>limit price</dt>
                <dd className="num">{usd(plan.limit_price)} USDC</dd>
              </div>
              <div>
                <dt>fee ceiling</dt>
                <dd className="num">{usd(plan.fee_ceiling)} USDC</dd>
              </div>
              <div>
                <dt>client order id</dt>
                <dd className="num" title={plan.client_order_id}>
                  {shortHash(plan.client_order_id)}
                </dd>
              </div>
            </dl>
          ) : null}
        </section>

        {explanation.engineered_interface && explanation.note ? (
          <aside className="dx-note chip-warn">
            <span className="dx-note-tag">engineered interface</span>
            <p>{explanation.note}</p>
          </aside>
        ) : null}

        {neural && signal ? (
          <section className="dx-block dx-audit">
            <h4 className="dx-h">Neural audit trail</h4>
            <dl className="dx-plan">
              <div>
                <dt>spike hash</dt>
                <dd className="num" title={signal.spike_sha256}>
                  {shortHash(signal.spike_sha256)}
                </dd>
              </div>
              <div>
                <dt>input hash</dt>
                <dd className="num" title={signal.input_sha256}>
                  {shortHash(signal.input_sha256)}
                </dd>
              </div>
              <div>
                <dt>neural time simulated (cumulative)</dt>
                <dd className="num">{signal.brain_ms} ms</dd>
              </div>
              <div>
                <dt>wall compute</dt>
                <dd className="num">{duration(signal.compute_seconds)}</dd>
              </div>
            </dl>
            <p className="dx-audit-note muted">
              Spikes counted by the MaleCNS v1.0 connectome run; the decoder above reads their
              firing rates only.
            </p>
          </section>
        ) : null}

        {ruleTable}
      </div>
      {fairnessNote}
    </section>
  )
}
