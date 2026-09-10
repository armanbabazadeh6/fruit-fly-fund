/**
 * Procedural models for the trading floor. Nothing is downloaded: every mesh is built from
 * primitives at load time, so the repository carries no third-party art and the two flies
 * can be tinted and posed independently.
 *
 * The fly is stylised, but it follows the anatomy the connectome actually has: a grey body
 * with a banded abdomen, large dark compound eyes with visible facets, bristles, six legs
 * with tarsi, halteres behind the wings, and translucent veined wings. It is a mascot for a
 * simulation, not a model of one — the real animal is 166,700 neurons in `vendor/stonkfly`.
 */

import * as THREE from 'three'

export interface FlyParts {
  root: THREE.Group
  leftWing: THREE.Group
  rightWing: THREE.Group
  body: THREE.Group
  eyes: THREE.Mesh[]
  /** The forelegs that reach the keyboard, one per side, hinged at the shoulder. */
  typingArms: THREE.Group[]
  /** World-space tips of the typing arms, for checking they are over the keys. */
  typingTips: THREE.Object3D[]
  deskLegs: THREE.Group[]
}

export interface KeyboardParts {
  group: THREE.Group
  /** Key caps, so the floor can light the ones being struck. */
  caps: THREE.Mesh[][]
}

function standard(color: number, options: THREE.MeshStandardMaterialParameters = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.08, ...options })
}

/** Deterministic pseudo-random in [0,1) so bristle placement never changes between loads. */
function noise(seed: number, index: number): number {
  const value = Math.sin(seed * 12.9898 + index * 78.233) * 43758.5453
  return value - Math.floor(value)
}

/** A translucent wing with drawn veins and a faint iridescent sheen. */
function wingTexture(): THREE.CanvasTexture {
  const size = 256
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = Math.round(size * 0.45)
  const context = canvas.getContext('2d')!
  const { width, height } = canvas

  context.clearRect(0, 0, width, height)
  const shape = new Path2D()
  shape.moveTo(4, height * 0.62)
  shape.bezierCurveTo(width * 0.22, 2, width * 0.78, 2, width - 4, height * 0.42)
  shape.bezierCurveTo(width * 0.72, height - 3, width * 0.28, height - 3, 4, height * 0.62)
  const sheen = context.createLinearGradient(0, 0, width, height)
  sheen.addColorStop(0, 'rgba(226, 238, 255, 0.38)')
  sheen.addColorStop(0.5, 'rgba(198, 220, 255, 0.22)')
  sheen.addColorStop(1, 'rgba(232, 226, 255, 0.34)')
  context.fillStyle = sheen
  context.fill(shape)
  context.strokeStyle = 'rgba(240, 248, 255, 0.8)'
  context.lineWidth = 2.4
  context.stroke(shape)

  // Cross veins plus the fan of longitudinal veins a fly wing actually shows.
  context.lineWidth = 1.1
  context.strokeStyle = 'rgba(228, 240, 255, 0.5)'
  for (let i = 1; i < 8; i += 1) {
    const t = i / 8
    context.beginPath()
    context.moveTo(width * 0.06, height * 0.62)
    context.bezierCurveTo(
      width * (0.3 + t * 0.2),
      height * (0.16 + t * 0.1),
      width * (0.5 + t * 0.25),
      height * (0.2 + t * 0.12),
      width * (0.9 - t * 0.05),
      height * (0.42 + t * 0.06),
    )
    context.stroke()
  }
  context.lineWidth = 0.8
  context.strokeStyle = 'rgba(226, 238, 255, 0.32)'
  for (let i = 1; i < 6; i += 1) {
    const x = width * (0.14 + i * 0.14)
    context.beginPath()
    context.moveTo(x, height * 0.24)
    context.lineTo(x - width * 0.03, height * 0.78)
    context.stroke()
  }

  const texture = new THREE.CanvasTexture(canvas)
  texture.anisotropy = 4
  return texture
}

