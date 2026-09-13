/**
 * The annotated groups a recorded population vector carries.
 *
 * `run.population` stores each sampled cell's id and annotated type, and counts the sampled
 * cells per category in `groups`. The categories overlap on purpose — a DNp20 is both a
 * descending neuron and a decoder cell — so the counts need not sum to the sample size, and
 * which categories a cell belongs to is a property of its annotated type rather than of its
 * position in the vector.
 *
 * The group *names* are never hard-coded: callers walk the recording's own `groups` block and
 * look the deciding predicate up by name here. The predicates mirror the tiers in
 * `flyvsly/population.py`, which is where the selection and its per-group counts are made; a
 * name this build does not know reads as unattributable rather than quietly zero.
 */

import type { PopulationDescription } from './types'

type Predicate = (type: string) => boolean

/** Mirrors the `_TIERS` predicates in `flyvsly/population.py`, keyed by group name. */
const GROUP_PREDICATES: Record<string, Predicate> = {
  descending: (type) => type.startsWith('DN'),
  mbon: (type) => type.startsWith('MBON'),
  dan: (type) =>
    type === 'PAM11' ||
    type === 'PPL101' ||
    type.startsWith('PAM') ||
    type.startsWith('PPL'),
  decoder: (type) => type === 'DNp20' || type === 'DNpe017',
  kc: (type) => type.startsWith('KC'),
}

export interface GroupFiring {
  /** The group name exactly as the recording wrote it. */
  name: string
  /** Cells in this group, as the recording counted them. Never recomputed here. */
  total: number
  /** How many of the group's cells fired on the bar being shown; null if unattributable. */
  fired: number | null
}

/** The annotated groups a cell type belongs to, in the recording's own names. */
export function groupsForType(type: string, names: string[]): string[] {
  return names.filter((name) => {
    const predicate = GROUP_PREDICATES[name]
    return predicate ? predicate(type) : false
  })
}

/**
 * One row per group the recording counted: its own total, and how many of those cells fired
 * in `values`, the population vector recorded for one arm on one bar.
 */
export function groupFiring(
  population: PopulationDescription,
  values: number[],
): GroupFiring[] {
  return Object.entries(population.groups).map(([name, total]) => {
    const predicate = GROUP_PREDICATES[name]
    if (!predicate) return { name, total, fired: null }
    let fired = 0
    population.types.forEach((type, index) => {
      if (predicate(type) && (values[index] ?? 0) > 0) fired += 1
    })
    return { name, total, fired }
  })
}
