/**
 * The readout face: a small logistic model fitted from the fly's own population vector.
 *
 * The honest headline is accuracy measured against the majority-class base rate, not
 * accuracy on its own — a 0.55 accuracy on a series that is 0.55 up bars is a model that
 * has learned nothing. Both splits are shown side by side, with the at-or-below-guessing
 * case called out, and the feature bars say which cells the model leans on, never that
 * those cells cause the price to move.
 */
import type { ArmMeta } from '../lib/types'
import { pctValue, signed, signedPct } from '../lib/format'
import './ReadoutPanel.css'

export interface ReadoutSplit {
  bars: number
  accuracy: number
  base_rate: number
}

export interface ReadoutFeature {
  index: number
  weight: number
  type?: string
  id?: string
}

export interface Readout {
  schema: string
  horizon: number
  features: number
  trained_on: string
  train: ReadoutSplit
  holdout: ReadoutSplit
  top_features: ReadoutFeature[]
  notes?: string
}

export interface ReadoutPanelProps {
  readout: Readout | null
  arms: ArmMeta[]
}

/** Accuracy and base rate are fractions; the meter is a percentage and must clamp. */
function barWidth(value: number): string {
  if (!Number.isFinite(value)) return '0%'
  return `${(Math.min(1, Math.max(0, value)) * 100).toFixed(1)}%`
}

function Split({ label, split }: { label: string; split: ReadoutSplit }) {
  // The +0.01 band is the honest reading of "at the base rate": anything inside it is
  // indistinguishable from always guessing the majority class.
  const hasMetrics = Number.isFinite(split.accuracy) && Number.isFinite(split.base_rate)
  const atBaseRate = hasMetrics && split.accuracy <= split.base_rate + 0.01

  return (
    <div className="readout-split">
      <div className="readout-split-head">
        <span className="panel-title">{label}</span>
        <span className="readout-bars muted num">
          {Number.isFinite(split.bars) ? `${split.bars} bars` : 'bar count not recorded'}
        </span>
      </div>

      <div className="readout-meter">
        <div className="readout-meter-row">
          <span className="readout-meter-key">accuracy</span>
          <span className="readout-meter-track">
            <span
              className={`readout-meter-fill ${atBaseRate ? 'is-chance' : ''}`}
              style={{ width: barWidth(split.accuracy) }}
            />
          </span>
          <span className="readout-meter-value num">{pctValue(split.accuracy, 1)}</span>
        </div>
        <div className="readout-meter-row">
          <span className="readout-meter-key">base rate</span>
          <span className="readout-meter-track">
            <span className="readout-meter-fill is-base" style={{ width: barWidth(split.base_rate) }} />
          </span>
          <span className="readout-meter-value num">{pctValue(split.base_rate, 1)}</span>
        </div>
      </div>

      {!hasMetrics ? (
        <p className="readout-verdict muted">no accuracy recorded for this split</p>
      ) : atBaseRate ? (
        <p className="readout-verdict">
          <span className="chip chip-warn">no better than guessing</span>
        </p>
      ) : (
        <p className="readout-verdict muted">
          <span className="num">{signedPct((split.accuracy - split.base_rate) * 100, 1)}</span> over
          the base rate
        </p>
      )}
    </div>
  )
}

export function ReadoutPanel({ readout, arms }: ReadoutPanelProps) {
  if (!readout) {
    return (
      <section className="panel readout">
        <div className="panel-head">
          <span className="panel-title">Trading readout</span>
          <span className="chip">fixed rule</span>
        </div>
        <p className="readout-empty muted">
          This run decided from the fixed DNp20 rule, not from a fitted readout.
        </p>
      </section>
    )
  }

  const features = [...(readout.top_features ?? [])].sort(
    (a, b) => (Math.abs(b.weight) || 0) - (Math.abs(a.weight) || 0),
  )
  const maxAbs = features.reduce((max, f) => Math.max(max, Math.abs(f.weight) || 0), 0) || 1
  const bar = readout.horizon === 1 ? 'bar' : 'bars'

  return (
    <section className="panel readout">
      <div className="panel-head">
        <span className="panel-title">Fitted readout</span>
        <span className="readout-chips">
          <span className="chip">{readout.schema}</span>
          {arms.length > 0 && (
            <span className="chip">one model, both flies</span>
          )}
        </span>
      </div>

      <div className="readout-body">
        <p className="readout-claim">
          Predicts the sign of the price change{' '}
          <span className="num">{readout.horizon}</span> {bar} ahead, from{' '}
          <span className="num">{readout.features}</span> population features, fitted on{' '}
          <span className="num">{readout.train.bars}</span> bars.
        </p>
        <p className="readout-provenance muted">
          {readout.trained_on
            ? `Weights learned from ${readout.trained_on}.`
            : 'Training recording not recorded.'}{' '}
          Accuracy is shown against each split's base rate — the majority-class share. An
          accuracy below the base rate means the model is worse than always guessing the
          common direction.
        </p>

        <div className="readout-splits">
          <Split label="Train" split={readout.train} />
          <Split label="Holdout" split={readout.holdout} />
        </div>

        <div className="readout-features">
          <div className="readout-features-head">
            <span className="panel-title">Cells the model leans on</span>
            <span className="readout-bars muted">
              top {features.length} by absolute weight
            </span>
          </div>
          {features.length === 0 ? (
            <p className="muted readout-empty">no feature weights recorded</p>
          ) : (
            features.map((feature) => {
              const name = [feature.type, feature.id].filter(Boolean).join(' ')
              return (
                <div className="readout-feature" key={feature.index}>
                  <span className="readout-feature-label num" title={name || `feature ${feature.index}`}>
                    {name || `feature ${feature.index}`}
                  </span>
                  <span className="readout-feature-track">
                    <span
                      className="readout-feature-bar"
                      style={{ width: `${((Math.abs(feature.weight) || 0) / maxAbs) * 100}%` }}
                    />
                  </span>
                  <span className="readout-feature-weight num">{signed(feature.weight, 3)}</span>
                </div>
              )
            })
          )}
        </div>

        <p className="readout-caption muted">
          Bar length is the absolute weight. A large weight means the model leans on that
          cell, not that the cell causes anything. A positive weight pushes the score toward
          a buy, a negative weight toward a sell.
        </p>
        {readout.notes && <p className="readout-caption muted">{readout.notes}</p>}
      </div>
    </section>
  )
}
