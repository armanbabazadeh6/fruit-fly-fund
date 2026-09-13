import { barClock } from '../lib/format'
import './Transport.css'

interface TransportProps {
  index: number
  bars: number
  playing: boolean
  live: boolean
  /** Live only: whether the view sits on the newest bar or a reader has stepped behind it. */
  following?: boolean
  onFollow?: () => void
  firstTimestamp: number
  timestamp: number
  onSeek: (index: number) => void
  onPlayToggle: () => void
  onStep: (delta: number) => void
  speed: number
  onSpeed: (barsPerSecond: number) => void
}

const SPEEDS = [1, 4, 12, 40]

export function Transport({
  index,
  bars,
  playing,
  live,
  following = true,
  onFollow,
  firstTimestamp,
  timestamp,
  onSeek,
  onPlayToggle,
  onStep,
  speed,
  onSpeed,
}: TransportProps) {
  const progress = bars > 1 ? (index / (bars - 1)) * 100 : 0
  return (
    <div className="transport">
      <button
        type="button"
        className="transport-btn"
        onClick={() => onStep(-1)}
        disabled={index <= 0}
        aria-label="Previous bar"
      >
        ◀
      </button>
      <button
        type="button"
        className="transport-btn transport-play"
        onClick={onPlayToggle}
        disabled={live}
        aria-label={playing ? 'Pause replay' : 'Play replay'}
      >
        {playing ? '❚❚' : '▶'}
      </button>
      <button
        type="button"
        className="transport-btn"
        onClick={() => onStep(1)}
        disabled={index >= bars - 1}
        aria-label="Next bar"
      >
        ▶
      </button>

      <div className="transport-scrub">
        <input
          type="range"
          min={0}
          max={Math.max(0, bars - 1)}
          value={index}
          onChange={(event) => onSeek(Number(event.target.value))}
          aria-label="Bar"
        />
        <span className="transport-progress" style={{ width: `${progress}%` }} aria-hidden="true" />
      </div>

      <span className="transport-readout num">
        bar {index + 1}/{bars}
        {timestamp ? ` · ${barClock(timestamp, firstTimestamp)}` : ''}
      </span>

      <div className="transport-speeds">
        {SPEEDS.map((value) => (
          <button
            key={value}
            type="button"
            className={`transport-speed ${speed === value ? 'is-active' : ''}`}
            onClick={() => onSpeed(value)}
          >
            {value}×
          </button>
        ))}
      </div>

      {live &&
        (following ? (
          <span className="chip chip-good transport-live">following the live run</span>
        ) : (
          // The season keeps growing while a reader inspects an older bar, so the way back
          // to the newest one is a control rather than an assumption.
          <button
            type="button"
            className="chip chip-warn transport-live transport-follow"
            onClick={onFollow}
          >
            behind · jump to the newest bar
          </button>
        ))}
    </div>
  )
}
