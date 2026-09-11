import type { RecordingListing, ReportGroup, Source } from '../lib/types'
import { duration, signedPct, usd } from '../lib/format'
import './TopBar.css'

interface TopBarProps {
  /** Present only while a run is in flight. */
  progress?: { done: number; total: number; etaSeconds: number | null } | null
  source: Source
  listings: RecordingListing[]
  activeId: string | null
  onPick: (id: string) => void
  onRun: (options: {
    engine: 'neural' | 'procedural'
    market: 'synthetic' | 'coinbase'
    bars: number
    repeats: number
    require_gate?: boolean
    daily_orders?: number
  }) => void
  running: boolean
  streamConnected: boolean
  report: ReportGroup[] | null
}

export function TopBar({
  progress,
  source,
  listings,
  activeId,
  onPick,
  onRun,
  running,
  streamConnected,
  report,
}: TopBarProps) {
  const group = report?.[0]
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          🪰
        </span>
        <span>
          <span className="brand-name">fruit fly fund<span style={{color:'#c7e8ad'}}>®</span></span>
          <span className="brand-sub">
            THE SMALLEST DESK ON WALL STREET
          </span>
        </span>
      </div>

      <div className={`source-badge source-${source.mode} ${source.neural ? '' : 'source-sim'}`}>
        <span className="source-dot" aria-hidden="true" />
        <span className="source-label">{source.label}</span>
        <span className="source-detail">{source.detail}</span>
      </div>

      <div className="topbar-controls">
        {listings.length > 0 && (
          <label className="pick">
            <span className="pick-label">Recording</span>
            <select
              className="pick-select"
              value={activeId ?? ''}
              onChange={(event) => onPick(event.target.value)}
            >
              {listings.map((listing) => (
                <option key={listing.id} value={listing.id}>
                  {listing.engine === 'neural' ? '🧠' : '🧪'} {listing.label} · {listing.bars} bars ·{' '}
                  {listing.id.slice(-14)}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="run-buttons">
          <button
            type="button"
            className="btn btn-primary"
            disabled={running || !streamConnected}
            onClick={() => onRun({ engine: 'neural', market: 'coinbase', bars: 24, repeats: 1 })}
            title="Runs the real MaleCNS engine. About 6 s per bar on an M2 laptop."
          >
            {running ? 'Flies trading…' : 'Run 24 neural bars'}
          </button>
          <button
            type="button"
            className="btn"
            disabled={running || !streamConnected}
            onClick={() =>
              onRun({
                engine: 'neural',
                market: 'coinbase',
                bars: 24,
                repeats: 1,
                require_gate: false,
                daily_orders: 100,
              })
            }
            title="Same engine with the DNpe017 gate off: every bar produces an order instead of half of them."
          >
            Busy run (gate off)
          </button>
          <button
            type="button"
            className="btn"
            disabled={running || !streamConnected}
            onClick={() =>
              onRun({
                engine: 'procedural',
                market: 'synthetic',
                bars: 240,
                repeats: 1,
              })
            }
            title="Procedural demo: builds and tests the interface. Not neural activity."
          >
            Procedural demo
          </button>
        </div>
      </div>

      {progress && (
        <div className="topbar-progress">
          <span className="topbar-progress-track">
            <span
              className="topbar-progress-fill"
              style={{ width: `${progress.total ? (progress.done / progress.total) * 100 : 0}%` }}
            />
          </span>
          <span className="num">
            bar {progress.done}/{progress.total}
            {progress.etaSeconds !== null && progress.etaSeconds > 0
              ? ` · ~${duration(progress.etaSeconds)} left`
              : ''}
          </span>
        </div>
      )}

      {group && (
        <p className="topbar-report num">
          across <strong>{group.repeats}</strong> season{group.repeats === 1 ? '' : 's'}: memory-on{' '}
          <strong>{group.memory_on_wins}</strong> · memory-off{' '}
          <strong>{group.memory_off_wins}</strong> · ties <strong>{group.ties}</strong> · mean paired
          delta <strong>{signedPct(group.mean_delta_pct, 3)}</strong> ±{' '}
          {usd(group.delta_spread_pct, 3)}% spread
        </p>
      )}
      {listings.length > 0 && activeId && (
        <p className="topbar-note num">
          {(() => {
            const listing = listings.find((entry) => entry.id === activeId)
            if (!listing) return null
            return (
              <>
                {listing.engine === 'neural' ? 'neural engine' : 'procedural demo'} ·{' '}
                {listing.season} · {duration(listing.duration_seconds)} compute ·{' '}
                {listing.seconds_per_bar_mean?.toFixed(1)}s per bar · two arms, identical input{' '}
                {listing.inputs_identical_every_bar ? '✓' : '✗'}
              </>
            )
          })()}
        </p>
      )}
    </header>
  )
}
