/**
 * The cell inspector: one measured cell, read straight out of the recorded population.
 *
 * A dot in the brain scan is a cell, and this is what that cell is: the recording's own id and
 * annotated type, the annotated groups the recording counted it in, and its measured spike
 * count on every bar of the session, with the bar on screen marked. A cell that never fires is
 * a fact about the run rather than a blank, so it gets the same panel as any other — the chart
 * is simply flat, at zero, for every bar.
 *
 * What is not claimed: the scan's positions are illustrative, not anatomical, and each point
 * on the chart is one completed bar's spike count, not individual spike timing.
 */

import type { ArmMeta, Observation, PopulationDescription } from '../lib/types'
import { isNeural } from '../lib/types'
import { groupFiring, groupsForType } from '../lib/population'
import './CellInspector.css'

/** The cell one fly's scan had picked, and which fly it belongs to. */
export interface CellPick {
  armId: string
  cell: number
}

const CHART_W = 560
const CHART_H = 84

/** The population vector one arm recorded on one bar, or empty when it is not neural. */
function vectorFor(observation: Observation | undefined, armId: string): number[] {
  const signal = observation?.arms?.[armId]?.signal
  return isNeural(signal) ? signal.population ?? [] : []
}

/**
 * Every bar's spike count for one cell, in market order. A live session has only the bars it
 * has traded; the chart grows with it rather than forecasting the rest of the season.
 */
