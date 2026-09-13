import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Commentary } from './components/Commentary'
import { DecisionExplainer } from './components/DecisionExplainer'
import { EquityChart } from './components/EquityChart'
import { ExamPanel } from './components/ExamPanel'
import { PriceChart } from './components/PriceChart'
import { Provenance } from './components/Provenance'
import { ReadoutPanel } from './components/ReadoutPanel'
import { Scoreboard } from './components/Scoreboard'
import { SeasonLibrary } from './components/SeasonLibrary'
import { TelemetryPanel } from './components/TelemetryPanel'
import { TopBar } from './components/TopBar'
import { TradeHistory } from './components/TradeHistory'
import { TradingFloor } from './components/TradingFloor'
import { Transport } from './components/Transport'
import {
  loadCatalog,
  loadDemo,
  loadRecording,
  loadReport,
  openStream,
  startRun,
  type Catalog,
} from './lib/data'
import { asRecording, seasonDescription, type LiveRun } from './lib/live'
import type { Observation, Recording, ReportGroup, Source } from './lib/types'
import './App.css'

const DEFAULT_RULES: Record<string, string | number> = {
  capital: '100',
  order_limit: '10',
  paper_fee: '0.006',
  slippage: '0.005',
  spread_limit: '0.005',
  daily_orders: 24,
  interval_seconds: 60,
  decoder_threshold_hz: 2,
  neural_ms: 500,
}

function sourceFor(recording: Recording, mode: 'recorded' | 'demo'): Source {
  const neural = recording.run.engine === 'neural'
  if (mode === 'demo') {
    return {
      mode: 'demo',
      label: 'procedural demo — not neural',
      detail: `${recording.summary.bars} bars of stand-in signals so the layout can be reviewed offline`,
      neural: false,
    }
  }
  return {
    mode: 'recorded',
    label: neural ? 'recorded neural run' : 'recorded procedural run — not neural',
    detail: `${recording.run.season} · ${recording.summary.bars} bars · ${recording.run.id}`,
    neural,
  }
}

/**
 * The header badge for a run in flight.
 *
 * Derived from the live state on every render rather than written once per stream event: a
 * live season grows, so a bar count stored when a bar arrived is stale the moment the next
 * one does. `starting` covers the window before the first bar, when the previous recording
 * is still the thing on screen and the detail says so.
 */
function liveSourceFor(live: LiveRun, starting: boolean): Source {
  const neural = live.engine === 'neural'
  const kind = neural ? 'live neural run' : 'live procedural demo'
  const traded = live.observations.length
  const label = starting
    ? `${kind} — starting`
    : live.finished
      ? `${kind} — finished, loading its recording`
      : neural
        ? `${kind} — streaming`
        : `${kind} — not neural`
  const detail = starting
    ? neural
      ? 'building two MaleCNS v1.0 brains, ~10 s before the first bar · the previous recording stays on screen until then'
      : 'starting the procedural engine · the previous recording stays on screen until then'
    : live.announcedBars > 0
      ? `${seasonDescription(live)} · bar ${traded}/${live.announcedBars}`
      : `${seasonDescription(live)} · ${traded} ${traded === 1 ? 'bar' : 'bars'} traded, no fixed length`
  return { mode: 'live', label, detail, neural }
}

