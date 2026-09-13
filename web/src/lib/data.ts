/**
 * Data sources. Three of them, and the app never blurs which one is on screen:
 *
 *   api       a running or stored `flyvsly serve` instance (includes a live SSE feed)
 *   static    recordings published next to the bundle, for a plain static host
 *   demo      the bundled procedural recording, so the layout can be reviewed offline
 *
 * Only `api` and `static` can contain real neural runs, and even then the badge is derived
 * from the recording's own `run.engine` field rather than from which loader produced it.
 */

import type { Recording, RecordingListing, ReportGroup } from './types'

export interface Catalog {
  mode: 'api' | 'static' | 'demo'
  listings: RecordingListing[]
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { accept: 'application/json' } })
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return (await response.json()) as T
}

export async function loadCatalog(): Promise<Catalog> {
  try {
    const payload = await getJson<{ recordings: RecordingListing[] }>('/api/recordings')
    if (payload.recordings?.length) return { mode: 'api', listings: payload.recordings }
  } catch {
    /* no local server: fall through to published files */
  }
  try {
    const listings = await getJson<RecordingListing[]>('./recordings/index.json')
    if (listings?.length) return { mode: 'static', listings }
  } catch {
    /* nothing published either: fall through to the bundled demo */
  }
  return { mode: 'demo', listings: [] }
}

export async function loadRecording(id: string, mode: Catalog['mode']): Promise<Recording> {
  if (mode === 'api') return getJson<Recording>(`/api/recordings/${id}`)
  return getJson<Recording>(`./recordings/${id}.json`)
}

/** The bundled procedural recording, so the layout is reviewable with no server running. */
export async function loadDemo(): Promise<Recording> {
  return getJson<Recording>('./demo-recording.json')
}

export async function loadReport(mode: Catalog['mode']): Promise<ReportGroup[] | null> {
  try {
    if (mode === 'api') {
      const payload = await getJson<{ groups: ReportGroup[] }>('/api/report')
      return payload.groups
    }
    if (mode === 'static') {
      const payload = await getJson<{ groups: ReportGroup[] }>('./recordings/report.json')
      return payload.groups
    }
  } catch {
    return null
  }
  return null
}

export async function startRun(options: Record<string, unknown>): Promise<{
  started: boolean
  reason?: string
}> {
  const response = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(options),
  })
  if (!response.ok) throw new Error(`Run request rejected (${response.status})`)
  return (await response.json()) as { started: boolean; reason?: string }
}

export interface StreamHandlers {
  onHello?: (payload: Record<string, unknown>) => void
  onRunStarting?: (payload: Record<string, unknown>) => void
  onSeason?: (payload: Record<string, unknown>) => void
  onArms?: (payload: Record<string, unknown>) => void
  onBar?: (payload: Record<string, unknown>) => void
  /** The live session's own report of how far behind the exchange it is running. */
  onLiveProgress?: (payload: Record<string, unknown>) => void
  onFinished?: (payload: Record<string, unknown>) => void
  onFailed?: (payload: Record<string, unknown>) => void
  onStatus?: (connected: boolean) => void
}

/** Subscribe to the arena stream. Returns a teardown function. */
export function openStream(handlers: StreamHandlers): () => void {
  const source = new EventSource('/api/run/stream')
  const bind = (name: string, fn?: (payload: Record<string, unknown>) => void) => {
    source.addEventListener(name, (event) => {
      try {
        fn?.(JSON.parse((event as MessageEvent).data))
      } catch {
        /* a malformed frame must not kill the stream */
      }
    })
  }
  bind('hello', handlers.onHello)
  bind('run_starting', handlers.onRunStarting)
  bind('season_ready', handlers.onSeason)
  bind('arms_ready', handlers.onArms)
  bind('bar', handlers.onBar)
  bind('live_progress', handlers.onLiveProgress)
  bind('recording', handlers.onFinished)
  bind('batch_finished', handlers.onFinished)
  bind('run_failed', handlers.onFailed)
  source.onopen = () => handlers.onStatus?.(true)
  source.onerror = () => handlers.onStatus?.(false)
  return () => source.close()
}
