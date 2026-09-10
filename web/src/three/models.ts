/**
 * Procedural models for the trading floor. Nothing is downloaded: every mesh is built from
 * primitives at load time, so the repository carries no third-party art and the two flies
 * can be tinted and posed independently.
 *
 * The fly is stylised low-poly rather than anatomically exact. It is a mascot for a
 * simulation, not a model of one: the real animal is 166,700 neurons in `vendor/stonkfly`.
 */

import * as THREE from 'three'

export interface FlyParts {
  root: THREE.Group
  leftWing: THREE.Group
  rightWing: THREE.Group
  body: THREE.Group
  eyes: THREE.Mesh[]
}

function standard(color: number, options: THREE.MeshStandardMaterialParameters = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.08, ...options })
}

/** A translucent wing with procedurally drawn veins. */
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
  context.fillStyle = 'rgba(226, 238, 255, 0.30)'
  context.fill(shape)
  context.strokeStyle = 'rgba(236, 245, 255, 0.72)'
  context.lineWidth = 2
  context.stroke(shape)

  context.lineWidth = 1.1
  context.strokeStyle = 'rgba(228, 240, 255, 0.45)'
  for (let i = 1; i < 7; i += 1) {
    const t = i / 7
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
  const texture = new THREE.CanvasTexture(canvas)
  texture.anisotropy = 4
  return texture
}

let wing: THREE.CanvasTexture | null = null
let wingPlane: THREE.PlaneGeometry | null = null

export function buildFly(accent: string): FlyParts {
  if (!wing) wing = wingTexture()
  // Wing span matters more than any other dimension: an oversized pair makes the fly read
  // as wider than its desk, which looks wrong at any camera distance.
  if (!wingPlane) wingPlane = new THREE.PlaneGeometry(1.3, 0.58, 1, 1)

  const body = new THREE.Group()
  const shell = standard(0x8f9aa8, { roughness: 0.42 })
  const shellDark = standard(0x5f6874, { roughness: 0.5 })
  const eyeColor = new THREE.Color(accent).lerp(new THREE.Color(0xb0182f), 0.55)
  const eyeMaterial = new THREE.MeshStandardMaterial({
    color: eyeColor,
    roughness: 0.16,
    metalness: 0.25,
    emissive: eyeColor.clone().multiplyScalar(0.35),
  })
  const legMaterial = standard(0x2b3038, { roughness: 0.7, metalness: 0.2 })

  const abdomen = new THREE.Mesh(new THREE.SphereGeometry(0.56, 24, 16), shellDark)
  abdomen.scale.set(1, 0.88, 1.42)
  abdomen.position.set(0, 0.5, -0.78)
  body.add(abdomen)

  const thorax = new THREE.Mesh(new THREE.SphereGeometry(0.5, 24, 18), shell)
  thorax.scale.set(1, 0.94, 1.18)
  thorax.position.set(0, 0.58, -0.05)
  body.add(thorax)

  const collar = new THREE.Mesh(new THREE.SphereGeometry(0.3, 18, 12), shellDark)
  collar.position.set(0, 0.6, 0.34)
  body.add(collar)

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.38, 22, 16), shell)
  head.scale.set(1, 0.95, 0.92)
  head.position.set(0, 0.66, 0.62)
  body.add(head)

  const eyes: THREE.Mesh[] = []
  for (const side of [-1, 1]) {
    const eye = new THREE.Mesh(new THREE.SphereGeometry(0.235, 20, 14), eyeMaterial)
    eye.scale.set(0.95, 1.15, 1.05)
    eye.position.set(side * 0.24, 0.71, 0.83)
    eye.rotation.y = side * 0.25
    body.add(eye)
    eyes.push(eye)
  }

  const proboscis = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.035, 0.3, 10), shellDark)
  proboscis.position.set(0, 0.53, 0.95)
  proboscis.rotation.x = 1.15
  body.add(proboscis)

  for (const side of [-1, 1]) {
    const antenna = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.012, 0.42, 8), legMaterial)
    antenna.position.set(side * 0.13, 0.88, 0.78)
    antenna.rotation.set(1.05, 0, side * 0.5)
    body.add(antenna)
    const tip = new THREE.Mesh(new THREE.SphereGeometry(0.045, 10, 8), legMaterial)
    tip.position.set(side * 0.24, 1.04, 0.9)
    body.add(tip)
  }

  // Six legs: two segments each, arranged along the thorax and pushed onto the desk top.
  for (const side of [-1, 1]) {
    for (let i = 0; i < 3; i += 1) {
      const forward = 0.3 - i * 0.26
      const hip = new THREE.Group()
      hip.position.set(side * 0.3, 0.42, forward)
      const upper = new THREE.Mesh(new THREE.CylinderGeometry(0.042, 0.036, 0.44, 8), legMaterial)
      upper.position.set(side * 0.16, 0.02, forward * 0.2)
      upper.rotation.z = side * 0.95
      const shin = new THREE.Mesh(new THREE.CylinderGeometry(0.034, 0.026, 0.46, 8), legMaterial)
      shin.position.set(side * 0.44, -0.24, forward * 0.28)
      shin.rotation.z = side * 0.12
      hip.add(upper, shin)
      body.add(hip)
    }
  }

  const wingMaterial = new THREE.MeshStandardMaterial({
    map: wing,
    transparent: true,
    opacity: 0.9,
    side: THREE.DoubleSide,
    roughness: 0.25,
    metalness: 0.05,
    depthWrite: false,
    emissive: new THREE.Color(0x9fb6d8),
    emissiveIntensity: 0.12,
  })

  const makeWing = (side: number) => {
    const hinge = new THREE.Group()
    hinge.position.set(side * 0.14, 0.78, -0.16)
    const plane = new THREE.Mesh(wingPlane!, wingMaterial)
    plane.position.set(side * 0.6, 0.02, -0.4)
    plane.rotation.set(-0.42, side * 0.34, side * -0.06)
    hinge.add(plane)
    body.add(hinge)
    return hinge
  }

  const root = new THREE.Group()
  root.add(body)
  return { root, leftWing: makeWing(-1), rightWing: makeWing(1), body, eyes }
}