export default function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [recording, setRecording] = useState<Recording | null>(null)
  const [live, setLive] = useState<LiveRun | null>(null)
  const [source, setSource] = useState<Source>({
    mode: 'demo',
    label: 'loading',
    detail: 'fetching recordings',
    neural: false,
  })
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const introPlayed = useRef(false)
  const [speed, setSpeed] = useState(1)
  const [connected, setConnected] = useState(false)
  const [running, setRunning] = useState(false)
  const [starting, setStarting] = useState(false)
  const [report, setReport] = useState<ReportGroup[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const liveRef = useRef<LiveRun | null>(null)

  // True while a stream is in flight. The finished run is handed to its recording, so this
  // goes false by itself as soon as that lands.
  const liveActive = Boolean(live && !live.finished)
  // A live view sits on the newest bar unless the reader has stepped back into the season.
  // The ref is what the stream handler reads: it is created once and must not go stale.
  const [following, setFollowing] = useState(true)
  const followingRef = useRef(true)
  const follow = useCallback((value: boolean) => {
    followingRef.current = value
    setFollowing(value)
  }, [])

  const pick = useCallback(async (id: string, mode: Catalog['mode']) => {
    try {
      const loaded = await loadRecording(id, mode)
      // The catalog finishes loading asynchronously; if a run started in the meantime, the
      // live view owns the header and the recording only stays as a fallback.
      const liveActive = Boolean(liveRef.current && !liveRef.current.finished)
      setRecording(loaded)
      if (liveActive) return
      setLive(null)
      liveRef.current = null
      const total = loaded.summary.bars || loaded.observations.length
      if (!introPlayed.current) {
        // One-shot intro on first open: replay the season from the start so the floor is
        // alive when the page appears, then settle on the final bar.
        introPlayed.current = true
        setIndex(0)
        setPlaying(true)
      } else {
        setIndex(Math.max(0, total - 1))
      }
      setSource(sourceFor(loaded, 'recorded'))
    } catch (failure) {
      setError(`Could not load ${id}: ${(failure as Error).message}`)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const found = await loadCatalog()
      if (cancelled) return
      setCatalog(found)
      if (found.listings.length) {
        const preferred = found.listings.find((entry) => entry.engine === 'neural') ?? found.listings[0]
        await pick(preferred.id, found.mode)
      } else {
        try {
          const demo = await loadDemo()
          setRecording(demo)
          setIndex(Math.max(0, (demo.summary.bars || 1) - 1))
          setSource(sourceFor(demo, 'demo'))
        } catch {
          setError(
            'No recordings found and the bundled demo is missing. Run `flyvsly run` or `flyvsly serve`.',
          )
        }
      }
      const groups = await loadReport(found.mode)
      if (!cancelled) setReport(groups)
    })()
    return () => {
      cancelled = true
    }
  }, [pick])

  useEffect(() => {
    const teardown = openStream({
      onStatus: setConnected,
      onRunStarting: (payload) => {
        setRunning(true)
        setError(null)
        const options = (payload.options ?? {}) as Record<string, string | number>
        const next: LiveRun = {
          runId: String(payload.run_id ?? 'live'),
          engine: (payload.engine as 'neural' | 'procedural') ?? 'neural',
          label: String(payload.label ?? 'live run'),
          seasonDescribe: String(payload.season ?? ''),
          seasonProvenance: {},
          seasonBars: [],
          arms: [],
          observations: [],
          barDurations: [],
          // The session's own age is measured from here, and used only to report how long the
          // stream has been running; the exchange's clock decides the bars.
          startedWall: Date.now() / 1000,
          elapsedSeconds: 0,
          barSeconds: Number(payload.bar_seconds ?? 60),
          // A live hub announces no length: it grows one bar at a time. A run started for a
          // fixed number of bars announces that number, and the header can show progress.
          announcedBars: Number(payload.bars ?? 0) || 0,
          progress: null,
          repeat: Number(payload.repeat ?? 0),
          repeats: Number(payload.repeats ?? 1),
          finished: false,
          rules: { ...DEFAULT_RULES, ...options, paper_fee: '0.006' },
        }
        liveRef.current = next
        setLive(next)
        follow(true)
        // The previous recording stays on screen until the new run's arms arrive: a live view
        // with no arms yet has nothing truthful to render, and the badge says so.
        setStarting(true)
        setIndex(0)
      },
      onSeason: (payload) => {
        const current = liveRef.current
        if (!current) return
        // A fixed season announces its whole bar list here; a live one announces an empty
        // list and the path is grown from the bars as they close (see `marketBars`).
        const announced = (payload.bars as LiveRun['seasonBars'] | null) ?? []
        const next: LiveRun = {
          ...current,
          seasonDescribe: String(payload.describe ?? current.seasonDescribe),
          seasonProvenance: (payload.provenance as Record<string, unknown>) ?? {},
          seasonBars: announced.length ? announced : current.seasonBars,
        }
        liveRef.current = next
        setLive(next)
      },
      onArms: (payload) => {
        const current = liveRef.current
        if (!current) return
        const next: LiveRun = { ...current, arms: (payload.arms as LiveRun['arms']) ?? [] }
        liveRef.current = next
        setLive(next)
      },
      onBar: (payload) => {
        const current = liveRef.current
        if (!current) return
        const observation: Observation = {
          i: Number(payload.i),
          t: Number(payload.t ?? 0),
          product: 'BTC-USDC',
          market: payload.market as Observation['market'],
          frame_sha256: payload.frame_sha256 as string,
          same_frame_both_arms: true,
          same_neural_input_both_arms: Boolean(payload.same_neural_input_both_arms),
          arms: (payload.arms as Observation['arms']) ?? {},
        }
        if (current.observations.some((entry) => entry.i === observation.i)) return
        const elapsed = Number(payload.elapsed ?? 0)
        const observations = [...current.observations, observation]
        const next: LiveRun = {
          ...current,
          observations,
          barDurations: [...current.barDurations, elapsed],
          elapsedSeconds: current.startedWall
            ? Math.max(0, Date.now() / 1000 - current.startedWall)
            : current.elapsedSeconds,
        }
        liveRef.current = next
        setLive(next)
        setStarting(false)
        // Following the live edge is the default. A reader who stepped back into the season
        // keeps the bar they are reading until they ask for the edge again.
        if (followingRef.current) setIndex(observations.length - 1)
      },
      onLiveProgress: (payload) => {
        const current = liveRef.current
        if (!current) return
        const next: LiveRun = {
          ...current,
          progress: {
            available: Number(payload.available ?? 0),
            lagSeconds: Number(payload.lag_seconds ?? 0),
          },
        }
        liveRef.current = next
        setLive(next)
      },
      onFinished: (payload) => {
        const current = liveRef.current
        if (current && payload.run_id) {
          const next: LiveRun = { ...current, finished: true }
          liveRef.current = next
          setLive(next)
        }
        setRunning(false)
        loadCatalog().then(async (found) => {
          setCatalog(found)
          const groups = await loadReport(found.mode)
          setReport(groups)
          const finishedId = String(payload.run_id ?? '')
          // Hand the finished run over to its authoritative recording.
          liveRef.current = null
          setLive(null)
          if (finishedId && found.listings.some((entry) => entry.id === finishedId)) {
            await pick(finishedId, found.mode)
          }
        })
      },
      onFailed: (payload) => {
        setRunning(false)
        setStarting(false)
        liveRef.current = null
        setLive(null)
        setError(
          `${String(payload.error ?? 'the run failed')} — the previous recording is still on screen.`,
        )
      },
    })
    return teardown
  }, [pick, follow])

  // A live session announces no season bars — it grows them — so the run is renderable as
  // soon as both flies exist, and every chart is drawn to the bars that have arrived.
  const liveReady = Boolean(live && live.arms.length >= 2)
  const shown = useMemo(
    () => (liveReady && live ? asRecording(live) : recording),
    [live, liveReady, recording],
  )

  // The bars that exist on screen. A live season grows them one at a time, so a stream still
  // in flight is never scrubbed into a bar that has not happened; a finished recording's
  // observation list is exactly its season, so replay is unchanged.
  const bars = shown ? Math.max(1, shown.observations.length || shown.summary.bars) : 1
  const clampedIndex = Math.min(index, bars - 1)

  useEffect(() => {
    if (!playing || liveActive) return
    const timer = window.setInterval(
      () =>
        setIndex((current) => {
          if (current >= bars - 1) {
            setPlaying(false)
            return current
          }
          return current + 1
        }),
      1000 / speed,
    )
    return () => window.clearInterval(timer)
  }, [playing, speed, bars, liveActive])

  const step = useCallback(
    (delta: number) => {
      setPlaying(false)
      const next = Math.max(0, Math.min(bars - 1, index + delta))
      // Stepping onto the newest bar re-joins the live edge; stepping behind it stays there
      // until asked otherwise, so a bar can be read while the session keeps trading.
      if (liveActive) follow(next >= bars - 1)
      setIndex(next)
    },
    [bars, follow, index, liveActive],
  )

  /** Any bar selection, from the transport or a chart. Never past the bars that exist. */
  const seek = useCallback(
    (value: number) => {
      setPlaying(false)
      const next = Math.max(0, Math.min(bars - 1, value))
      if (liveActive) follow(next >= bars - 1)
      setIndex(next)
    },
    [bars, follow, liveActive],
  )

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return
      if (event.key === 'ArrowRight') step(1)
      if (event.key === 'ArrowLeft') step(-1)
      if (event.key === ' ' && !liveActive) {
        event.preventDefault()
        setPlaying((value) => !value)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step, liveActive])

  if (!shown || shown.arms.length < 2) {
    return (
      <div className="boot">
        <h1>Fly vs. Fly</h1>
        <p className="muted">
          {error ??
            (starting
              ? 'Starting a run: building both flies before the first bar.'
              : 'Loading recordings. Start the local server with `flyvsly serve`, or build the web bundle.')}
        </p>
      </div>
    )
  }

  const observations = shown.observations
  const arms = shown.arms
  const onArm = arms[0]
  const offArm = arms[1] ?? arms[0]
  const summaries = shown.summary.arms
  const initialCapital = shown.summary.initial_capital
  const firstBarT = shown.season.bars[0]?.t ?? 0
  // Every fill in the season, for the equity chart's trade markers.
  const fillMarkers = arms.flatMap((arm) =>
    (summaries[arm.id]?.trades ?? []).map((trade) => ({
      id: arm.id,
      i: trade.i,
      side: trade.side,
      value: summaries[arm.id]?.curve?.[trade.i] ?? Number(trade.equity_after),
    })),
  )
  const firstTimestamp = observations[0]?.t ?? firstBarT
  // Only a run with an announced length can have a time left. The estimate uses the last
  // handful of decisions, so a machine that slows down is reflected rather than averaged away.
  const recentDurations = live?.barDurations.slice(-12) ?? []
  const estimate =
    live && live.announcedBars > live.observations.length && recentDurations.length > 0
      ? ((live.announcedBars - live.observations.length) *
          recentDurations.reduce((sum, value) => sum + value, 0)) /
        recentDurations.length
      : null

  return (
    <div className="app">
      <TopBar
        progress={
          running && live
            ? live.announcedBars > 0
              ? {
                  done: live.observations.length,
                  total: live.announcedBars,
                  etaSeconds: estimate,
                }
              : {
                  // A live session has no announced length. The header says how many bars it
                  // has traded and how far behind the market clock it is, and claims no ETA.
                  done: live.observations.length,
                  total: null,
                  etaSeconds: null,
                  lagSeconds: live.progress?.lagSeconds ?? null,
                }
            : null
        }
        source={live ? liveSourceFor(live, starting) : source}
        listings={catalog?.listings ?? []}
        activeId={recording?.run.id ?? null}
        onPick={(id) => catalog && pick(id, catalog.mode)}
        onRun={async (options) => {
          try {
            const result = await startRun(options)
            if (!result.started) setError(result.reason ?? 'the server refused to start a run')
          } catch (failure) {
            setError((failure as Error).message)
          }
        }}
        running={running}
        streamConnected={connected}
        report={report}
      />

      <main className="app-main">
        <section className="lab-intro">
          <div><p className="eyebrow">FRUIT FLY FUND / EXPERIMENT 001</p>
          <h1>Small brains.<br /><em>Big market energy.</em></h1>
          <p>Two fruit flies. One trading floor. Watch a neural experiment unfold, one decision at a time.</p></div>
          <div className="lab-stamp"><span className="lab-orbit">◉</span><strong>166,700</strong><span>NEURONS PER TRADER</span><small>MaleCNS v1.0 · Paper trading</small></div>
        </section>
        {error && (
          <p className="app-error chip chip-bad">
            {error}
          </p>
        )}

        <div className="controls-row">
          <Transport
            index={clampedIndex}
            bars={bars}
            playing={playing}
            live={liveActive}
            following={following}
            onFollow={() => {
              follow(true)
              setIndex(bars - 1)
            }}
            firstTimestamp={firstTimestamp}
            timestamp={observations[clampedIndex]?.t ?? firstBarT}
            onSeek={seek}
            onPlayToggle={() => { if (!playing && index >= bars - 1) setIndex(0); setPlaying((value) => !value) }}
            onStep={step}
            speed={speed}
            onSpeed={setSpeed}
          />
          <div className="fact-chips">
            <span className="chip">{shown.run.engine === 'neural' ? 'MaleCNS v1.0' : 'procedural'}</span>
            <span className="chip num">{shown.summary.bars} bars × {shown.run.bar_seconds}s</span>
            <span className="chip num">
              {onArm?.backend?.neurons
                ? `${onArm.backend.neurons.toLocaleString()} neurons · ${onArm.backend.directed_edges?.toLocaleString()} connections`
                : shown.run.engine === 'neural'
                  ? 'connectome size not recorded'
                  : 'no connectome in this mode'}
            </span>
            <span className={`chip ${shown.run.inputs_identical_every_bar ? 'chip-good' : 'chip-bad'}`}>
              {shown.run.inputs_identical_every_bar
                ? 'identical input verified'
                : 'input parity unverified'}
            </span>
          </div>
        </div>

        <TradingFloor
          arms={arms}
          summaries={summaries}
          observations={observations}
          seasonBars={shown.season.bars}
          index={clampedIndex}
          engine={shown.run.engine}
          live={liveActive}
          initialCapital={initialCapital}
          product={observations[0]?.product ?? 'BTC-USDC'}
          population={shown.run.population}
        />

        <Scoreboard
          arms={arms}
          summaries={summaries}
          benchmarks={shown.summary.benchmarks}
          initialCapital={initialCapital}
          comparison={shown.summary.comparison}
          barIndex={clampedIndex}
          provisional={liveActive}
        />

        {/* What this run is, and what its comparison can support. */}
        <ExamPanel
          kind={shown.run.kind ?? 'competition'}
          arms={arms}
          summaries={summaries}
          starting={Object.fromEntries(
            arms.map((arm) => [
              arm.id,
              arm.starting_weights ?? { kind: 'baseline', label: 'baseline' },
            ]),
          )}
          comparison={shown.summary.comparison}
          startingCapital={initialCapital}
        />

        <ReadoutPanel readout={shown.run.readout ?? null} arms={arms} />

        <section className="charts-row">
          <EquityChart
            bars={bars}
            curve={summaries[onArm?.id]?.curve ?? []}
            controlCurve={summaries[offArm?.id]?.curve ?? []}
            buyAndHold={shown.summary.benchmarks.buy_and_hold.curve}
            cash={shown.summary.benchmarks.cash.curve}
            initialCapital={Number(initialCapital)}
            selectedIndex={clampedIndex}
            onSelect={seek}
            arms={arms}
            visibleBars={observations.length || bars}
            provisional={liveActive}
            fills={fillMarkers}
          />
          <Commentary
            arms={arms}
            summaries={summaries}
            observations={observations}
            index={clampedIndex}
            engine={shown.run.engine}
            comparison={shown.summary.comparison}
          />
        </section>

        <PriceChart
          times={shown.season.bars.map((bar) => bar.t)}
          mids={shown.season.bars.map((bar) => bar.mid)}
          selectedIndex={clampedIndex}
          onSelect={setIndex}
          product={observations[0]?.product ?? 'BTC-USDC'}
          visibleBars={clampedIndex + 1}
        />

        <section className="explain-row">
          {[onArm, offArm].map((arm) => (
            <DecisionExplainer
              key={arm.id}
              arm={arm}
              observation={observations[clampedIndex]?.arms?.[arm.id] ?? null}
              bar={clampedIndex}
              rules={shown.run.rules}
              fairness={shown.run.starting_conditions?.fairness ?? null}
              product={observations[clampedIndex]?.product ?? 'BTC-USDC'}
            />
          ))}
        </section>

        <section className="telemetry-row">
          {[onArm, offArm].map((arm) => (
            <TelemetryPanel key={arm.id} arm={arm} observations={observations} index={clampedIndex} />
          ))}
        </section>

        <TradeHistory
          arms={arms}
          summaries={summaries}
          selectedIndex={clampedIndex}
          onSelectBar={seek}
          firstBarT={firstBarT}
        />

        <SeasonLibrary
          listings={catalog?.listings ?? []}
          activeId={recording?.run.id ?? null}
          onPick={(id) => catalog && pick(id, catalog.mode)}
          report={report}
        />

        <Provenance recording={shown} />

        <footer className="app-footer">
          <p>
            Built on <a href="https://github.com/nftechie/stonkfly">Stonkfly</a> (MIT) and the{' '}
            <a href="https://male-cns.janelia.org/">MaleCNS v1.0</a> release. Upstream attribution is
            kept in <span className="num">UPSTREAM.md</span>.{' '}
            <a href="https://www.research.google/blog/a-connectomics-milestone-mapping-the-complete-male-fruit-fly-brain/">
              Connectome announcement
            </a>
            .
          </p>
          <p className="muted">
            Paper trading only: no exchange account, no credentials, no real orders. Rivalry
            commentary is entertainment. No claim of profitable learning is made anywhere in this
            project.
          </p>
        </footer>
      </main>
    </div>
  )
}
