/** Formatting helpers. Every one encodes a decision (sign, precision, unit) worth naming. */

export function usd(value: number | string | null | undefined, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'string' ? Number(value) : value
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function signedPct(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(digits)}%`
}

export function signed(value: number | null | undefined, digits = 3, unit = ''): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const sign = value >= 0 ? '+' : '−'
  return `${sign}${Math.abs(value).toFixed(digits)}${unit}`
}

export function pctValue(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(Math.round(value))
}

/** Hash prefix for the audit trail, long enough to check by eye in a terminal. */
export function shortHash(hash: string | null | undefined, length = 10): string {
  return hash ? hash.slice(0, length) : '—'
}

export function clock(t: number | null | undefined): string {
  if (!t) return '—'
  return new Date(t * 1000).toLocaleString('en-GB', {
    timeZone: 'UTC',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Market time inside a season, relative to the first bar of that season. */
export function barClock(t: number, first: number): string {
  const minutes = Math.round((t - first) / 60)
  const sign = minutes < 0 ? '−' : '+'
  const abs = Math.abs(minutes)
  return `T${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.round(seconds - minutes * 60)}s`
}

export function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'flat'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}
