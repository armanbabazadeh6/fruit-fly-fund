/**
 * Standalone harness for the 3D floor.
 *
 * The trading floor is the one part of this project that cannot be checked by reading the
 * DOM: it draws through WebGL, so a reviewer (or a headless test) needs a way to confirm it
 * renders real geometry and to look at it in isolation. This page builds the scene with a
 * small deterministic state, exposes `data-floor` on the stage, and reports what it drew.
 *
 * Not part of the app. Served at `/floor-check.html`.
 */

import { createFloor, type FloorArmState, type FloorState } from './three/floor'
import type { Recording } from './lib/types'
import { isNeural } from './lib/types'
import { moodFor } from './lib/mood'

const marks: string[] = []
const mark = (label: string) => {
  marks.push(`${Math.round(performance.now())}:${label}`)
  document.body.dataset.harness = marks.join(' ')
}

const stage = document.getElementById('stage') as HTMLDivElement
mark('module')
const status = document.getElementById('status') as HTMLParagraphElement
const stats = document.getElementById('stats') as HTMLSpanElement
const barInput = document.getElementById('bar') as HTMLInputElement
const picker = document.getElementById('recording') as HTMLSelectElement
const MOODS = ['idle', 'buy', 'sell', 'veto', 'blocked', 'halted'] as const

let recording: Recording | null = null
// Framing can be swept from the URL, so composition is chosen from measurements rather
// than from whoever happens to be looking at the screen.
const params = new URLSearchParams(location.search)
const framing = {
  spread: params.has('spread') ? Number(params.get('spread')) : undefined,
  distance: params.has('distance') ? Number(params.get('distance')) : undefined,
  height: params.has('height') ? Number(params.get('height')) : undefined,
  flyScale: params.has('fly') ? Number(params.get('fly')) : undefined,
}
let floor = createFloor(stage, framing)
mark('floor-created')
let moodOverride: string | null = null
let index = 0
let timer: number | null = null

async function loadCatalog(): Promise<string[]> {
  try {
    const payload = await (await fetch('/api/recordings')).json()
    return (payload.recordings ?? []).map((entry: { id: string }) => entry.id)
  } catch {
    const payload = await (await fetch('./recordings/index.json')).json()
    return (payload ?? []).map((entry: { id: string }) => entry.id)
  }
}

async function loadRecording(id: string): Promise<Recording> {
  try {
    return await (await fetch(`/api/recordings/${id}`)).json()
  } catch {
    return await (await fetch(`./recordings/${id}.json`)).json()
  }
}

function buildState(): FloorState {
  const rec = recording!
  const bars = rec.season.bars
  const observation = rec.observations[Math.min(index, rec.observations.length - 1)]
  const arms: FloorArmState[] = rec.arms.map((arm, position) => {
    const summary = rec.summary.arms[arm.id]
    const entry = observation?.arms?.[arm.id]
    const signal = entry?.signal
    const neural = signal ? isNeural(signal) : rec.run.engine === 'neural'
    return {
      id: arm.id,
      name: arm.name,
      roleLabel: arm.role_label,
      accent: position === 0 ? '#ffb454' : '#5ec8ff',
      learning: arm.learning,
      mood: (moodOverride ?? moodFor(entry ?? null)) as FloorArmState['mood'],
      equity: summary.curve[Math.min(index, summary.curve.length - 1)] ?? Number(arm.starting_capital),
      returnPct: entry?.portfolio.return_pct ?? 0,
      fills: entry?.portfolio.fills ?? 0,
      vetoes: entry?.portfolio.vetoes ?? 0,
      fees: entry?.portfolio.fees_paid ?? '0',
      curve: summary.curve.slice(0, index + 1),
      side: entry?.decision.side ?? 'HOLD',
      exec: entry?.execution.status ?? 'HOLD',
      reason: entry?.execution.reason ?? entry?.decision.explanation.steps.at(-1) ?? '',
      signalLine: signal && isNeural(signal) ? `${signal.difference_hz.toFixed(1)}Hz g${signal.gate_spikes}` : 'score',
      memoryLine: signal?.memory?.enabled
        ? `${signal.memory.changed_edges ?? 0} eff`
        : 'frozen',
      neural,
      halted: Boolean(entry?.portfolio.halted),
    }
  })
  return {
    arms,
    mid: bars[Math.min(index, bars.length - 1)]?.mid ?? 0,
    bar: index,
    bars: bars.length,
    product: 'BTC-USDC',
    engine: rec.run.engine,
    live: false,
    initialCapital: Number(rec.summary.initial_capital) || 100,
  }
}

function report() {
  const diagnostics = JSON.parse(stage.dataset.floor ?? 'null')
  status.textContent = JSON.stringify(diagnostics, null, 1)
  stats.textContent = diagnostics
    ? `draws ${diagnostics.render.calls} · tris ${diagnostics.render.triangles}`
    : 'no diagnostics yet'
  document.body.dataset.check = JSON.stringify({
    viewport: [window.innerWidth, window.innerHeight],
    framing,
    diagnostics,
    canvas: (() => {
      const canvas = stage.querySelector('canvas')
      return canvas ? [canvas.width, canvas.height] : null
    })(),
  })
}

function render() {
  mark('render-enter')
  floor.update(buildState())
  mark('render-updated')
  // Deliberately not chained to requestAnimationFrame: a background or headless tab may
  // throttle animation frames, and the harness still has to report what it drew.
  window.setTimeout(report, 180)
}

async function start() {
  const ids = await loadCatalog()
  ids.forEach((id) => {
    const option = document.createElement('option')
    option.value = id
    option.textContent = id
    picker.appendChild(option)
  })
  if (!ids.length) {
    status.textContent = 'no recordings published'
    return
  }
  mark('catalog:' + ids.length)
  recording = await loadRecording(ids[0])
  mark('recording:' + recording.observations.length)
  barInput.max = String(recording.observations.length - 1)
  index = recording.observations.length - 1
  barInput.value = String(index)
  render()
  mark('rendered')
}

picker.addEventListener('change', async () => {
  recording = await loadRecording(picker.value)
  barInput.max = String(recording.observations.length - 1)
  index = Math.min(index, recording.observations.length - 1)
  render()
})

barInput.addEventListener('input', () => {
  index = Math.max(0, Math.min(Number(barInput.value), recording!.observations.length - 1))
  render()
})

document.getElementById('moods')!.addEventListener('click', () => {
  const next = moodOverride ? MOODS[(MOODS.indexOf(moodOverride as (typeof MOODS)[number]) + 1) % MOODS.length] : MOODS[0]
  moodOverride = next === 'idle' && moodOverride === 'halted' ? null : next
  render()
})

document.getElementById('play')!.addEventListener('click', () => {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
    return
  }
  timer = window.setInterval(() => {
    index = (index + 1) % recording!.observations.length
    barInput.value = String(index)
    render()
  }, 700)
})

window.addEventListener('beforeunload', () => floor.dispose())
window.addEventListener('resize', () => render())
// Fit measurement for the sweep: the widest station footprint plus the camera used.
window.addEventListener('keydown', (event) => {
  if (event.key === 'r') {
    floor.dispose()
    floor = createFloor(stage, framing)
    render()
  }
})

start()
  .then(() => mark('start-resolved'))
  .catch((error) => {
    mark('start-rejected:' + String(error).slice(0, 80))
    status.textContent = String(error)
  })
