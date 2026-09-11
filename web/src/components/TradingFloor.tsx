import { useEffect, useMemo, useRef, useState } from 'react'
import type { ArmMeta, ArmSummary, Observation, FlyMood } from '../lib/types'
import { isNeural } from '../lib/types'
import { compact, signedPct, tone, usd } from '../lib/format'
import { Desk } from './Desk'
import { moodFor } from '../lib/mood'
import type { FloorArmState, FloorHandle, FloorState } from '../three/floor'
import './TradingFloor.css'

interface TradingFloorProps {
  arms: ArmMeta[]
  summaries: Record<string, ArmSummary>
  observations: Observation[]
  seasonBars: { t: number; mid: number; bid: string; ask: string }[]
  index: number
  engine: 'neural' | 'procedural'
  live: boolean
  initialCapital: string
  product: string
}

function webglAvailable(): boolean {
  try {
    const probe = document.createElement('canvas')
    return Boolean(
      probe.getContext('webgl2') ??
        probe.getContext('webgl') ??
        probe.getContext('experimental-webgl'),
    )
  } catch {
    return false
  }
}

/** The one-line signal summary, and the memory state, for a desk terminal. */
function deskLines(arm: ArmMeta, observation: Observation | null) {
  const signal = observation?.arms?.[arm.id]?.signal
  const decision = observation?.arms?.[arm.id]?.decision
  if (!signal) {
    return {
      signalLine: 'no observation',
      memoryLine: '—',
      reason:
        decision?.side === 'BLOCKED'
          ? observation?.arms?.[arm.id]?.execution.reason ?? 'guard blocked the bar'
          : 'waiting for the first bar',
    }
  }
  if (isNeural(signal)) {
    return {
      signalLine: `${signal.difference_hz >= 0 ? '+' : '−'}${Math.abs(signal.difference_hz).toFixed(1)}Hz g${signal.gate_spikes}`,
      memoryLine: signal.memory.enabled
        ? `${compact(signal.memory.changed_edges)} eff ${signal.memory.mean_efficacy?.toFixed(5)}x`
        : `frozen · ${compact(signal.memory.plastic_edges)} eff`,
      reason: decision?.explanation.steps.at(-1) ?? '',
    }
  }
  return {
    signalLine: `${signal.score >= 0 ? '+' : '−'}${Math.abs(signal.score).toFixed(2)}/±${signal.threshold.toFixed(2)}`,
    memoryLine: signal.simulated_memory_delta === null
      ? 'not applied'
      : `simulated ${signal.simulated_memory_delta.toFixed(4)}`,
    reason: 'procedural demo signal — not a neural measurement',
  }
}

