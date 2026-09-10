import type { ArmMeta } from '../lib/types'
import { usd, signedPct, tone } from '../lib/format'
import { linePath, paddedExtent, ticks } from '../lib/series'
import { useMeasuredWidth, useScrub } from '../lib/hooks'
import './EquityChart.css'

interface EquityChartProps {
  arms: ArmMeta[]
  curve: number[]
  controlCurve: number[]
  buyAndHold: number[]
  cash: number[]
  initialCapital: number
  bars: number
  selectedIndex: number
  onSelect: (index: number) => void
  visibleBars?: number
  provisional?: boolean
  /** Every fill in the season, so the chart shows when trading actually happened. */
  fills?: { id: string; i: number; side: string; value: number }[]
}

const HEIGHT = 300
const PAD = { top: 16, right: 74, bottom: 26, left: 12 }

/**
 * Equity against the two benchmarks. Drawn as SVG rather than with a chart library: the
 * series are at most a few hundred points, and drawing them directly keeps the stroke
 * weights, grid and selection marker under the same design tokens as the rest of the page.
 */
export function EquityChart({
  arms,
  curve,
  controlCurve,
  buyAndHold,
  cash,
  initialCapital,
  bars,
  selectedIndex,
  onSelect,
  visibleBars,
  provisional,
  fills = [],
}: EquityChartProps) {
  const [ref, width] = useMeasuredWidth<HTMLDivElement>()
  const series = [
    { key: arms[0]?.id ?? 'on', label: arms[0]?.name ?? 'memory on', color: arms[0]?.accent ?? 'var(--amber)', values: curve, dash: '' },
    { key: arms[1]?.id ?? 'off', label: arms[1]?.name ?? 'memory off', color: arms[1]?.accent ?? 'var(--cyan)', values: controlCurve, dash: '' },
    { key: 'buy_and_hold', label: 'Buy & hold', color: 'var(--violet)', values: buyAndHold, dash: '5 4' },
    { key: 'cash', label: 'Cash', color: 'var(--ink-4)', values: cash, dash: '2 4' },
  ]
  const shown = Math.max(2, Math.min(bars, visibleBars ?? bars))
  const plotted = series.map((entry) => ({
    ...entry,
    values: entry.values.length ? entry.values.slice(0, shown) : [initialCapital, initialCapital],
  }))
  const [low, high] = paddedExtent([
    ...plotted.flatMap((entry) => entry.values),
    initialCapital,
  ])
  const plotWidth = Math.max(80, width - PAD.left - PAD.right)
  const plotHeight = HEIGHT - PAD.top - PAD.bottom
  const x = (index: number) => PAD.left + (index / Math.max(1, shown - 1)) * plotWidth
  const y = (value: number) => PAD.top + ((high - value) / (high - low)) * plotHeight
  const scrub = useScrub(width, { left: PAD.left, right: PAD.right }, shown, onSelect)
  const markerX = x(selectedIndex)

  return (
    <section className="panel equity">
      <div className="panel-head">
        <span className="panel-title">
          Equity · USDC per bar
          {provisional && <span className="chip chip-warn equity-live">streaming</span>}
        </span>
        <span className="panel-sub num">
          start {usd(initialCapital)} · {shown} of {bars} bars
        </span>
      </div>

      <div className="equity-legend">
        {plotted.map((entry) => {
          const value = entry.values[Math.min(selectedIndex, entry.values.length - 1)] ?? initialCapital
          const pct = initialCapital ? (value / initialCapital - 1) * 100 : 0
          return (
            <span className="equity-legend-item" key={entry.key}>
              <span className="equity-swatch" style={{ background: entry.color, opacity: entry.dash ? 0.8 : 1 }} />
              <span className="equity-legend-name">{entry.label}</span>
              <span className="num equity-legend-value">{usd(value)}</span>
              <span className={`num equity-legend-delta ${tone(pct)}`}>{signedPct(pct, 2)}</span>
            </span>
          )
        })}
      </div>

      <div className="equity-plot" ref={ref}>
        <svg
          viewBox={`0 0 ${width} ${HEIGHT}`}
          width="100%"
          height={HEIGHT}
          role="img"
          aria-label="Equity per bar for both flies, buy and hold, and cash"
          {...scrub}
        >
          {ticks(low, high, 4).map((value) => (
            <g key={value}>
              <line x1={PAD.left} x2={PAD.left + plotWidth} y1={y(value)} y2={y(value)} stroke="var(--line)" strokeWidth="1" />
              <text x={PAD.left + plotWidth + 8} y={y(value) + 4} className="equity-tick num">
                {usd(value, 2)}
              </text>
            </g>
          ))}

          <line
            x1={PAD.left}
            x2={PAD.left + plotWidth}
            y1={y(initialCapital)}
            y2={y(initialCapital)}
            stroke="var(--line-2)"
            strokeWidth="1"
            strokeDasharray="3 3"
          />

          {plotted.map((entry) => (
            <path
              key={entry.key}
              d={linePath(entry.values, x, y)}
              fill="none"
              stroke={entry.color}
              strokeWidth={entry.dash ? 1.4 : 2}
              strokeDasharray={entry.dash || undefined}
              strokeLinejoin="round"
              opacity={entry.dash ? 0.85 : 1}
            />
          ))}

          <line
            x1={markerX}
            x2={markerX}
            y1={PAD.top - 6}
            y2={PAD.top + plotHeight}
            stroke="var(--ink)"
            strokeWidth="1"
            opacity="0.5"
          />
          {plotted.slice(0, 2).map((entry) => {
            const value = entry.values[Math.min(selectedIndex, entry.values.length - 1)]
            if (!Number.isFinite(value)) return null
            return (
              <circle key={entry.key} cx={markerX} cy={y(value)} r="3.2" fill={entry.color} stroke="var(--bg)" strokeWidth="1.4" />
            )
          })}

          {/* Fills: a triangle per trade, pointing the way the order went. */}
          {fills
            .filter((fill) => fill.i >= 0 && fill.i < shown && Number.isFinite(fill.value))
            .map((fill, index) => {
              const colour =
                plotted.find((entry) => entry.key === fill.id)?.color ?? 'var(--ink-2)';
              const cx = x(fill.i);
              const cy = y(fill.value);
              const buy = fill.side === 'BUY';
              const tip = buy ? cy - 9 : cy + 9;
              const base = buy ? cy - 2 : cy + 2;
              return (
                <polygon
                  key={`${fill.id}-${fill.i}-${index}`}
                  points={`${cx},${tip} ${cx - 4},${base} ${cx + 4},${base}`}
                  fill={colour}
                  stroke="var(--bg)"
                  strokeWidth="1"
                  opacity="0.95"
                />
              );
            })}

          {[0, Math.floor((shown - 1) / 2), shown - 1].map((index) => (
            <text key={index} x={x(index)} y={HEIGHT - 8} textAnchor="middle" className="equity-tick num">
              bar {index + 1}
            </text>
          ))}
        </svg>
      </div>

      <p className="equity-note">
        Buy &amp; hold and cash are context on the same bars and fee. Buy &amp; hold obeys no
        order-size, cooldown or daily-order limit; the flies obey all three.
      </p>
    </section>
  )
}
