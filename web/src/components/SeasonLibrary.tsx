import type { RecordingListing, ReportGroup } from '../lib/types'
import { duration, signedPct, tone, usd } from '../lib/format'
import './SeasonLibrary.css'

interface SeasonLibraryProps {
  listings: RecordingListing[]
  activeId: string | null
  onPick: (id: string) => void
  report: ReportGroup[] | null
}

const WIDTH = 132
const HEIGHT = 30

/** A tiny equity path for one season, drawn from the two returns it records. */
function spark(points: number[], colour: string) {
  if (points.length < 2) return null
  const low = Math.min(...points)
  const high = Math.max(...points)
  const span = high - low || 1
  const d = points
    .map((value, i) => {
      const x = (i / (points.length - 1)) * WIDTH
      const y = HEIGHT - ((value - low) / span) * (HEIGHT - 4) - 2
      return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ')
  return <path d={d} fill="none" stroke={colour} strokeWidth="2" strokeLinejoin="round" />
}

/**
 * Every recording that has been published, with the campaign-level summary above it.
 *
 * This is the experiment's reading surface: one row per season, and above them the paired
 * difference across seasons, because a single season's return is an anecdote. The sparklines
 * are drawn from the two recorded returns in the listing, not from invented data.
 */
export function SeasonLibrary({ listings, activeId, onPick, report }: SeasonLibraryProps) {
  const group = report?.[0]
  return (
    <section className="panel library">
      <div className="panel-head">
        <span className="panel-title">Seasons</span>
        <span className="panel-sub num">
          {listings.length} published · click one to load it into the floor
        </span>
      </div>

      {group && (
        <div className="library-summary">
          <div className="library-stat">
            <span className="library-stat-label">seasons</span>
            <span className="num">{group.repeats}</span>
          </div>
          <div className="library-stat">
            <span className="library-stat-label">mean paired delta</span>
            <span className={`num ${tone(group.mean_delta_pct)}`}>{signedPct(group.mean_delta_pct, 3)}</span>
          </div>
          <div className="library-stat">
            <span className="library-stat-label">spread</span>
            <span className="num">±{usd(group.delta_spread_pct, 3)}%</span>
          </div>
          <div className="library-stat">
            <span className="library-stat-label">seasons won (on/off/tie)</span>
            <span className="num">
              {group.memory_on_wins}/{group.memory_off_wins}/{group.ties}
            </span>
          </div>
          <p className="library-note">{group.label} — {report[0].engine} engine</p>
        </div>
      )}

      <div className="library-rows">
        {listings.map((listing) => {
          const on = listing.returns.gordon ?? 0
          const off = listing.returns.warren ?? 0
          return (
            <button
              type="button"
              key={listing.id}
              className={`library-row ${listing.id === activeId ? 'is-active' : ''}`}
              onClick={() => onPick(listing.id)}
            >
              <span className="library-id num">{listing.id}</span>
              <span className={`chip ${listing.engine === 'neural' ? 'chip-good' : 'chip-warn'}`}>
                {listing.engine === 'neural' ? 'neural' : 'demo'}
              </span>
              <span className="library-spark">
                <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width={WIDTH} height={HEIGHT} aria-hidden="true">
                  {spark([0, on], 'var(--amber)')}
                  {spark([0, off], 'var(--cyan)')}
                </svg>
              </span>
              <span className={`library-return num ${tone(on - off)}`}>
                Δ {signedPct(on - off, 3)}
              </span>
              <span className="library-pair num">
                <span className="up">{signedPct(on, 2)}</span>
                <span className="down">{signedPct(off, 2)}</span>
                <span className="flat">{signedPct(listing.returns.buy_and_hold ?? 0, 2)}</span>
              </span>
              <span className="library-meta num">
                {listing.bars} bars · {duration(listing.duration_seconds)} ·{' '}
                {listing.seconds_per_bar_mean?.toFixed(1)}s/bar · rewrites{' '}
                {listing.memory_changed_edges ?? '—'}
              </span>
            </button>
          )
        })}
      </div>

      <p className="library-legend">
        <span className="up">memory on</span> · <span className="down">memory off</span> ·{' '}
        <span className="flat">buy &amp; hold</span> — the sparkline joins each recording's
        start to its two returns, so it shows direction and nothing finer.
      </p>
    </section>
  )
}