export function TradingFloor({
  arms,
  summaries,
  observations,
  seasonBars,
  index,
  engine,
  live,
  initialCapital,
  product,
}: TradingFloorProps) {
  const stageRef = useRef<HTMLDivElement | null>(null)
  const floorRef = useRef<FloorHandle | null>(null)
  // The scene loads asynchronously, so it needs the latest state the moment it exists —
  // otherwise a paused recording would sit there with blank terminals until something
  // changed.
  const stateRef = useRef<FloorState | null>(null)
  const [fallback, setFallback] = useState(false)
  const [loading, setLoading] = useState(true)
  const [pixel, setPixel] = useState(false)
  const [tradeCam, setTradeCam] = useState(false)
  const [brain, setBrain] = useState(false)
  const [view, setView] = useState<'floor' | 'gordon' | 'warren'>('floor')

  const observation = observations[index] ?? null
  const mid = seasonBars[index]?.mid ?? 0
  const bars = Math.max(1, seasonBars.length || observations.length)

  const state: FloorState = useMemo(() => {
    const armStates: FloorArmState[] = arms.map((arm, position) => {
      const summary = summaries[arm.id]
      const entry = observation?.arms?.[arm.id] ?? null
      const { signalLine, memoryLine, reason } = deskLines(arm, observation)
      const curve = summary?.curve ?? []
      const trail = observations.slice(0, index + 1)
      const events = trail
        .filter((entry) => (entry.arms?.[arm.id]?.execution.status ?? 'HOLD') !== 'HOLD')
        .slice(-6)
        .reverse()
        .map((entry) => {
          const armEntry = entry.arms[arm.id]
          const fill = armEntry.execution.fill
          const status = armEntry.execution.status
          return {
            side: armEntry.decision.side,
            label: fill
              ? `${armEntry.decision.side} ${fill.base} @ ${fill.price}`
              : `${armEntry.decision.side} ${status}`,
          }
        })
      const lastFillEntry = [...trail]
        .reverse()
        .find((entry) => entry.arms?.[arm.id]?.execution.status === 'FILLED' && entry.arms[arm.id].execution.fill)
      const lastFill = lastFillEntry
        ? {
            i: lastFillEntry.i,
            side: lastFillEntry.arms[arm.id].decision.side,
            base: lastFillEntry.arms[arm.id].execution.fill!.base,
            price: lastFillEntry.arms[arm.id].execution.fill!.price,
          }
        : null
      return {
        id: arm.id,

        name: arm.name,
        roleLabel: arm.role_label,
        accent: position === 0 ? '#ffb454' : '#5ec8ff',
        learning: arm.learning,
        mood: moodFor(entry) as FlyMood,
        equity: curve[index] ?? Number(arm.starting_capital),
        returnPct: Number(entry?.portfolio.return_pct ?? 0),
        fills: entry?.portfolio.fills ?? summary?.fills ?? 0,
        vetoes: entry?.portfolio.vetoes ?? summary?.vetoes ?? 0,
        fees: entry?.portfolio.fees_paid ?? summary?.fees_paid ?? '0',
        curve: curve.slice(0, index + 1),
        side: entry?.decision.side ?? 'HOLD',
        exec: entry?.execution.status ?? 'HOLD',
        reason: entry?.execution.reason ?? reason,
        signalLine,
        memoryLine,
        neural: entry?.signal ? isNeural(entry.signal) : engine === 'neural',
        brainActivity: isNeural(entry?.signal) ? [entry.signal.left_hz,entry.signal.right_hz,entry.signal.KC_spikes,entry.signal.reward_spikes,entry.signal.gate_spikes,entry.signal.aversive_spikes] : [],
        halted: Boolean(entry?.portfolio.halted),
        trades: events,
        lastFill,
      }
    })
    return {
      arms: armStates,
      mid,
      bar: index,
      bars,
      product,
      engine,
      live,
      initialCapital: Number(initialCapital) || 100,
    }
  }, [arms, summaries, observation, seasonBars.length, index, mid, bars, product, engine, live, initialCapital])

  useEffect(() => {
    if (!webglAvailable()) {
      setFallback(true)
      setLoading(false)
      return
    }
    let cancelled = false
    let handle: FloorHandle | null = null
    const stage = stageRef.current
    if (!stage) return

    // three.js is fetched only after WebGL is confirmed: a client without it cannot run
    // this scene, and should not download it. The 2D desks below are its fallback.
    import('../three/floor')
      .then(({ createFloor }) => {
        if (cancelled) return
        handle = createFloor(stage)
        floorRef.current = handle
        if (stateRef.current) handle.update(stateRef.current)
        // Diagnostics hook: lets a headless check confirm the scene rendered real geometry
        // and lets anyone inspect framing from the console.
        ;(window as unknown as { __flyvslyFloor?: FloorHandle }).__flyvslyFloor = handle
        setLoading(false)
      })
      .catch(() => {
        if (cancelled) return
        setFallback(true)
        setLoading(false)
      })

    return () => {
      cancelled = true
      handle?.dispose()
      floorRef.current = null
      delete (window as unknown as { __flyvslyFloor?: FloorHandle }).__flyvslyFloor
    }
  }, [])

  useEffect(() => {
    stateRef.current = state
    floorRef.current?.update(state)
  }, [state])

  const lastTrades = useMemo(() => {
    const rows: string[] = []
    for (const arm of arms) {
      for (const trade of (summaries[arm.id]?.trades ?? []).filter(trade => trade.i <= index).slice(-3)) {
        rows.push(
          `${arm.name.split(' ')[0].toUpperCase()} ${trade.side} ${trade.base_size} @ ${usd(trade.price)} fee ${usd(trade.fee, 4)}`,
        )
      }
    }
    return rows.length ? rows : ['NO FILLS YET AT THIS BAR']
  }, [arms, summaries, index])

  return (
    <section className="panel floor">
      <div className="panel-head">
        <span className="panel-title">01 / The trading floor</span>
        <span className="floor-head-chips">
          {!fallback && <>
            {(['floor','gordon','warren'] as const).map(camera => <button key={camera} type="button" className={`chip floor-mode ${view===camera?'is-on':''}`} aria-pressed={view===camera} onClick={()=>{setView(camera);floorRef.current?.setView(camera)}}>{camera==='floor'?'Full floor':camera==='gordon'?'Gordon close-up':'Warren close-up'}</button>)}
            <button type="button" className={`chip floor-mode ${brain?'is-on':''}`} aria-pressed={brain} onClick={()=>{setBrain(!brain);floorRef.current?.setBrainMode(!brain)}}>◉ Brain scan {brain?'on':'off'}</button>
          </>}
          {!fallback && (
            <button
              type="button"
              className={`chip floor-mode ${tradeCam ? 'is-on' : ''}`}
              onClick={() => {
                const next = !tradeCam
                setTradeCam(next)
                floorRef.current?.setTradeCam(next)
              }}
              title={
                tradeCam
                  ? 'Stop following trades'
                  : 'Trade cam: ease the camera to whichever fly just filled'
              }
            >
              {tradeCam ? 'following' : 'trade cam'}
            </button>
          )}
          {!fallback && (
            <button
              type="button"
              className={`chip floor-mode ${pixel ? 'is-on' : ''}`}
              onClick={() => {
                const next = !pixel
                setPixel(next)
                floorRef.current?.setPixelMode(next)
              }}
              title={
                pixel
                  ? 'Back to the rendered floor'
                  : '8-bit mode: pixelates the scene. The numbers move to the strip above.'
              }
            >
              {pixel ? '8-bit' : '3D'}
            </button>
          )}
          <span className={`chip ${engine === 'neural' ? 'chip-good' : 'chip-warn'}`}>
            {engine === 'neural' ? 'MaleCNS v1.0 on the desk' : 'procedural demo — not neural'}
          </span>
          {live && <span className="chip chip-good">live</span>}
          <span className="chip num">
            bar {index + 1}/{bars}
          </span>
        </span>
      </div>

      <div className="floor-readouts">
        {arms.map((arm) => {
          const entry = observation?.arms?.[arm.id] ?? null
          return (
            <div className="floor-readout" key={arm.id} style={{ '--accent': arm.accent } as React.CSSProperties}>
              <span className="floor-dot" aria-hidden="true" />
              <span className="floor-name">{arm.name}</span>
              <span className="chip">{arm.role_label}</span>
              <span className="floor-pair num">
                <span className="floor-pair-label">side</span>
                <span className={entry?.decision.side === 'BUY' ? 'up' : entry?.decision.side === 'SELL' ? 'down' : 'flat'}>
                  {entry?.decision.side ?? 'HOLD'}
                </span>
              </span>
              <span className="floor-pair num">
                <span className="floor-pair-label">exec</span>
                <span
                  className={
                    entry?.execution.status === 'FILLED'
                      ? 'up'
                      : entry?.execution.status === 'VETO' || entry?.execution.status === 'BLOCKED'
                        ? 'down'
                        : 'flat'
                  }
                >
                  {entry?.execution.status ?? 'HOLD'}
                </span>
              </span>
              <span className="floor-pair num">
                <span className="floor-pair-label">equity</span>
                <span>{usd(state.arms.find((a) => a.id === arm.id)?.equity ?? 0)}</span>
              </span>
              <span className={`floor-return num ${tone(state.arms.find((a) => a.id === arm.id)?.returnPct ?? 0)}`}>
                {signedPct(state.arms.find((a) => a.id === arm.id)?.returnPct ?? 0, 3)}
              </span>
              <span className="floor-pair num floor-minor">
                <span className="floor-pair-label">fills / vetoes / fees</span>
                <span>
                  {entry?.portfolio.fills ?? 0} / {entry?.portfolio.vetoes ?? 0} / {usd(entry?.portfolio.fees_paid ?? '0', 3)}
                </span>
              </span>
              {/* In 8-bit mode the screens are decorative, so the trade that just happened is
                  repeated here, in text that cannot be pixelated. */}
              <span className="floor-pair num floor-minor">
                <span className="floor-pair-label">last trade</span>
                <span>
                  {state.arms.find((a) => a.id === arm.id)?.lastFill
                    ? `${state.arms.find((a) => a.id === arm.id)!.lastFill!.side} ${
                        state.arms.find((a) => a.id === arm.id)!.lastFill!.base
                      } @ ${usd(state.arms.find((a) => a.id === arm.id)!.lastFill!.price)}`
                    : 'none yet'}
                </span>
              </span>
              {entry?.portfolio.halted && <span className="chip chip-bad">halted</span>}
            </div>
          )
        })}
      </div>

      {fallback ? (
        <div className="floor-fallback">
          <p className="floor-fallback-note">
            WebGL is unavailable here, so the 3D floor is replaced by the illustration. Same
            telemetry either way.
          </p>
          <div className="floor-fallback-desks">
            {arms.map((arm) => (
              <Desk
                key={arm.id}
                arm={arm}
                observation={observation?.arms?.[arm.id] ?? null}
                mood={moodFor(observation?.arms?.[arm.id] ?? null)}
                curve={summaries[arm.id]?.curve ?? []}
                curveIndex={index}
                live={live}
              />
            ))}
          </div>
        </div>
      ) : (
        <div
          className={`floor-stage ${pixel ? 'is-pixel' : ''}`}
          ref={stageRef}
          role="img"
          aria-label="Two fly traders at their terminals"
        >
          {loading && <span className="floor-loading">building the floor…</span>}
        </div>
      )}

      {pixel && (
        <p className="floor-pixel-note">
          8-bit mode: the scene is rendered at a fraction of its resolution with a quantised
          palette, so the terminal screens are decorative — the live numbers are in the strip
          above and in the panels below.
        </p>
      )}

      {brain && <section className="brain-inspector" aria-label="Neural activity inspector">
        <div className="brain-caption"><span className="eyebrow">02 / INSIDE THE MIND</span><h2>Every decision starts <br/>with a spark.</h2><p>Measured activity, schematic anatomy. Each glowing cluster represents a recorded signal group; paths and pulses illustrate activity, not individual neuron locations or spike timing.</p></div>
        {arms.map(arm=> {
          const signal=observation?.arms[arm.id]?.signal
          if(!isNeural(signal)) return <div className="brain-stats" key={arm.id}><h3>{arm.name}</h3><p>No neural measurements in this procedural recording.</p></div>
          const values=[['Left DNp20',signal.left_hz,'Hz'],['Right DNp20',signal.right_hz,'Hz'],['Kenyon cells',signal.KC_spikes,'spikes'],['Reward DAN',signal.reward_spikes,'spikes'],['Gate DNpe017',signal.gate_spikes,'spikes'],['Aversive DAN',signal.aversive_spikes,'spikes']] as const
          return <div className="brain-stats" key={arm.id}><h3>{arm.name}<span>{signal.total_spikes.toLocaleString()} total spikes</span></h3>{values.map(([name,value,unit],i)=><div className="brain-signal" key={name} style={{'--signal-color':['#64dfff','#64dfff','#c7e8ad','#ffb454','#cc9eff','#ff7793'][i]} as React.CSSProperties}><span>{name}</span><div><i style={{width:`${Math.min(100,Math.log1p(value)/Math.log(101)*100)}%`}}/></div><strong>{value.toLocaleString()} <small>{unit}</small></strong></div>)}<p>{signal.memory.enabled?`${signal.memory.changed_edges?.toLocaleString() ?? 0} memory connections changed`:'Memory connections frozen'} · {signal.seconds}s observation</p></div>
        })}
      </section>}

      <div className="floor-ticker" aria-label="Tape">
        <span className="floor-ticker-product num">{product}</span>
        <span className="floor-ticker-mid num">{usd(mid)}</span>
        <div className="floor-tape">
          <div className="floor-tape-track">
            {[...lastTrades, ...lastTrades].map((line, i) => (
              <span className="floor-tape-item num" key={`${line}-${i}`}>
                {line}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
