/**
 * Every paper fill either fly booked this season, merged into one table in bar order so the
 * two rivals' rows interleave. P&L is never recomputed here: `equity_after` is the ledger's
 * own recorded value and is rendered as-is.
 */

import type { CSSProperties } from 'react'
import type { ArmMeta, ArmSummary } from '../lib/types'
import { barClock, clock, pctValue, usd } from '../lib/format'
import './TradeHistory.css'

interface TradeHistoryProps {
  arms: ArmMeta[]
  summaries: Record<string, ArmSummary>
  selectedIndex: number
  onSelectBar: (i: number) => void
  /** Season-origin timestamp for barClock; without it rows fall back to absolute UTC time. */
  firstBarT?: number
}

export function TradeHistory({
  arms,
  summaries,
  selectedIndex,
  onSelectBar,
  firstBarT,
}: TradeHistoryProps) {
  const rows = arms
    .flatMap((arm, armIndex) =>
      (summaries[arm.id]?.trades ?? []).map((trade) => ({ trade, arm, armIndex })),
    )
    .sort((a, b) => a.trade.i - b.trade.i || a.armIndex - b.armIndex)

  return (
    <section className="panel th">
      <header className="panel-head">
        <span className="panel-title">Trade history</span>
        <span className="panel-sub">
          <span className="num">{rows.length}</span> fills recorded ·{' '}
          {firstBarT === undefined ? 'absolute UTC time' : 'market time from season open'}
        </span>
      </header>

      <div className="th-heads">
        {arms.map((arm) => {
          const summary = summaries[arm.id]
          const style = { '--arm': arm.accent } as CSSProperties
          return (
            <article className="th-head" key={arm.id} style={style}>
              <div className="th-head-top">
                <span className="th-name">{arm.name}</span>
                <span className="muted th-role">{arm.role_label}</span>
                {summary?.halted ? <span className="chip chip-warn">halted</span> : null}
              </div>
              {summary ? (
                <>
                  <dl className="th-stats">
                    <div>
                      <dt>fills</dt>
                      <dd className="num">{summary.fills}</dd>
                    </div>
                    <div>
                      <dt>vetoes</dt>
                      <dd className="num">{summary.vetoes}</dd>
                    </div>
                    <div>
                      <dt>fees paid</dt>
                      <dd className="num">{usd(summary.fees_paid)}</dd>
                    </div>
                    <div>
                      <dt>holding</dt>
                      <dd className="num">{pctValue(summary.deployment.holding_fraction, 1)}</dd>
                    </div>
                  </dl>
                  {summary.trades.length === 0 ? (
                    <p className="th-empty muted">
                      No fills recorded.{' '}
                      {summary.vetoes > 0
                        ? `The execution guard vetoed ${summary.vetoes} proposal${
                            summary.vetoes === 1 ? '' : 's'
                          }`
                        : 'No proposal reached the book'}{' '}
                      across <span className="num">{summary.deployment.bars}</span> bars
                      {summary.vetoes === 0 && summary.blocked_bars > 0 ? (
                        <>
                          , <span className="num">{summary.blocked_bars}</span> of them blocked
                          before any observation
                        </>
                      ) : null}
                      . Cash-only is a valid outcome
                      {summary.halted ? `; the account was then halted: ${summary.halted}` : ''}.
                    </p>
                  ) : (
                    <p className="th-note muted">
                      Holding a position on{' '}
                      <span className="num">{summary.deployment.bars_holding}</span> of{' '}
                      <span className="num">{summary.deployment.bars}</span> bars.
                    </p>
                  )}
                </>
              ) : (
                <p className="th-empty muted">No summary recorded for this arm.</p>
              )}
            </article>
          )
        })}
      </div>

      {rows.length === 0 ? (
        <p className="th-none muted">
          Neither fly booked a fill in this season. The guard can veto every proposal, and
          neither fly can short — cash-only is a valid outcome.
        </p>
      ) : (
        <div className="scroll th-scroll">
          <table className="th-table">
            <thead>
              <tr>
                <th scope="col">fly</th>
                <th scope="col">bar</th>
                <th scope="col">time</th>
                <th scope="col">side</th>
                <th scope="col" className="th-right">
                  size
                </th>
                <th scope="col" className="th-right">
                  price
                </th>
                <th scope="col" className="th-right">
                  notional
                </th>
                <th scope="col" className="th-right">
                  fee
                </th>
                <th scope="col" className="th-right">
                  equity after
                </th>
                <th scope="col">reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ trade, arm }) => {
                const selected = trade.i === selectedIndex
                return (
                  <tr
                    key={`${arm.id}-${trade.i}`}
                    className={selected ? 'th-row th-selected' : 'th-row'}
                    style={{ '--arm': arm.accent } as CSSProperties}
                  >
                    <td className="th-fly">
                      <span className="th-dot" aria-hidden="true" />
                      {arm.name}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="th-bar num"
                        onClick={() => onSelectBar(trade.i)}
                        aria-current={selected ? 'true' : undefined}
                      >
                        {trade.i}
                      </button>
                    </td>
                    <td className="num th-time">
                      {firstBarT === undefined ? clock(trade.t) : barClock(trade.t, firstBarT)}
                    </td>
                    <td className={trade.side === 'BUY' ? 'up' : 'down'}>{trade.side}</td>
                    <td className="num th-right">{trade.base_size}</td>
                    <td className="num th-right">{usd(trade.price)}</td>
                    <td className="num th-right">{usd(trade.quote_size)}</td>
                    <td className="num th-right">{usd(trade.fee)}</td>
                    <td className="num th-right">{usd(trade.equity_after)}</td>
                    <td className="th-reason muted">{trade.reason}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
