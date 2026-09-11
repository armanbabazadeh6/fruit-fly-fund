import * as THREE from 'three'

/**
 * The fly's brain, drawn from its own measured spike counts.
 *
 * What is real: every dot is one cell from the recorded population (see
 * `flyvsly/population.py`), coloured by that cell's annotated type, and its brightness and
 * size come from that cell's spike count on the current bar. A silent cell goes dark; the
 * cells the decoder watches are the ones already deciding the trade.
 *
 * What is not: the positions. The simulation runs a graph, not a body, so there are no
 * anatomical coordinates to draw. Cells are laid out in per-type clusters for legibility —
 * schematic geometry, measured activity — and the copy says so on screen.
 */

export type CellGroup = 'dopamine' | 'decoder' | 'gate' | 'descending' | 'kenyon' | 'other'

/**
 * Read the group out of a MaleCNS cell type, e.g. `PAM11`, `DNp20`, `KCg-m`.
 *
 * The sampled population is mostly descending neurons — the cells that carry the brain's
 * output — so they get their own colour rather than falling through to grey, and the few the
 * decoder actually reads are picked out of them.
 */
export function groupForType(type: string): CellGroup {
  if (type.startsWith('DNp20')) return 'decoder'
  if (type.startsWith('DNpe017')) return 'gate'
  if (type.startsWith('PAM') || type.startsWith('PPL')) return 'dopamine'
  if (type.startsWith('KC')) return 'kenyon'
  if (type.startsWith('DN')) return 'descending'
  return 'other'
}

const COLOURS: Record<CellGroup, number> = {
  dopamine: 0xffb454,
  decoder: 0x64dfff,
  gate: 0xcc9eff,
  descending: 0x7fd1c0,
  kenyon: 0xc7e8ad,
  other: 0x8fa3b8,
}

/** Where each group's cluster sits. Illustrative, and labelled as such. */
const CLUSTERS: Record<CellGroup, [number, number, number]> = {
  dopamine: [-0.28, 0.16, 0.06],
  decoder: [0.28, 0.14, 0.02],
  gate: [0, -0.22, 0.08],
  descending: [0, -0.02, 0],
  kenyon: [0, 0.28, -0.14],
  other: [0, 0.02, 0.24],
}

/** Uniform-ish scatter inside a cluster, so the same cell lands in the same place every frame. */
function scatter(i: number, spread: number): [number, number, number] {
  const angle = i * 2.39996
  const radius = spread * Math.sqrt(((i % 97) + 1) / 97)
  return [Math.cos(angle) * radius, Math.sin(angle) * radius * 0.8, Math.sin(i * 1.7) * spread * 0.5]
}

export function buildBrain() {
  const root = new THREE.Group()
  const shell = new THREE.Mesh(
    new THREE.SphereGeometry(0.57, 32, 24),
    new THREE.MeshBasicMaterial({
      color: 0x66d9e8,
      wireframe: true,
      transparent: true,
      opacity: 0.055,
      depthWrite: false,
    }),
  )
  shell.scale.set(1.15, 0.85, 0.8)
  root.add(shell)

  const cloud = new THREE.Group()
  root.add(cloud)

  const geometry = new THREE.SphereGeometry(1, 6, 5)
  const material = (group: CellGroup) =>
    new THREE.MeshBasicMaterial({
      color: COLOURS[group],
      transparent: true,
      opacity: 0.12,
      depthWrite: false,
    })

  let dots: THREE.Mesh[] = []
  let types: string[] = []
  let lit = 0

  /** One dot per measured cell, in the order the population vector is recorded in. */
  const rebuild = (next: string[]) => {
    for (const dot of dots) cloud.remove(dot)
    dots = []
    types = next
    const counters: Record<string, number> = {}
    next.forEach((type) => {
      const group = groupForType(type)
      const seen = counters[group] ?? 0
      counters[group] = seen + 1
      const dot = new THREE.Mesh(geometry, material(group))
      const [cx, cy, cz] = CLUSTERS[group]
      const [dx, dy, dz] = scatter(seen, 0.17)
      dot.position.set(cx + dx, cy + dy, cz + dz)
      dot.scale.setScalar(0.7)
      cloud.add(dot)
      dots.push(dot)
    })
    lit = 0
  }

  root.visible = false
  return {
    root,
    /** Cells currently drawn — 0 until a bar with a measured population is shown. */
    get cellCount() {
      return dots.length
    },
    /** Cells lit above the quiet floor on the last update, for the diagnostic readout. */
    get litCount() {
      return lit
    },
    /**
     * Drive every dot from the measured vector. `types` (the run's cell identities) is only
     * read when it changes, which is once per run.
     */
    update(values: number[], time: number, cellTypes?: string[]) {
      if (cellTypes && (cellTypes.length !== types.length || cellTypes !== types)) {
        rebuild(cellTypes)
      }
      const peak = Math.max(1, ...values.slice(0, dots.length))
      let bright = 0
      dots.forEach((dot, index) => {
        const spikes = Math.max(0, values[index] ?? 0)
        const activity = Math.min(1, Math.log1p(spikes) / Math.log1p(peak))
        if (activity > 0.08) bright += 1
        const pulse = 0.65 + 0.35 * Math.sin(time * 3 + index * 0.7)
        const material = dot.material as THREE.MeshBasicMaterial
        material.opacity = 0.1 + activity * pulse * 0.9
        dot.scale.setScalar(0.62 + activity * pulse * 0.85)
      })
      lit = bright
    },
  }
}
