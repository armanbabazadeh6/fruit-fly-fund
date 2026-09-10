/**
 * The rivalry feed: a deterministic function of the telemetry, never a generator.
 *
 * Every sentence below is selected by a measured condition and quotes a number that came
 * from the recording. There is no randomness and no wall clock, so the same recording
 * always produces the same feed. Personification is the joke; the flies do not learn,
 * know, want or feel anything, and nothing here claims a result means memory helps.
 */

import type { CSSProperties, ReactNode } from 'react'
import type { ArmMeta, ArmSummary, Observation } from '../lib/types'
import { isNeural } from '../lib/types'
import { barClock, duration, pctValue, shortHash, signedPct, usd } from '../lib/format'
import './Commentary.css'

interface CommentaryProps {
  arms: ArmMeta[]
  summaries: Record<string, ArmSummary>
  observations: Observation[]
  index: number
  engine: 'neural' | 'procedural'
  comparison: {
    leader: string
    equity_delta_usdc: number
    return_delta_pct: number
    single_season_note: string
  }
}

interface Line {
  key: string
  tag: string
  accent?: string
  text: ReactNode
}

const MAX_LINES = 8
const MAX_BAR_LINES = 4
const SEASON_TAG = 'season'

/** Every figure that reaches the feed goes through here, so `.num` styling is lockstep. */
function Num({ children }: { children: ReactNode }) {
  return <span className="num">{children}</span>
}

