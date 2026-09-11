import { linePath, paddedExtent, ticks } from '../lib/series'
import { useMeasuredWidth, useScrub } from '../lib/hooks'
import { usd } from '../lib/format'
import './PriceChart.css'

interface PriceChartProps {
  times: number[]
  mids: number[]
  selectedIndex: number
  onSelect: (index: number) => void
  product: string
  visibleBars?: number
}

const HEIGHT = 132
const PAD = { top: 12, right: 74, bottom: 20, left: 12 }

function clockLabel(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString('en-GB', {
    timeZone: 'UTC',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** The market the flies were shown. No indicators, no predictions: just the closes. */
export function PriceChart({
  times,
  mids,
  selectedIndex,
  onSelect,
  product,
  visibleBars,
}: PriceChartProps) {
  const [ref, width] = useMeasuredWidth<HTMLDivElement>()
  const shown = Math.max(1, Math.min(mids.length || 1, visibleBars ?? mids.length ?? 1))
  const values = mids.length ? mids.slice(0, shown) : [0, 0]
  const [low, high] = paddedExtent(values, 0.12)
  const plotWidth = Math.max(80, width - PAD.left - PAD.right)
  const plotHeight = HEIGHT - PAD.top - PAD.bottom
  const x = (index: number) => PAD.left + (index / Math.max(1, shown - 1)) * plotWidth
  const y = (value: number) => PAD.top + ((high - value) / (high - low || 1)) * plotHeight
  const scrub = useScrub(width, { left: PAD.left, right: PAD.right }, shown, onSelect)
  const markerX = x(Math.min(selectedIndex, values.length - 1))
  const current = values[Math.min(selectedIndex, values.length - 1)]
  const first = values[0]
  const changePct = first ? (current / first - 1) * 100 : 0

  return (
    <section className="panel price" ref={ref}>
      <div className="panel-head">
        <span className="panel-title">{product} · mid price per bar</span>
        <span className="panel-sub num">
          {usd(current)} {product.split('-').at(-1) ?? 'USD'}{' '}
          <span className={changePct >= 0 ? 'up' : 'down'}>
            {changePct >= 0 ? '+' : '−'}
            {Math.abs(changePct).toFixed(3)}%
          </span>{' '}
          over {shown} bars
        </span>
      </div>
      <div className="price-plot">
        <svg
          viewBox={`0 0 ${width} ${HEIGHT}`}
          width="100%"
          height={HEIGHT}
          role="img"
          aria-label={`${product} mid price per bar`}
          {...scrub}
        >
          {ticks(low, high, 3).map((value) => (
            <g key={value}>
              <line x1={PAD.left} x2={PAD.left + plotWidth} y1={y(value)} y2={y(value)} stroke="var(--line)" strokeWidth="1" />
              <text x={PAD.left + plotWidth + 8} y={y(value) + 4} className="price-tick num">
                {usd(value, 0)}
              </text>
            </g>
          ))}
          <path d={linePath(values, x, y)} fill="none" stroke="var(--ink-2)" strokeWidth="1.6" />
          <line
            x1={markerX}
            x2={markerX}
            y1={PAD.top - 4}
            y2={PAD.top + plotHeight}
            stroke="var(--amber)"
            strokeWidth="1"
          />
          <circle cx={markerX} cy={y(current)} r="3" fill="var(--amber)" stroke="var(--bg)" strokeWidth="1.3" />
          {[...new Set([0, shown - 1])].map((index) => (
            <text
              key={index}
              x={x(index)}
              y={HEIGHT - 6}
              textAnchor={index === 0 ? 'start' : 'end'}
              className="price-tick num"
            >
              {times[index] ? clockLabel(times[index]) : ''}
            </text>
          ))}
        </svg>
      </div>
    </section>
  )
}