/** Compound-eye facets: a dense dot lattice that reads as ommatidia up close. */
function eyeTexture(): THREE.CanvasTexture {
  const size = 256
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const context = canvas.getContext('2d')!
  context.fillStyle = '#5e0f1c'
  context.fillRect(0, 0, size, size)
  for (let row = 0; row < 26; row += 1) {
    for (let column = 0; column < 26; column += 1) {
      const offset = row % 2 ? 5 : 0
      const x = column * 10 + offset
      const y = row * 10
      context.beginPath()
      context.arc(x, y, 3.4, 0, Math.PI * 2)
      context.fillStyle = row % 2 ? 'rgba(190, 42, 62, 0.55)' : 'rgba(150, 26, 44, 0.5)'
      context.fill()
    }
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping
  texture.repeat.set(1.4, 1)
  return texture
}

/** Body shading: a darker dorsum, lighter flanks, and a faint banded abdomen. */
function bodyTexture(): THREE.CanvasTexture {
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const context = canvas.getContext('2d')!
  const gradient = context.createLinearGradient(0, 0, 0, size)
  gradient.addColorStop(0, '#6c7683')
  gradient.addColorStop(0.35, '#9aa5b2')
  gradient.addColorStop(0.62, '#b3bdc9')
  gradient.addColorStop(1, '#79838f')
  context.fillStyle = gradient
  context.fillRect(0, 0, size, size)
  context.strokeStyle = 'rgba(60, 68, 78, 0.35)'
  context.lineWidth = 1
  for (let i = 1; i < 9; i += 1) {
    context.beginPath()
    context.moveTo(0, (i / 9) * size)
    context.lineTo(size, (i / 9) * size)
    context.stroke()
  }
  const texture = new THREE.CanvasTexture(canvas)
  return texture
}

let wing: THREE.CanvasTexture | null = null
let wingPlane: THREE.PlaneGeometry | null = null
let eye: THREE.CanvasTexture | null = null
let body: THREE.CanvasTexture | null = null

export function buildFly(accent: string): FlyParts {
  if (!wing) wing = wingTexture()
  if (!eye) eye = eyeTexture()
  if (!body) body = bodyTexture()
  // Wing span matters more than any other dimension: an oversized pair makes the fly read
  // as wider than its desk, which looks wrong at any camera distance.
  if (!wingPlane) wingPlane = new THREE.PlaneGeometry(1.3, 0.58, 1, 1)

  const bodyGroup = new THREE.Group()
  const shell = standard(0x9aa5b2, { map: body, roughness: 0.42 })
  const shellDark = standard(0x6b7480, { map: body, roughness: 0.5 })
  const eyeColor = new THREE.Color(accent).lerp(new THREE.Color(0xb0182f), 0.6)
  const eyeMaterial = new THREE.MeshStandardMaterial({
    color: eyeColor,
    map: eye,
    roughness: 0.14,
    metalness: 0.3,
    emissive: eyeColor.clone().multiplyScalar(0.3),
  })
  const chitin = standard(0x3a4048, { roughness: 0.6, metalness: 0.25 })
  const legMaterial = standard(0x2b3038, { roughness: 0.68, metalness: 0.22 })
  const bristleMaterial = standard(0x4a515b, { roughness: 0.8 })
  const eyeGlint = new THREE.MeshStandardMaterial({ color: 0xf4f8ff, roughness: 0.05, metalness: 0.9 })

  const abdomen = new THREE.Mesh(new THREE.SphereGeometry(0.56, 28, 20), shellDark)
  abdomen.scale.set(1, 0.88, 1.42)
  abdomen.position.set(0, 0.5, -0.78)
  bodyGroup.add(abdomen)

  // Banded segments: three rings that read as a segmented abdomen.
  for (let i = 0; i < 3; i += 1) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.5 - i * 0.06, 0.022, 8, 26), chitin)
    ring.position.set(0, 0.5, -0.52 - i * 0.28)
    ring.rotation.y = Math.PI / 2
    ring.scale.set(1.28, 0.9, 1)
    bodyGroup.add(ring)
  }

  const thorax = new THREE.Mesh(new THREE.SphereGeometry(0.5, 28, 20), shell)
  thorax.scale.set(1, 0.94, 1.18)
  thorax.position.set(0, 0.58, -0.05)
  bodyGroup.add(thorax)

  const collar = new THREE.Mesh(new THREE.SphereGeometry(0.3, 20, 14), shellDark)
  collar.position.set(0, 0.6, 0.34)
  bodyGroup.add(collar)

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.38, 26, 18), shell)
  head.scale.set(1, 0.95, 0.92)
  head.position.set(0, 0.66, 0.62)
  bodyGroup.add(head)

  const eyes: THREE.Mesh[] = []
  for (const side of [-1, 1]) {
    const eyeball = new THREE.Mesh(new THREE.SphereGeometry(0.235, 24, 18), eyeMaterial)
    eyeball.scale.set(0.95, 1.15, 1.05)
    eyeball.position.set(side * 0.24, 0.71, 0.83)
    eyeball.rotation.y = side * 0.25
    bodyGroup.add(eyeball)
    eyes.push(eyeball)

    const glint = new THREE.Mesh(new THREE.SphereGeometry(0.045, 10, 8), eyeGlint)
    glint.position.set(side * 0.36, 0.84, 0.99)
    bodyGroup.add(glint)
  }

  const proboscis = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.035, 0.3, 12), chitin)
  proboscis.position.set(0, 0.53, 0.95)
  proboscis.rotation.x = 1.15
  bodyGroup.add(proboscis)

  for (const side of [-1, 1]) {
    const antenna = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.012, 0.42, 8), legMaterial)
    antenna.position.set(side * 0.13, 0.88, 0.78)
    antenna.rotation.set(1.05, 0, side * 0.5)
    bodyGroup.add(antenna)
    const arista = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.004, 0.2, 6), legMaterial)
    arista.position.set(side * 0.25, 1.03, 0.9)
    arista.rotation.set(0.6, 0, side * 0.8)
    bodyGroup.add(arista)
  }

  // Bristles: the detail that makes a grey shell read as a fly rather than a bead.
  for (let i = 0; i < 26; i += 1) {
    const onThorax = i < 16
    const radius = onThorax ? 0.5 : 0.5
    const theta = noise(7, i) * Math.PI * 2
    const phi = 0.45 + noise(11, i) * 1.5
    const bristle = new THREE.Mesh(new THREE.ConeGeometry(0.012, 0.085 + noise(3, i) * 0.05, 5), bristleMaterial)
    const y = 0.58 + Math.sin(phi) * radius * 0.94
    const z = (onThorax ? -0.05 : -0.78) + Math.cos(phi) * radius * (onThorax ? 1.18 : 1.42)
    const x = Math.cos(theta) * radius * 0.98
    bristle.position.set(x, y, z)
    bristle.rotation.set(phi, theta, 0)
    bodyGroup.add(bristle)
  }

  // Halteres: the balancing organs behind the wings, a small but unmistakably fly detail.
  for (const side of [-1, 1]) {
    const stalk = new THREE.Mesh(new THREE.CylinderGeometry(0.016, 0.016, 0.18, 6), legMaterial)
    stalk.position.set(side * 0.2, 0.5, 0.1)
    stalk.rotation.z = side * 0.9
    bodyGroup.add(stalk)
    const knob = new THREE.Mesh(new THREE.SphereGeometry(0.05, 10, 8), chitin)
    knob.position.set(side * 0.29, 0.52, 0.1)
    bodyGroup.add(knob)
  }

  /** One leg: femur, tibia and a short tarsus, so the legs do not read as bare sticks. */
  const buildLeg = (side: number, forward: number, hipY: number, scale: number) => {
    const hip = new THREE.Group()
    hip.position.set(side * 0.3, hipY, forward)
    const femur = new THREE.Mesh(new THREE.CylinderGeometry(0.042, 0.034, 0.44 * scale, 8), legMaterial)
    femur.position.set(side * 0.16 * scale, 0.02, forward * 0.2)
    femur.rotation.z = side * 0.95
    const tibia = new THREE.Mesh(new THREE.CylinderGeometry(0.032, 0.024, 0.46 * scale, 8), legMaterial)
    tibia.position.set(side * 0.44 * scale, -0.24 * scale, forward * 0.28)
    tibia.rotation.z = side * 0.12
    const tarsus = new THREE.Mesh(new THREE.CylinderGeometry(0.024, 0.014, 0.16 * scale, 6), legMaterial)
    tarsus.position.set(side * 0.47 * scale, -0.44 * scale, forward * 0.3)
    tarsus.rotation.z = side * -0.25
    hip.add(femur, tibia, tarsus)
    bodyGroup.add(hip)
    return hip
  }

  // Middle and hind legs rest on the desk; the forelegs become the typing arms.
  const deskLegs = [
    buildLeg(-1, -0.1, 0.42, 1),
    buildLeg(1, -0.1, 0.42, 1),
    buildLeg(-1, -0.38, 0.42, 1.05),
    buildLeg(1, -0.38, 0.42, 1.05),
  ]

  const typingArms: THREE.Group[] = []
  const typingTips: THREE.Object3D[] = []
  for (const side of [-1, 1]) {
    const shoulder = new THREE.Group()
    shoulder.position.set(side * 0.26, 0.5, 0.3)
    // Length is set by reach, not by taste: the shoulder sits 0.45 above the keys, so a
    // 0.58-long arm swung forward lands the tarsus on the deck instead of through it.
    const upper = new THREE.Mesh(new THREE.CylinderGeometry(0.038, 0.032, 0.26, 8), legMaterial)
    upper.position.set(0, -0.13, 0)
    const forearm = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.022, 0.22, 8), legMaterial)
    forearm.position.set(side * 0.025, -0.37, 0.02)
    const tarsus = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.012, 0.1, 6), legMaterial)
    tarsus.position.set(side * 0.035, -0.53, 0.04)
    const tip = new THREE.Object3D()
    tip.position.set(side * 0.035, -0.575, 0.05)
    shoulder.add(upper, forearm, tarsus, tip)
    bodyGroup.add(shoulder)
    typingArms.push(shoulder)
    typingTips.push(tip)
  }

  const wingMaterial = new THREE.MeshStandardMaterial({
    map: wing,
    transparent: true,
    opacity: 0.88,
    side: THREE.DoubleSide,
    roughness: 0.22,
    metalness: 0.08,
    depthWrite: false,
    emissive: new THREE.Color(0x9fb6d8),
    emissiveIntensity: 0.14,
  })

  const makeWing = (side: number) => {
    const hinge = new THREE.Group()
    hinge.position.set(side * 0.14, 0.78, -0.16)
    const plane = new THREE.Mesh(wingPlane!, wingMaterial)
    plane.position.set(side * 0.6, 0.02, -0.4)
    plane.rotation.set(-0.42, side * 0.34, side * -0.06)
    hinge.add(plane)
    bodyGroup.add(hinge)
    return hinge
  }

  const root = new THREE.Group()
  root.add(bodyGroup)
  return {
    root,
    leftWing: makeWing(-1),
    rightWing: makeWing(1),
    body: bodyGroup,
    eyes,
    typingArms,
    typingTips,
    deskLegs,
  }
}

