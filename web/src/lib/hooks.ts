import { useCallback, useEffect, useRef, useState } from 'react'

/** Element width from a ResizeObserver, so SVG charts can draw in real pixels. */
export function useMeasuredWidth<T extends HTMLElement>(initial = 720) {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(initial)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver((entries) => {
      const next = Math.round(entries[0]?.contentRect.width ?? 0)
      if (next > 0) setWidth(next)
    })
    observer.observe(element)
    setWidth(Math.round(element.getBoundingClientRect().width) || initial)
    return () => observer.disconnect()
  }, [initial])

  return [ref, width] as const
}

/** Pointer-driven bar selection, used by both charts. */
export function useScrub(
  width: number,
  plots: { left: number; right: number },
  bars: number,
  onSelect: (index: number) => void,
) {
  const dragging = useRef(false)

  const fromEvent = useCallback(
    (clientX: number, rect: DOMRect) => {
      const usable = Math.max(1, width - plots.left - plots.right)
      const ratio = (clientX - rect.left - plots.left) / usable
      return Math.max(0, Math.min(bars - 1, Math.round(ratio * (bars - 1))))
    },
    [width, plots.left, plots.right, bars],
  )

  const onPointerDown = useCallback(
    (event: React.PointerEvent<SVGSVGElement>) => {
      dragging.current = true
      event.currentTarget.setPointerCapture(event.pointerId)
      onSelect(fromEvent(event.clientX, event.currentTarget.getBoundingClientRect()))
    },
    [fromEvent, onSelect],
  )

  const onPointerMove = useCallback(
    (event: React.PointerEvent<SVGSVGElement>) => {
      if (!dragging.current) return
      onSelect(fromEvent(event.clientX, event.currentTarget.getBoundingClientRect()))
    },
    [fromEvent, onSelect],
  )

  const end = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
    dragging.current = false
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }, [])

  return { onPointerDown, onPointerMove, onPointerUp: end, onPointerCancel: end }
}
