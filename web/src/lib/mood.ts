/** Which pose a fly is in, derived from its recorded telemetry and nothing else. */

import type { ArmObservation, FlyMood } from './types'

export function moodFor(observation: ArmObservation | null): FlyMood {
  if (!observation) return 'idle'
  if (observation.portfolio.halted || observation.execution.status === 'BLOCKED') {
    return observation.portfolio.halted ? 'halted' : 'blocked'
  }
  if (observation.execution.status === 'VETO') return 'veto'
  if (observation.execution.status === 'FILLED') {
    return observation.decision.side === 'SELL' ? 'sell' : 'buy'
  }
  return 'idle'
}

/** A fly is only "excited" while something actually happened on this bar. */
export function isFlapping(mood: FlyMood): boolean {
  return mood === 'buy' || mood === 'sell'
}