/** A keyboard with individual key caps, so a keystroke can be lit where it lands. */
export function buildKeyboard(): KeyboardParts {
  const group = new THREE.Group()
  const deck = new THREE.Mesh(
    new THREE.BoxGeometry(2.05, 0.07, 0.68),
    standard(0x23272f, { roughness: 0.72, metalness: 0.15 }),
  )
  group.add(deck)
  const lip = new THREE.Mesh(
    new THREE.BoxGeometry(2.09, 0.02, 0.72),
    standard(0x2f353f, { roughness: 0.5, metalness: 0.3 }),
  )
  lip.position.y = 0.035
  group.add(lip)

  const capGeometry = new THREE.BoxGeometry(0.105, 0.035, 0.105)
  const capBase = new THREE.MeshStandardMaterial({ color: 0x555d68, roughness: 0.6 })
  const caps: THREE.Mesh[][] = []
  for (let row = 0; row < 4; row += 1) {
    const rowCaps: THREE.Mesh[] = []
    for (let column = 0; column < 14; column += 1) {
      const cap = new THREE.Mesh(capGeometry, capBase.clone())
      cap.position.set(-0.9 + column * 0.138, 0.06, -0.22 + row * 0.145)
      group.add(cap)
      rowCaps.push(cap)
    }
    caps.push(rowCaps)
  }
  return { group, caps }
}

