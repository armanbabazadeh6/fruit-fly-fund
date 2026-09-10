import { useId, type CSSProperties } from 'react'
import type { ArmMeta, ArmObservation, ExecutionStatus, Side } from '../lib/types'
import { signedPct, tone } from '../lib/format'
import { Fly, type FlyMood } from './Fly'
import './Desk.css'

interface DeskProps {
  arm: ArmMeta
  observation: ArmObservation | null
  mood: FlyMood
  curve: number[]
  curveIndex: number
  live?: boolean
}

/* Scene is laid out in a 320x180 viewBox; the fly is a second layer over it (see Desk.css). */
const VIEW_W = 320
const VIEW_H = 180
const PLOT = { x: 32, y: 22, w: 92, h: 58 }

const STATUS_CHIP: Record<ExecutionStatus, string> = {
  FILLED: 'chip-good',
  VETO: 'chip-warn',
  HOLD: '',
  BLOCKED: 'chip-bad',
}

const SIDE_TONE: Record<Side, string> = { BUY: 'up', SELL: 'down', HOLD: 'flat', BLOCKED: 'flat' }

/** Geometry for the monitor sparkline, scaled to the slice's own range so it always fills the screen. */
function sparkGeometry(slice: number[]) {
  const n = slice.length
  if (n === 0) return null
  const min = Math.min(...slice)
  const max = Math.max(...slice)
  const span = max - min
  const yOf = (v: number) => (span === 0 ? PLOT.y + PLOT.h / 2 : PLOT.y + PLOT.h - ((v - min) / span) * PLOT.h)
  // One bar has no slope to draw, so it becomes a flat line at that bar's own value.
  const pts =
    n === 1
      ? [
          { x: PLOT.x, y: yOf(slice[0]) },
          { x: PLOT.x + PLOT.w, y: yOf(slice[0]) },
        ]
      : slice.map((v, i) => ({ x: PLOT.x + (i / (n - 1)) * PLOT.w, y: yOf(v) }))
  const line = pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const area = `${PLOT.x},${PLOT.y + PLOT.h} ${line} ${PLOT.x + PLOT.w},${PLOT.y + PLOT.h}`
  return { line, area, last: pts[pts.length - 1] }
}

/** Two desks share one page: a deterministic per-arm phase keeps the two flies out of lockstep. */
function flapPhase(id: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return -(h % 16) * 0.07
}