export function Commentary({
  arms,
  summaries,
  observations,
  index,
  engine,
  comparison,
}: CommentaryProps) {
  const observation = observations[index]
  const seasonStart = observations.length ? observations[0].t : undefined
  const shortNames: Record<string, string | undefined> = Object.fromEntries(
    arms.map((arm) => [arm.id, arm.name.split(' ')[0]] as const),
  )
  const deploymentBars = arms.length
    ? summaries[arms[0].id]?.deployment.bars ?? observations.length
    : observations.length

  const barLines: Line[] = []
  const stimulusLines: Line[] = []
  const seasonLines: Line[] = []
  const laterLines: Line[] = []

  if (observation) {
    const barTag =
      seasonStart === undefined
        ? `bar ${observation.i}`
        : barClock(observation.t, seasonStart)

    for (const arm of arms) {
      const short = shortNames[arm.id] ?? arm.name
      const record = observation.arms[arm.id]
      const summary = summaries[arm.id]
      if (!record) continue

      const signal = record.signal
      const measured = record.decision.explanation.measured
      const execution = record.execution
      const side = record.decision.side
      // Assigning the guard's result narrows once, so the branches below stay typed.
      const neuralSignal = isNeural(signal) ? signal : null
      const proceduralSignal = signal && !isNeural(signal) ? signal : null

      if (record.portfolio.halted) {
        barLines.push({
          key: `${arm.id}-halted`,
          tag: barTag,
          accent: arm.accent,
          text: (
            <>
              {short} is out for the season: the ledger halted this account ({record.portfolio.halted}
              ). It can still be marked to market, but it cannot trade.
            </>
          ),
        })
        continue
      }

      if (side === 'BLOCKED') {
        barLines.push({
          key: `${arm.id}-blocked`,
          tag: barTag,
          accent: arm.accent,
          text: (
            <>
              {short} never got as far as an observation on bar <Num>{observation.i}</Num>: the
              guard blocked the bar first — {execution.reason ?? 'no reason recorded'}.
              {summary ? (
                <>
                  {' '}
                  That is blocked bar <Num>{summary.blocked_bars}</Num> of the season.
                </>
              ) : null}
            </>
          ),
        })
        continue
      }

      const baseUnit = observation.product.split('-')[0]

      if (execution.status === 'FILLED' && execution.fill) {
        const fill = execution.fill
        barLines.push({
          key: `${arm.id}-fill`,
          tag: barTag,
          accent: arm.accent,
          text: (
            <>
              {short} filled a <strong>{side}</strong> of <Num>{fill.base}</Num> {baseUnit} at{' '}
              <Num>{usd(fill.price)}</Num> USDC — fee <Num>{usd(fill.fee)}</Num>, equity after{' '}
              <Num>{usd(record.portfolio.equity)}</Num> USDC.
            </>
          ),
        })
      } else if (execution.status === 'VETO') {
        barLines.push({
          key: `${arm.id}-veto`,
          tag: barTag,
          accent: arm.accent,
          text: (
            <>
              {short} proposed <strong>{side}</strong> and the guard vetoed it:{' '}
              <em>{execution.reason ?? 'no reason recorded'}</em>.
              {summary ? (
                <>
                  {' '}
                  That makes <Num>{summary.vetoes}</Num> veto
                  {summary.vetoes === 1 ? '' : 'es'} this season
                  {summary.fills === 0 ? ' and no fills to show for them' : ''}.
                </>
              ) : null}
            </>
          ),
        })
      } else {
        const inside = neuralSignal
          ? typeof measured.difference_hz === 'number' &&
            typeof measured.threshold_hz === 'number'
            ? (
                <>
                  difference <Num>{measured.difference_hz.toFixed(3)}</Num> Hz inside the ±
                  <Num>{measured.threshold_hz.toFixed(2)}</Num> Hz band
                </>
              )
            : <>no measured difference was recorded</>
          : typeof measured.score === 'number' && typeof measured.threshold === 'number'
            ? (
                <>
                  score <Num>{measured.score.toFixed(3)}</Num> inside the ±
                  <Num>{measured.threshold.toFixed(2)}</Num> band
                </>
              )
            : <>no measured score was recorded</>
        barLines.push({
          key: `${arm.id}-hold`,
          tag: barTag,
          accent: arm.accent,
          text: (
            <>
              {short} sat this bar out: {inside}. Nothing to execute, nothing to pay for.
            </>
          ),
        })
      }

      if (record.stimulus.kind !== 'none') {
        const kind = record.stimulus.kind
        const spikes = neuralSignal
          ? kind === 'reward'
            ? neuralSignal.reward_spikes
            : neuralSignal.aversive_spikes
          : null
        stimulusLines.push({
          key: `${arm.id}-stimulus`,
          tag: barTag,
          accent: arm.accent,
          text: (
            <>
              A <strong>{kind}</strong> pulse of <Num>{usd(record.stimulus.delta_usdc)}</Num> USDC
              was scheduled for {short}
              {spikes !== null && neuralSignal ? (
                <>
                  , carried by <Num>{spikes}</Num> {kind} spikes out of{' '}
                  <Num>{neuralSignal.total_spikes}</Num> counted
                </>
              ) : null}
              .
            </>
          ),
        })
      }

      if (
        neuralSignal &&
        neuralSignal.memory.changed_edges !== null &&
        neuralSignal.memory.changed_edges > 0
      ) {
        laterLines.push({
          key: `${arm.id}-plasticity`,
          tag: barTag,
          accent: arm.accent,
          text: (
            <>
              The plasticity rule rewrote <Num>{neuralSignal.memory.changed_edges}</Num> edges in{' '}
              {short}'s circuit on this bar
              {neuralSignal.memory.mean_efficacy !== undefined ? (
                <>
                  {' '}
                  (mean efficacy <Num>{neuralSignal.memory.mean_efficacy.toFixed(4)}</Num>)
                </>
              ) : null}
              .
            </>
          ),
        })
      }

      if (
        proceduralSignal &&
        proceduralSignal.simulated_memory_delta !== null &&
        proceduralSignal.simulated_memory_delta !== 0
      ) {
        laterLines.push({
          key: `${arm.id}-sim-memory`,
          tag: barTag,
          accent: arm.accent,
          text: (
            <>
              {short}'s simulated memory stand-in moved its score by{' '}
              <Num>{proceduralSignal.simulated_memory_delta.toFixed(3)}</Num> — a stand-in, not a
              measurement of anything.
            </>
          ),
        })
      }

      laterLines.push({
        key: `${arm.id}-compute`,
        tag: barTag,
        accent: arm.accent,
        text: (
          <>
            {short} burned <Num>{duration(record.compute_seconds)}</Num> of wall compute on this
            bar.
          </>
        ),
      })
    }

    const statuses = arms.map((arm) => observation.arms[arm.id]?.execution.status)
    const sides = arms.map((arm) => observation.arms[arm.id]?.decision.side)
    if (
      statuses.length > 1 &&
      statuses[0] !== undefined &&
      statuses.every((status) => status === statuses[0]) &&
      sides.every((side) => side === sides[0])
    ) {
      barLines.push({
        key: 'identical-outcome',
        tag: barTag,
        text: (
          <>
            Identical scorecard this bar: both flies reported <strong>{statuses[0]}</strong> with{' '}
            <strong>{sides[0]}</strong>. Two independent accounts, one shared market, no
            disagreement.
          </>
        ),
      })
    }

    barLines.push(...stimulusLines)

    if (observation.frame_sha256) {
      barLines.push({
        key: 'shared-frame',
        tag: barTag,
        text: (
          <>
            Both flies were shown the same market frame — sha256{' '}
            <Num>{shortHash(observation.frame_sha256)}</Num>.
          </>
        ),
      })
    }

    if (engine === 'neural' && observation.same_neural_input_both_arms) {
      barLines.push({
        key: 'shared-input',
        tag: barTag,
        text: (
          <>
            Both flies were decoded from the same spike input this bar: the per-arm input hashes
            match, so neither got a private view.
          </>
        ),
      })
    }
  }

  if (arms.length) {
    const leaderArm = arms.find((arm) => arm.id === comparison.leader)
    const leaderName = leaderArm ? shortNames[leaderArm.id] ?? leaderArm.name : null
    if (comparison.equity_delta_usdc === 0 || comparison.leader === 'tie') {
      seasonLines.push({
        key: 'leader',
        tag: SEASON_TAG,
        text: (
          <>
            Dead heat on the season so far: <Num>{usd(Math.abs(comparison.equity_delta_usdc))}</Num>{' '}
            USDC between them — which is to say nothing between them.
          </>
        ),
      })
    } else if (leaderArm && leaderName) {
      seasonLines.push({
        key: 'leader',
        tag: SEASON_TAG,
        accent: leaderArm.accent,
        text: (
          <>
            {leaderName} is ahead by <Num>{usd(Math.abs(comparison.equity_delta_usdc))}</Num> USDC
            (<Num>{signedPct(comparison.return_delta_pct)}</Num> of return). Being ahead is not
            the same as being right, and one season proves nothing.
          </>
        ),
      })
    } else {
      seasonLines.push({
        key: 'leader',
        tag: SEASON_TAG,
        text: <>No leader recorded for this comparison.</>,
      })
    }
  }

  const equityBits = arms.map((arm, position) => {
    const equity = observation
      ? observation.arms[arm.id]?.portfolio.equity
      : summaries[arm.id]?.final_equity
    return (
      <span key={arm.id}>
        {position > 0 ? ' · ' : ''}
        {shortNames[arm.id] ?? arm.name} <Num>{usd(equity)}</Num>
      </span>
    )
  })

  if (arms.length) {
    seasonLines.push({
      key: 'progress',
      tag: SEASON_TAG,
      text: observation ? (
        <>
          Bar <Num>{observation.i + 1}</Num> of <Num>{deploymentBars}</Num> in the books:{' '}
          {equityBits} USDC.
        </>
      ) : (
        <>
          Season closed after <Num>{deploymentBars}</Num> bars: {equityBits} USDC.
        </>
      ),
    })
  }

  for (const arm of arms) {
    const summary = summaries[arm.id]
    const short = shortNames[arm.id] ?? arm.name
    if (!summary) continue

    if (summary.halted) {
      seasonLines.push({
        key: `${arm.id}-season-halted`,
        tag: SEASON_TAG,
        accent: arm.accent,
        text: (
          <>
            {short}'s account is halted for the rest of the season: {summary.halted}.
          </>
        ),
      })
    }

    if (summary.fills === 0) {
      seasonLines.push({
        key: `${arm.id}-no-fills`,
        tag: SEASON_TAG,
        accent: arm.accent,
        text: (
          <>
            {short} has not filled a single order — <Num>{summary.vetoes}</Num> veto
            {summary.vetoes === 1 ? '' : 'es'}, <Num>{summary.blocked_bars}</Num> blocked bars and{' '}
            <Num>{usd(summary.fees_paid)}</Num> USDC of fees to show for it.
          </>
        ),
      })
    } else {
      seasonLines.push({
        key: `${arm.id}-fills`,
        tag: SEASON_TAG,
        accent: arm.accent,
        text: (
          <>
            {short} is on <Num>{summary.fills}</Num> fill
            {summary.fills === 1 ? '' : 's'} this season and held a position for{' '}
            <Num>{pctValue(summary.deployment.holding_fraction, 1)}</Num> of it.
          </>
        ),
      })
    }

    if (
      engine === 'neural' &&
      summary.final_memory?.changed_edges !== null &&
      summary.final_memory?.changed_edges !== undefined &&
      summary.final_memory.changed_edges > 0
    ) {
      seasonLines.push({
        key: `${arm.id}-final-memory`,
        tag: SEASON_TAG,
        accent: arm.accent,
        text: (
          <>
            {short} closed the season with <Num>{summary.final_memory.changed_edges}</Num> edges
            changed by the plasticity rule{summary.final_memory.sha256 ? (
              <>
                {' '}
                (state sha256 <Num>{shortHash(summary.final_memory.sha256)}</Num>)
              </>
            ) : null}
            .
          </>
        ),
      })
    }
  }

  if (arms.length) {
    seasonLines.push({
      key: 'fees',
      tag: SEASON_TAG,
      text: (
        <>
          Fees paid so far:{' '}
          {arms.map((arm, position) => (
            <span key={arm.id}>
              {position > 0 ? ' · ' : ''}
              {shortNames[arm.id] ?? arm.name}{' '}
              <Num>{usd(summaries[arm.id]?.fees_paid)}</Num> USDC
            </span>
          ))}
          {'. Both flies pay the same rate.'}
        </>
      ),
    })
  }

  seasonLines.push({
    key: 'engine',
    tag: SEASON_TAG,
    text:
      engine === 'procedural' ? (
        <>
          This feed is reading the procedural demo engine: simulated momentum scores off the
          price path, with no connectome and no measurement of a fly's brain anywhere in the
          run.
        </>
      ) : (
        <>
          This feed is reading connectome runs: two flies, the same market frame, the same rules,
          different memory settings.
        </>
      ),
  })

  const lines: Line[] = arms.length
    ? [
        ...barLines.slice(0, MAX_BAR_LINES),
        ...seasonLines,
        ...laterLines.slice(0, 1),
        {
          key: 'fine-print',
          tag: SEASON_TAG,
          text: <>Fine print from the recording: {comparison.single_season_note}</>,
        },
      ].slice(0, MAX_LINES)
    : []

  return (
    <section className="panel cm">
      <header className="panel-head">
        <span className="panel-title">Rivalry commentary</span>
        <span className="chip cm-badge">entertainment only</span>
      </header>

      {engine === 'procedural' ? (
        <p className="cm-warn chip-warn">
          Procedural demo run — these numbers are simulated scores, not measurements from a
          connectome. Nothing on this feed measures a fly's brain.
        </p>
      ) : null}

      {lines.length ? (
        <ul className="scroll cm-feed">
          {lines.map((line) => (
            <li
              className="cm-line"
              key={line.key}
              style={line.accent ? ({ '--arm': line.accent } as CSSProperties) : undefined}
            >
              <span className="num cm-tag">{line.tag}</span>
              <span className="cm-text">{line.text}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="cm-empty muted">
          No telemetry to talk about yet. The feed lights up as soon as the first bar lands.
        </p>
      )}
    </section>
  )
}