export interface TerminalParts {
  group: THREE.Group
  screen: THREE.Mesh
  glow: THREE.PointLight
  canvas: HTMLCanvasElement
  texture: THREE.CanvasTexture
}

/** A Bloomberg-style terminal: amber monospace on black, drawn to a canvas texture. */
export function buildTerminal(width = 768, height = 448) {
  const group = new THREE.Group()

  const shell = standard(0x1b1f26, { roughness: 0.62, metalness: 0.35 })
  const body = new THREE.Mesh(new THREE.BoxGeometry(3.42, 2.06, 0.11), shell)
  body.position.y = 0.99
  group.add(body)

  const bezel = new THREE.Mesh(new THREE.BoxGeometry(3.22, 1.86, 0.04), standard(0x0d1015, { roughness: 0.8 }))
  bezel.position.set(0, 1.0, 0.06)
  group.add(bezel)

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace

  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(3.12, 1.78),
    new THREE.MeshBasicMaterial({ map: texture, toneMapped: false }),
  )
  screen.position.set(0, 1.0, 0.085)
  group.add(screen)

  const glow = new THREE.PointLight(0xffb454, 0, 6, 2)
  glow.position.set(0, 1.0, 0.7)
  group.add(glow)

  const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.11, 0.4, 12), shell)
  stand.position.y = -0.2
  group.add(stand)
  const foot = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.05, 0.44), shell)
  foot.position.y = -0.4
  group.add(foot)

  return { group, screen, glow, canvas, texture }
}

