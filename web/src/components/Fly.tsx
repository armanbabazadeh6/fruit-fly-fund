import './Fly.css'

/** Poses a fly can hold. The parent derives this from telemetry — the fly never infers it. */
export type FlyMood = 'idle' | 'buy' | 'sell' | 'veto' | 'blocked' | 'halted'

interface FlyProps {
  accent: string
  mood: FlyMood
  size?: number
  label?: string
}

const VIEW_W = 140
const VIEW_H = 110

/** Pose in words, for the accessible name only. */
const MOOD_WORDS: Record<FlyMood, string> = {
  idle: 'idle pose',
  buy: 'buy bar, wings beating fast',
  sell: 'sell bar, wings beating fast',
  veto: 'veto bar, wings half tucked',
  blocked: 'blocked bar, wings half tucked',
  halted: 'halted, wings still',
}

/* The left wing, pinned at (58,50); the right wing is this same shape mirrored about x = 70. */
const WING = (
  <g className="fly__wing">
    <ellipse className="fly__wing-pane" cx="42" cy="32" rx="24" ry="10.5" transform="rotate(38 42 32)" />
    <path className="fly__wing-vein" d="M58 49 Q 46 36 26 22" />
  </g>
)

const LEGS = ['M60 62 L47 74 L42 88', 'M55 72 L40 84 L38 97', 'M56 82 L44 96 L46 106']

export function Fly({ accent, mood, size = VIEW_W, label }: FlyProps) {
  const height = Math.round((size * VIEW_H) / VIEW_W)
  /* One compound eye — accent-rimmed facets, glint and antenna — mirrored for the right side. */
  const eye = (
    <g>
      <ellipse className="fly__eye" cx="61" cy="33" rx="8.6" ry="9.6" transform="rotate(-12 61 33)" fill={accent} />
      <path className="fly__eye-facet" d="M55 40 Q 61 35 68 29" />
      <path className="fly__eye-facet" d="M53 33 Q 60 28 67 22" />
      <ellipse className="fly__eye-glint" cx="58.2" cy="28.6" rx="2.5" ry="3.1" transform="rotate(-12 58.2 28.6)" />
      <path className="fly__antenna" d="M63 19 Q 58 11 51 8" />
    </g>
  )
  return (
    <svg
      className={`fly fly--${mood}`}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      width={size}
      height={height}
      role="img"
      aria-label={`${label ? `${label} — ` : 'fly — '}${MOOD_WORDS[mood]}`}
    >
      <g className="fly__body">
        {WING}
        <g transform={`translate(${VIEW_W} 0) scale(-1 1)`}>{WING}</g>

        <g className="fly__legs">
          {LEGS.map((d) => (
            <path key={d} className="fly__leg" d={d} />
          ))}
        </g>
        <g transform={`translate(${VIEW_W} 0) scale(-1 1)`}>
          {LEGS.map((d) => (
            <path key={d} className="fly__leg" d={d} />
          ))}
        </g>

        <ellipse className="fly__abdomen" cx="70" cy="88" rx="11.5" ry="12.5" />
        <ellipse className="fly__thorax" cx="70" cy="64" rx="17.5" ry="20" />
        <circle className="fly__head" cx="70" cy="34" r="16" />
        {eye}
        <g transform={`translate(${VIEW_W} 0) scale(-1 1)`}>{eye}</g>
      </g>
    </svg>
  )
}