export function Desk({ arm, observation, mood, curve, curveIndex, live }: DeskProps) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')
  const glowId = `desk-glow-${uid}`
  const stageStyle = { '--fly-delay': `${flapPhase(arm.id)}s` } as CSSProperties
  const halted = mood === 'halted'
  const bars = Math.max(0, Math.min(curveIndex + 1, curve.length))
  const spark = sparkGeometry(curve.slice(0, bars))

  return (
    <section className={`panel desk${halted ? ' desk--halted' : ''}`}>
      <header className="panel-head">
        <h3 className="desk__name">{arm.name}</h3>
        <span className="desk__chips">
          {live && <span className="desk__live">live</span>}
          <span className="chip">{arm.role_label}</span>
          {halted && <span className="chip chip-bad">halted</span>}
        </span>
      </header>

      {observation ? (
        <div className="desk__strip">
          <span className="desk__cell">
            <span className="desk__label">side</span>
            <span className={`desk__side ${SIDE_TONE[observation.decision.side]}`}>
              {observation.decision.side}
            </span>
          </span>
          <span className="desk__cell">
            <span className="desk__label">exec</span>
            <span className={`chip ${STATUS_CHIP[observation.execution.status]}`}>
              {observation.execution.status}
            </span>
          </span>
          <span className="desk__cell">
            <span className="desk__label">return</span>
            <span className={`num desk__ret ${tone(observation.portfolio.return_pct)}`}>
              {signedPct(observation.portfolio.return_pct)}
            </span>
          </span>
        </div>
      ) : (
        <p className="desk__waiting muted">waiting for the first bar</p>
      )}

      <div className="desk__stage" style={stageStyle}>
        <svg className="desk__art" viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} aria-hidden="true">
          <defs>
            <radialGradient id={glowId} cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor={arm.accent} stopOpacity="0.3" />
              <stop offset="55%" stopColor={arm.accent} stopOpacity="0.09" />
              <stop offset="100%" stopColor={arm.accent} stopOpacity="0" />
            </radialGradient>
          </defs>

          <circle cx="272" cy="64" r="140" fill={`url(#${glowId})`} />

          {/* monitor */}
          <rect className="desk__monitor" x="20" y="10" width="116" height="82" rx="6" />
          <rect className="desk__screen" x="26" y="16" width="104" height="70" rx="3" />
          <line className="desk__grid" x1={PLOT.x} y1={PLOT.y + PLOT.h / 3} x2={PLOT.x + PLOT.w} y2={PLOT.y + PLOT.h / 3} />
          <line
            className="desk__grid"
            x1={PLOT.x}
            y1={PLOT.y + (PLOT.h * 2) / 3}
            x2={PLOT.x + PLOT.w}
            y2={PLOT.y + (PLOT.h * 2) / 3}
          />
          <line
            className="desk__axis"
            x1={PLOT.x}
            y1={PLOT.y + PLOT.h}
            x2={PLOT.x + PLOT.w}
            y2={PLOT.y + PLOT.h}
          />
          {spark ? (
            <>
              <polygon className="desk__spark-area" points={spark.area} />
              <polyline className="desk__spark" points={spark.line} />
              <circle className="desk__spark-dot" cx={spark.last.x} cy={spark.last.y} r="2.4" />
            </>
          ) : (
            // No completed bar yet: a dashed placeholder, deliberately not a value.
            <line
              className="desk__spark-empty"
              x1={PLOT.x}
              y1={PLOT.y + PLOT.h / 2}
              x2={PLOT.x + PLOT.w}
              y2={PLOT.y + PLOT.h / 2}
            />
          )}
          <rect className="desk__stand" x="70" y="92" width="16" height="19" />
          <rect className="desk__stand" x="58" y="111" width="44" height="7" rx="3.5" />

          {/* desk */}
          <rect className="desk__top" x="4" y="118" width="312" height="9" />
          <rect className="desk__front" x="4" y="127" width="312" height="45" />
          <rect className="desk__edge" x="4" y="127" width="312" height="1" />
          <ellipse cx="272" cy="122" rx="62" ry="8" fill={arm.accent} opacity="0.12" />

          {/* coffee */}
          <path className="desk__cup" d="M19 96 L23 115 Q23.4 118 26.5 118 L37.5 118 Q40.6 118 41 115 L45 96 Z" />
          <ellipse className="desk__cup-rim" cx="32" cy="96" rx="13" ry="4.5" />
          <ellipse className="desk__coffee" cx="32" cy="96.6" rx="10.2" ry="3.1" />
          <path className="desk__cup-handle" d="M45 101 q9 1 8 7 q-1 6 -9 6" />
          <g className="desk__steam-group">
            <path className="desk__steam" d="M28 92 C 25 86 31 82 28 75" />
            <path className="desk__steam" d="M33 92 C 30 85 36 81 33 73" />
            <path className="desk__steam" d="M38 92 C 35 86 41 82 38 75" />
          </g>

          {/* lamp */}
          <rect className="desk__lamp" x="250" y="112" width="48" height="6" rx="3" />
          <path className="desk__lamp-stem" d="M274 112 V 74" />
          <g transform="rotate(12 274 60)">
            <path className="desk__shade" d="M262 44 L286 44 L298 74 L250 74 Z" />
            <ellipse className="desk__bulb" cx="274" cy="74" rx="18" ry="5" fill={arm.accent} />
          </g>
        </svg>

        <div className="desk__fly">
          <Fly accent={arm.accent} mood={mood} size={132} label={arm.name} />
        </div>
      </div>
    </section>
  )
}