export interface TerminalParts {
  group: THREE.Group
  screen: THREE.Mesh
  glow: THREE.PointLight
  canvas: HTMLCanvasElement
  texture: THREE.CanvasTexture
}

/** A Bloomberg-style terminal: amber monospace on black, drawn to a canvas texture. */
export function buildTerminal(width = 640, height = 380): TerminalParts {
  const group = new THREE.Group()

  const shell = standard(0x1b1f26, { roughness: 0.62, metalness: 0.35 })
  const body = new THREE.Mesh(new THREE.BoxGeometry(2.5, 1.5, 0.1), shell)
  body.position.y = 0.75
  group.add(body)

  const bezel = new THREE.Mesh(new THREE.BoxGeometry(2.32, 1.32, 0.04), standard(0x0d1015, { roughness: 0.8 }))
  bezel.position.set(0, 0.76, 0.055)
  group.add(bezel)

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace

  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(2.24, 1.24),
    new THREE.MeshBasicMaterial({ map: texture, toneMapped: false }),
  )
  screen.position.set(0, 0.76, 0.078)
  group.add(screen)

  const glow = new THREE.PointLight(0xffb454, 0, 4.5, 2)
  glow.position.set(0, 0.8, 0.6)
  group.add(glow)

  const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.09, 0.34, 10), shell)
  stand.position.y = -0.17
  group.add(stand)
  const foot = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.05, 0.36), shell)
  foot.position.y = -0.34
  group.add(foot)

  const keyboard = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.045, 0.44), standard(0x23272f, { roughness: 0.75 }))
  keyboard.position.set(0, -0.34, 0.62)
  keyboard.rotation.x = -0.05
  group.add(keyboard)

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

/** The market board on the wall: the shared market and both equity curves. */
export function buildBoard(width = 1024, height = 256) {
  const group = new THREE.Group()
  // Kept deliberately shallow: the stage is a wide letterbox, so a tall board would be
  // clipped at the top of the frame.
  const frame = new THREE.Mesh(
    new THREE.BoxGeometry(8.6, 1.62, 0.12),
    standard(0x141a22, { roughness: 0.7, metalness: 0.3 }),
  )
  group.add(frame)

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace

  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(8.34, 1.42),
    new THREE.MeshBasicMaterial({ map: texture, toneMapped: false }),
  )
  screen.position.z = 0.07
  group.add(screen)

  const wash = new THREE.PointLight(0xffb454, 0.5, 9, 2)
  wash.position.set(0, 0, 1.4)
  group.add(wash)

  return { group, canvas, texture, screen }
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