function Series({ series, index, peak }: { series: number[]; index: number; peak: number }) {
  const bars = Math.max(1, series.length)
  const step = CHART_W / bars
  const width = Math.max(1, step * 0.6)
  // A zero bar keeps a one-unit tick so a silent cell reads as a flat series rather than an
  // empty chart; a bar that did fire is at least two units, so one spike is never drawn as
  // the same mark as none.
  const height = (value: number) =>
    value > 0 ? Math.max(2, (value / Math.max(1, peak)) * (CHART_H - 10)) : 1
  const centre = Math.min(index, bars - 1)
  const current = series[centre] ?? 0

  return (
    <svg
      className="cell-series"
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Spike count for this cell on each of ${series.length} bars; bar ${index + 1} is marked`}
    >
      <rect className="cell-series-plot" x={0} y={0} width={CHART_W} height={CHART_H} />
      <rect className="cell-series-current" x={centre * step} y={0} width={step} height={CHART_H} />
      {series.map((value, i) => (
        <rect
          key={i}
          className={`cell-series-bar ${value > 0 ? '' : 'is-quiet'}`}
          x={i * step + (step - width) / 2}
          y={CHART_H - height(value)}
          width={width}
          height={height(value)}
        />
      ))}
      <line className="cell-series-baseline" x1={0} y1={CHART_H} x2={CHART_W} y2={CHART_H} />
      <circle
        className="cell-series-point"
        cx={centre * step + step / 2}
        cy={Math.min(CHART_H - 2, CHART_H - height(current))}
        r={3}
      />
    </svg>
  )
}

export interface CellInspectorProps {
  /** The run's cells, once per run. Null on a run that recorded no population description. */
  population: PopulationDescription | null
  engine: 'neural' | 'procedural'
  arms: ArmMeta[]
  observations: Observation[]
  index: number
  picked: CellPick | null
  /** The reader chose a cell, by clicking a dot or stepping through the population. */
  onPick: (pick: CellPick) => void
}

export function CellInspector({
  population,
  engine,
  arms,
  observations,
  index,
  picked,
  onPick,
}: CellInspectorProps) {
  // A neural run can predate the population block — an old checkpoint salvaged into a
  // recording stores per-bar vectors but not the ids and types that would name them. That is
  // a real state, so it is stated rather than shown as a panel with nothing in it.
  if (!population) {
    if (engine !== 'neural') return null
    return (
      <div className="cell-inspector" data-population="missing">
        <p className="cell-note muted">
          This recording has no population description, so its cells cannot be identified. A
          session salvaged from an older checkpoint stored its per-bar spike vectors without the
          cell ids and annotated types that would name them.
        </p>
      </div>
    )
  }

  const arm = arms.find((entry) => entry.id === picked?.armId) ?? arms[0]
  const armId = arm?.id ?? ''
  const names = Object.keys(population.groups)
  const cell =
    picked && picked.cell >= 0 && picked.cell < population.types.length ? picked.cell : null
  // Read from the recording's own group block, never a fixed list of names.
  const groups = groupFiring(population, vectorFor(observations[index], armId))
  const series = cell === null ? [] : observations.map((entry) => vectorFor(entry, armId)[cell] ?? 0)
  const peak = series.reduce((high, value) => Math.max(high, value), 0)
  const total = series.reduce((sum, value) => sum + value, 0)
  const firedBars = series.filter((value) => value > 0).length
  // Stepping wraps, so a cell hidden behind the scan's bright ones is reachable from either
  // end. That matters most for the cells that never fire: they draw no light of their own and
  // are the ones a reader is least likely to be able to click.
  const step = (delta: number) => {
    if (!arm) return
    const from = cell === null ? (delta > 0 ? -1 : population.size) : cell
    onPick({ armId: arm.id, cell: (from + delta + population.size) % population.size })
  }

  return (
    <div
      className="cell-inspector"
      data-population="recorded"
      data-cell={cell === null ? '' : cell}
      data-cell-id={cell === null ? '' : population.ids[cell]}
    >
      <div className="cell-groups">
        <span className="panel-title">Annotated groups</span>
        <p className="cell-note muted">
          Cells per group as this recording counted them, and how many of them fired on bar{' '}
          {index + 1}. Categories overlap — a DNp20 is both a descending neuron and a decoder
          cell — so the counts need not sum to the {population.size}-cell sample.
        </p>
        <ul className="cell-group-list">
          {groups.map((group) => (
            <li
              key={group.name}
              className={group.fired === null ? '' : group.fired > 0 ? 'is-lit' : 'is-quiet'}
            >
              <span className="cell-group-name">{group.name}</span>
              <span className="cell-group-total num">{group.total} cells</span>
              <span className="cell-group-fired num">
                {group.fired === null ? 'not attributable' : `${group.fired} fired`}
              </span>
            </li>
          ))}
        </ul>
        <p className="cell-note muted">
          {arm ? `${arm.name} on bar ${index + 1}` : 'no arm'} · measured spikes, not rates.
        </p>
      </div>

      <div className="cell-detail">
        <div className="cell-head">
          <span className="panel-title">
            {cell === null ? 'No cell picked' : `Cell ${cell + 1} of ${population.size}`}
          </span>
          {cell !== null && <span className="cell-id num">id {population.ids[cell]}</span>}
          {cell !== null && (
            <span className="cell-type">{population.types[cell] || 'unannotated'}</span>
          )}
          {cell !== null &&
            groupsForType(population.types[cell], names).map((name) => (
              <span className="chip" key={name}>
                {name}
              </span>
            ))}
          <span className="cell-arm muted">{arm?.name ?? '—'}</span>
          <span className="cell-step">
            <button type="button" onClick={() => step(-1)} aria-label="Previous cell" title="Previous cell">
              ‹
            </button>
            <button type="button" onClick={() => step(1)} aria-label="Next cell" title="Next cell">
              ›
            </button>
          </span>
        </div>
        {cell === null ? (
          <p className="cell-hint">
            Click a dot in the scan to read its cell: the recording's own id and annotated type,
            and its measured spike count on every bar of the session. A cell that never fires
            draws no light of its own, so use ‹ › to step through the population to it.
          </p>
        ) : (
          <>
            <Series series={series} index={index} peak={peak} />
            <p className="cell-facts num">
              bar {index + 1}: {series[index] ?? 0} spikes · session total {total} · peak {peak} ·
              fired on {firedBars} of {series.length} bars
            </p>
            <p className="cell-note muted">
              Each point is one completed bar's measured spike count for this cell. Scan positions
              are illustrative, not anatomical, and this is not individual spike timing.
            </p>
          </>
        )}
      </div>
    </div>
  )
}
