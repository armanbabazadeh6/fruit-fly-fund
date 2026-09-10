/** Small SVG series helpers shared by the equity and price charts. */

export function extent(values: number[]): [number, number] {
  let min = Infinity
  let max = -Infinity
  for (const value of values) {
    if (!Number.isFinite(value)) continue
    if (value < min) min = value
    if (value > max) max = value
  }
  if (min === Infinity) return [0, 1]
  if (min === max) return [min - 1, max + 1]
  return [min, max]
}

/** Axis bounds padded so the line never touches the frame. */
export function paddedExtent(values: number[], pad = 0.06): [number, number] {
  const [min, max] = extent(values)
  const span = max - min || Math.abs(max) || 1
  return [min - span * pad, max + span * pad]
}

export function linePath(
  values: number[],
  x: (index: number) => number,
  y: (value: number) => number,
): string {
  let path = ''
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i]
    if (!Number.isFinite(value)) continue
    path += `${path ? 'L' : 'M'}${x(i).toFixed(2)} ${y(value).toFixed(2)}`
  }
  return path
}

/** Nice round tick values inside a range, for y axes. */
export function ticks(min: number, max: number, count = 4): number[] {
  const span = max - min
  if (!Number.isFinite(span) || span <= 0) return [min]
  const raw = span / count
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude
  const out: number[] = []
  for (let value = Math.ceil(min / step) * step; value <= max; value += step) out.push(value)
  return out
}