export function buildLamp(accent: string) {
  const group = new THREE.Group()
  const metal = standard(0x30363f, { roughness: 0.4, metalness: 0.6 })
  const colour = new THREE.Color(accent)

  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.26, 0.32, 0.06, 16), metal)
  group.add(base)
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.045, 1.5, 10), metal)
  stem.position.set(0, 0.75, -0.06)
  stem.rotation.x = 0.16
  group.add(stem)
  const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.6, 10), metal)
  arm.position.set(0, 1.44, 0.2)
  arm.rotation.x = 1.25
  group.add(arm)

  const shade = new THREE.Mesh(
    new THREE.ConeGeometry(0.34, 0.42, 20, 1, true),
    standard(0x2a3038, { roughness: 0.5, metalness: 0.4, side: THREE.DoubleSide }),
  )
  shade.position.set(0, 1.36, 0.46)
  shade.rotation.x = 2.9
  group.add(shade)

  const bulb = new THREE.Mesh(
    new THREE.SphereGeometry(0.09, 12, 10),
    new THREE.MeshBasicMaterial({ color: colour.clone().lerp(new THREE.Color(0xffffff), 0.5) }),
  )
  bulb.position.set(0, 1.24, 0.5)
  group.add(bulb)

  const light = new THREE.SpotLight(colour, 14, 6, 0.85, 0.6, 1.6)
  light.position.set(0, 1.24, 0.5)
  light.target.position.set(0, 0, 1.1)
  group.add(light, light.target)
  return group
}

export function buildDesk() {
  const group = new THREE.Group()
  const top = new THREE.Mesh(
    new THREE.BoxGeometry(4.6, 0.14, 1.9),
    standard(0x2f3540, { roughness: 0.45, metalness: 0.18 }),
  )
  group.add(top)
  const edge = new THREE.Mesh(new THREE.BoxGeometry(4.6, 0.05, 1.9), standard(0x454c59, { roughness: 0.3, metalness: 0.4 }))
  edge.position.y = 0.08
  group.add(edge)
  const panel = new THREE.Mesh(new THREE.BoxGeometry(4.5, 1.5, 0.12), standard(0x252a32, { roughness: 0.6 }))
  panel.position.set(0, -0.82, -0.8)
  group.add(panel)
  for (const side of [-1, 1]) {
    const leg = new THREE.Mesh(new THREE.BoxGeometry(0.14, 1.5, 0.7), standard(0x1d2128, { roughness: 0.7 }))
    leg.position.set(side * 2.1, -0.8, -0.5)
    group.add(leg)
  }
  return group
}

export function buildCup() {
  const group = new THREE.Group()
  const ceramic = standard(0xe8edf4, { roughness: 0.35 })
  const mug = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.14, 0.24, 18, 1, true), ceramic)
  mug.position.y = 0.12
  group.add(mug)
  const top = new THREE.Mesh(new THREE.RingGeometry(0.14, 0.17, 18), ceramic)
  top.rotation.x = -Math.PI / 2
  top.position.y = 0.24
  group.add(top)
  const coffee = new THREE.Mesh(
    new THREE.CircleGeometry(0.14, 18),
    standard(0x3a2418, { roughness: 0.25, metalness: 0.3 }),
  )
  coffee.rotation.x = -Math.PI / 2
  coffee.position.y = 0.215
  group.add(coffee)
  const handle = new THREE.Mesh(new THREE.TorusGeometry(0.08, 0.022, 8, 16, Math.PI * 1.3), ceramic)
  handle.position.set(0.19, 0.12, 0)
  handle.rotation.y = Math.PI / 2
  group.add(handle)
  return group
}

/** Steam wisps: a few translucent spheres that rise and fade on a loop. */
export function buildSteam() {
  const group = new THREE.Group()
  const material = new THREE.MeshBasicMaterial({ color: 0xdfe8f4, transparent: true, opacity: 0.1, depthWrite: false })
  for (let i = 0; i < 3; i += 1) {
    const wisp = new THREE.Mesh(new THREE.SphereGeometry(0.055 + i * 0.012, 8, 6), material.clone())
    wisp.position.set(0, 0.28 + i * 0.16, 0.02 * i)
    group.add(wisp)
  }
  return group
}
