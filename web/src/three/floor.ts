/**
 * The trading floor: two flies, two desks, two terminals, one market.
 *
 * three.js is imported by this module only. `TradingFloor.tsx` loads it with a dynamic
 * import after checking for WebGL, because a browser without WebGL cannot use it and
 * should not download it — the fallback there is the 2D desk illustration.
 *
 * Every number drawn on a terminal screen comes from the recording passed in through
 * `update()`. Nothing on those screens is decorative.
 */

import * as THREE from 'three'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'

import type { FlyMood } from '../lib/types'
import {
  buildCup,
  buildDesk,
  buildFly,
  buildKeyboard,
  buildLamp,
  buildSteam,
  buildTerminal,
  type FlyParts,
  type KeyboardParts,
  type TerminalParts,
} from './models'

export interface FloorArmState {
  id: string
  name: string
  roleLabel: string
  accent: string
  learning: boolean
  mood: FlyMood
  equity: number
  returnPct: number
  fills: number
  vetoes: number
  fees: string
  curve: number[]
  side: string
  exec: string
  reason: string
  signalLine: string
  memoryLine: string
  neural: boolean
  halted: boolean
}

export interface FloorState {
  arms: FloorArmState[]
  mid: number
  bar: number
  bars: number
  product: string
  engine: 'neural' | 'procedural'
  live: boolean
  initialCapital: number
}

interface Station {
  side: number
  desk: THREE.Group
  fly: FlyParts
  terminal: TerminalParts
  keyboard: KeyboardParts
  cup: THREE.Group
  lamp: THREE.Group
  steam: THREE.Group
  glow: THREE.PointLight
  accent: THREE.Color
  flapPhase: number
  mood: FlyMood
  pulse: number
}

export interface FloorHandle {
  update(state: FloorState): void
  setPaused(paused: boolean): void
  diagnostics(): Record<string, unknown>
  dispose(): void
}


function gridTexture(): THREE.CanvasTexture {
  const size = 512
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const context = canvas.getContext('2d')!
  context.fillStyle = '#0b1017'
  context.fillRect(0, 0, size, size)
  context.strokeStyle = 'rgba(90, 130, 175, 0.18)'
  context.lineWidth = 2
  for (let i = 0; i <= size; i += size / 8) {
    context.beginPath()
    context.moveTo(i, 0)
    context.lineTo(i, size)
    context.stroke()
    context.beginPath()
    context.moveTo(0, i)
    context.lineTo(size, i)
    context.stroke()
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping
  texture.repeat.set(9, 5)
  texture.anisotropy = 4
  return texture
}

function drawTerminal(terminal: TerminalParts, arm: FloorArmState, state: FloorState, time: number) {
  const context = terminal.canvas.getContext('2d')
  if (!context) return
  const { width, height } = terminal.canvas
  const accent = arm.accent
  const amber = '#ffb454'
  const dim = '#6b7f94'
  const ink = '#dbe7f3'
  const mono = '13px ui-monospace, SFMono-Regular, Menlo, monospace'

  context.fillStyle = '#05080c'
  context.fillRect(0, 0, width, height)

  // Header
  context.fillStyle = '#0e1620'
  context.fillRect(0, 0, width, 34)
  context.fillStyle = accent
  context.fillRect(0, 0, 4, 34)
  context.font = 'bold 16px ui-monospace, SFMono-Regular, Menlo, monospace'
  context.fillStyle = accent
  context.fillText(arm.name.toUpperCase(), 16, 23)
  context.font = '12px ui-monospace, SFMono-Regular, Menlo, monospace'
  context.fillStyle = dim
  context.fillText(arm.roleLabel.toUpperCase(), 240, 23)
  context.fillStyle = state.live ? '#5ad9a4' : dim
  context.fillText(state.live ? 'LIVE' : 'RECORDED', width - 92, 23)

  // Equity + return, the two numbers a desk always has in view
  context.font = 'bold 30px ui-monospace, SFMono-Regular, Menlo, monospace'
  context.fillStyle = ink
  context.fillText(arm.equity.toFixed(2), 16, 74)
  context.font = 'bold 15px ui-monospace, SFMono-Regular, Menlo, monospace'
  context.fillStyle = arm.returnPct >= 0 ? '#5ad9a4' : '#ff6b81'
  context.fillText(`${arm.returnPct >= 0 ? '+' : '−'}${Math.abs(arm.returnPct).toFixed(3)}%`, 132, 74)
  context.font = mono
  context.fillStyle = dim
  context.fillText(`start ${state.initialCapital.toFixed(2)}`, 232, 73)

  // Equity curve, scaled to its own range
  const plot = { x: 16, y: 92, w: width - 32, h: 84 }
  context.strokeStyle = 'rgba(120, 150, 185, 0.18)'
  context.lineWidth = 1
  context.strokeRect(plot.x, plot.y, plot.w, plot.h)
  const values = arm.curve.length ? arm.curve : [state.initialCapital]
  if (values.length > 1) {
    const low = Math.min(...values, state.initialCapital)
    const high = Math.max(...values, state.initialCapital)
    const span = high - low || 1
    const xOf = (i: number) => plot.x + (i / (values.length - 1)) * plot.w
    const yOf = (v: number) => plot.y + plot.h - ((v - low) / span) * plot.h
    context.setLineDash([3, 3])
    context.strokeStyle = 'rgba(160, 180, 205, 0.4)'
    context.beginPath()
    context.moveTo(plot.x, yOf(state.initialCapital))
    context.lineTo(plot.x + plot.w, yOf(state.initialCapital))
    context.stroke()
    context.setLineDash([])
    context.strokeStyle = accent
    context.lineWidth = 2
    context.beginPath()
    values.forEach((value, i) => (i ? context.lineTo(xOf(i), yOf(value)) : context.moveTo(xOf(i), yOf(value))))
    context.stroke()
    const last = values.length - 1
    context.fillStyle = accent
    context.beginPath()
    context.arc(xOf(last), yOf(values[last]), 3, 0, Math.PI * 2)
    context.fill()
  } else {
    context.fillStyle = dim
    context.font = mono
    context.fillText('waiting for the first bar', plot.x + 8, plot.y + plot.h / 2)
  }

  // Ledger rows
  const rows: [string, string][] = [
    ['SIDE', arm.side],
    ['EXEC', arm.exec],
    ['FILLS', String(arm.fills)],
    ['VETOES', String(arm.vetoes)],
    ['FEES', `${Number(arm.fees).toFixed(4)}`],
    ['BAR', `${state.bar + 1}/${state.bars}`],
    ['MID', state.mid.toFixed(2)],
    [arm.neural ? 'SIGNAL' : 'SCORE', arm.signalLine],
  ]
  const top = plot.y + plot.h + 18
  context.font = mono
  rows.forEach(([label, value], index) => {
    const column = index % 4
    const row = Math.floor(index / 4)
    const x = 16 + column * ((width - 32) / 4)
    const y = top + row * 30
    context.fillStyle = dim
    context.fillText(label, x, y)
    context.fillStyle =
      label === 'SIDE' ? accent : label === 'EXEC' ? (value === 'FILLED' ? '#5ad9a4' : value === 'VETO' || value === 'BLOCKED' ? '#ff6b81' : ink) : ink
    context.font = 'bold 14px ui-monospace, SFMono-Regular, Menlo, monospace'
    context.fillText(value.slice(0, 16), x, y + 15)
    context.font = mono
  })

  // Memory row: the experimental variable, stated on the desk itself
  const memoryY = top + 74
  context.fillStyle = '#0e1620'
  context.fillRect(12, memoryY - 14, width - 24, 26)
  context.fillStyle = arm.learning ? amber : dim
  context.fillText(arm.learning ? 'MEMORY UPDATES ON' : 'MEMORY UPDATES OFF', 18, memoryY + 3)
  context.fillStyle = ink
  context.fillText(arm.memoryLine.slice(0, 40), 190, memoryY + 3)

  // Footer: the reason this bar went the way it did
  context.fillStyle = '#0b121a'
  context.fillRect(0, height - 30, width, 30)
  context.fillStyle = dim
  context.fillText(arm.reason.slice(0, 86), 14, height - 10)
  if (Math.sin(time * 3) > 0) {
    context.fillStyle = accent
    context.fillRect(width - 16, height - 21, 7, 12)
  }

  terminal.texture.needsUpdate = true
}

/** Where the camera looks. The desk surface is world y = 0 and the fly sits on it. */
const LOOK_AT_Y = 1.02
/** Desk feet are at this local height, so this is where the room's floor belongs. */
const DESK_FEET_Y = -1.55

export interface FloorOptions {
  /** Horizontal distance between the two stations. */
  spread?: number
  /** Camera distance and height; the pair sets how tightly the desks fill the frame. */
  distance?: number
  height?: number
  fov?: number
  /** Overall fly scale; the mascot should not dominate the desk it sits at. */
  flyScale?: number
}

export function createFloor(container: HTMLElement, options: FloorOptions = {}): FloorHandle {
  // Framing was chosen from measurements, not by eye: with these values both flies sit in
  // the lower middle of the frame (|x| ~ 0.48), both screens above them (|x| ~ 0.65), the
  // desks bleed off the bottom and outer edges, and nothing important is cropped. The
  // harness page (`/floor-check.html`) prints these numbers for anyone changing them.
  const spread = options.spread ?? 3.34
  const cameraDistance = options.distance ?? 8.6
  const cameraHeight = options.height ?? 3.4
  const fov = options.fov ?? 34
  const flyScale = options.flyScale ?? 0.86
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.08
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.width = '100%'
  renderer.domElement.style.height = '100%'
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x070b11)
  // Far enough that the wall board (which sits behind the desks) is not washed out.
  scene.fog = new THREE.Fog(0x070b11, 17, 42)

  const pmrem = new THREE.PMREMGenerator(renderer)
  const environment = pmrem.fromScene(new RoomEnvironment(), 0.05)
  scene.environment = environment.texture
  pmrem.dispose()

  const camera = new THREE.PerspectiveCamera(fov, 2, 0.1, 100)
  camera.position.set(0, cameraHeight, cameraDistance)
  camera.lookAt(0, LOOK_AT_Y, 0)

  scene.add(new THREE.HemisphereLight(0x9dc0ff, 0x0a0f16, 0.55))

  const key = new THREE.DirectionalLight(0xfff0dc, 2.1)
  key.position.set(4.5, 8.5, 6.5)
  key.castShadow = true
  key.shadow.mapSize.set(2048, 2048)
  key.shadow.camera.near = 1
  key.shadow.camera.far = 26
  key.shadow.camera.left = -9
  key.shadow.camera.right = 9
  key.shadow.camera.top = 8
  key.shadow.camera.bottom = -7
  key.shadow.bias = -0.0016
  key.shadow.normalBias = 0.02
  scene.add(key)

  const fill = new THREE.DirectionalLight(0x6f8fd0, 0.7)
  fill.position.set(-6, 4, -4)
  scene.add(fill)

  const backdrop = new THREE.Mesh(
    new THREE.PlaneGeometry(60, 26),
    new THREE.MeshStandardMaterial({ color: 0x0a0f16, roughness: 1, metalness: 0 }),
  )
  backdrop.position.set(0, 5, -9)
  scene.add(backdrop)

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(60, 34),
    new THREE.MeshStandardMaterial({ map: gridTexture(), roughness: 0.9, metalness: 0.1 }),
  )
  // The floor sits at the height of the desk feet. It used to sit at y = 0 while the
  // stations were pushed 1.02 below it, which buried both flies under an opaque plane.
  floor.rotation.x = -Math.PI / 2
  floor.position.y = DESK_FEET_Y
  floor.receiveShadow = true
  floor.name = 'floor'
  scene.add(floor)

  const stations: Station[] = []
  const accents = ['#ffb454', '#5ec8ff']

  for (const side of [-1, 1]) {
    const station = new THREE.Group()
    // Desk top at world y = 0, desk feet on the floor below it.
    station.position.set(side * spread, 0, 0)
    station.rotation.y = -side * 0.4
    scene.add(station)

    const desk = buildDesk()
    desk.name = 'desk'
    desk.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) child.receiveShadow = true
    })
    station.add(desk)

    const terminal = buildTerminal()
    terminal.group.name = 'terminal'
    // Raised so the fly's head sits below the screen instead of covering it.
    terminal.group.position.set(side * 0.42, 1.34, -0.78)
    terminal.group.rotation.y = -side * 0.22
    terminal.group.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) child.castShadow = true
    })
    station.add(terminal.group)

    const fly = buildFly(accents[side < 0 ? 0 : 1])
    fly.root.name = 'fly'
    fly.root.scale.setScalar(flyScale)
    // Feet on the desk surface (the desk top plane is at local y = 0.105), set back from
    // the keyboard so the forelegs can reach the keys.
    fly.root.position.set(-side * 0.3, 0.12, 0.34)
    // Facing inward: the two flies are rivals sharing a floor, and turning them away from
    // each other read as two unrelated desks.
    fly.root.rotation.y = -side * 0.62
    fly.body.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        child.castShadow = true
        child.receiveShadow = true
      }
    })
    station.add(fly.root)

    const lamp = buildLamp(accents[side < 0 ? 0 : 1])
    lamp.name = 'lamp'
    lamp.position.set(side * 1.12, 0.07, -0.3)
    lamp.rotation.y = -side * 0.6
    lamp.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) child.castShadow = true
    })
    station.add(lamp)

    const keyboard = buildKeyboard()
    keyboard.group.name = 'keyboard'
    keyboard.group.position.set(-side * 0.06, 0.09, 0.92)
    keyboard.group.rotation.y = -side * 0.3
    keyboard.group.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        child.castShadow = true
        child.receiveShadow = true
      }
    })
    station.add(keyboard.group)

    const cup = buildCup()
    cup.name = 'cup'
    cup.position.set(-side * 1.62, 0.07, 0.34)
    station.add(cup)

    const steam = buildSteam()
    steam.position.copy(cup.position)
    station.add(steam)

    const glow = new THREE.PointLight(new THREE.Color(accents[side < 0 ? 0 : 1]), 0.6, 4, 2)
    glow.position.set(side * 0.5, 1.5, -0.1)
    station.add(glow)

    stations.push({
      side,
      desk,
      fly,
      keyboard,
      cup,
      lamp,
      terminal,
      steam,
      glow,
      accent: new THREE.Color(accents[side < 0 ? 0 : 1]),
      flapPhase: side < 0 ? 0 : 1.7,
      mood: 'idle',
      pulse: 0,
    })
  }

  let state: FloorState | null = null
  // Must start below zero: `performance.now()` is small right after load, and a zero
  // baseline would skip the first publish — which is the only one a paused recording gets.
  let lastDiagnostics = -1e9
  let width = container.clientWidth || 1200
  let height = container.clientHeight || 520
  let paused = false
  let disposed = false
  let frame = 0
  let lastTime = performance.now()
  let clock = 0
  const pointer = { x: 0, y: 0 }
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  const applySize = () => {
    const nextWidth = Math.max(320, container.clientWidth || width)
    const nextHeight = Math.max(240, container.clientHeight || height)
    width = nextWidth
    height = nextHeight
    renderer.setSize(width, height, false)
    camera.aspect = width / height
    // Keep both desks in frame on wide and narrow layouts by pulling the camera back.
    // Narrow viewports need a wider shot; ultrawide ones should not shrink the desks into
    // the middle of the screen.
    const fit = Math.max(0.86, Math.min(1.34, 1400 / width))
    camera.position.set(0, cameraHeight * fit, cameraDistance * fit)
    camera.lookAt(0, 0.75, 0)
    camera.updateProjectionMatrix()
  }

  const onPointerMove = (event: PointerEvent) => {
    const rect = renderer.domElement.getBoundingClientRect()
    pointer.x = ((event.clientX - rect.left) / rect.width - 0.5) * 2
    pointer.y = ((event.clientY - rect.top) / rect.height - 0.5) * 2
  }
  if (!reduced) window.addEventListener('pointermove', onPointerMove)

  const animateWings = (station: Station, dt: number) => {
    const mood = station.mood
    const busy = mood === 'buy' || mood === 'sell'
    // The two flies used to flap at wildly different rates — one manic, one still — because
    // an active bar ran at 34 rad/s against an idle 6.4. Both now buzz, with the difference
    // legible but not a wind machine.
    const speed = mood === 'halted' ? 0 : busy ? 19 : 8
    const amplitude = mood === 'halted' ? 0 : busy ? 0.5 : 0.22
    clock += dt
    const t = clock * speed + station.flapPhase * 3.1
    const beat = Math.sin(t)
    const tuck = mood === 'veto' || mood === 'blocked' ? -0.22 : 0
    station.fly.leftWing.rotation.z = -amplitude * beat + tuck
    station.fly.rightWing.rotation.z = amplitude * beat - tuck
    station.fly.rightWing.rotation.x = station.fly.leftWing.rotation.x = -0.05 + 0.07 * beat

    // Typing: the forelegs strike the keys in alternation, and the key under each arm lights
    // as it lands. Always typing — they are supposed to be working.
    const tapRate = mood === 'halted' ? 0 : busy ? 13 : 6.5
    const tapPhase = clock * tapRate + station.flapPhase
    station.fly.typingArms.forEach((arm, index) => {
      const side = index === 0 ? -1 : 1
      const strike = Math.max(0, Math.sin(tapPhase + index * Math.PI))
      // Negative X swings the forelegs forward onto the deck (positive swung them back).
      arm.rotation.x = -0.68 + strike * 0.16
      arm.rotation.z = side * (0.16 - strike * 0.06)
      const row = station.keyboard.caps[2]
      const cap = row?.[index === 0 ? 4 : 9 + ((Math.floor(clock * tapRate) % 3) - 1)]
      if (cap) {
        const material = cap.material as THREE.MeshStandardMaterial
        material.emissive = material.emissive ?? new THREE.Color()
        material.emissive.setHex(0xffb454)
        material.emissiveIntensity = station.mood === 'halted' ? 0 : strike * 0.9
      }
    })

    const nod = Math.sin(clock * (busy ? 5 : 1.9) + station.flapPhase) * (busy ? 0.022 : 0.012)
    const bob = mood === 'halted' ? 0 : nod
    station.fly.body.position.y = bob + (mood === 'halted' ? -0.1 : 0)
    // A working posture: leaning over the keyboard, with a small nod as it types.
    station.fly.body.rotation.x = mood === 'halted' ? -0.12 : 0.13 + nod
    station.fly.body.rotation.z =
      mood === 'halted' ? station.side * 0.2 : Math.sin(clock * 1.3 + station.flapPhase) * 0.015
    const eyeGlow = mood === 'halted' ? 0.1 : busy ? 0.5 : 0.28
    for (const eye of station.fly.eyes) {
      const material = eye.material as THREE.MeshStandardMaterial
      material.emissiveIntensity = eyeGlow
    }
    station.pulse = Math.max(0, station.pulse - dt * 1.6)
    const activity = busy ? 1.1 : mood === 'veto' || mood === 'blocked' ? 0.8 : 0.45
    station.glow.intensity = activity + station.pulse * 2.4
    station.terminal.glow.intensity = 0.5 + station.pulse * 1.6
    for (let i = 0; i < station.steam.children.length; i += 1) {
      const wisp = station.steam.children[i]
      const phase = (clock * 0.32 + i * 0.33) % 1
      wisp.position.y = 0.28 + phase * 0.62 + i * 0.05
      wisp.position.x = Math.sin((phase + i) * 3.4) * 0.05
      const material = (wisp as THREE.Mesh).material as THREE.MeshBasicMaterial
      material.opacity = 0.16 * Math.sin(phase * Math.PI)
    }
    if (mood === 'halted') {
      for (const eye of station.fly.eyes) (eye.material as THREE.MeshStandardMaterial).emissiveIntensity = 0.06
    }
  }

  const renderFrame = (dt: number) => {
    for (const station of stations) animateWings(station, dt)
    if (!reduced) {
      const fit = Math.max(0.86, Math.min(1.34, 1400 / width))
      camera.position.x += (pointer.x * 0.55 - camera.position.x) * 0.045
      camera.position.y += (cameraHeight * fit + pointer.y * -0.28 - camera.position.y) * 0.045
      camera.lookAt(0, LOOK_AT_Y, 0)
    }
    renderer.render(scene, camera)
  }

  const loop = () => {
    if (disposed) return
    frame = requestAnimationFrame(loop)
    const now = performance.now()
    const dt = Math.min(0.05, (now - lastTime) / 1000)
    lastTime = now
    if (paused) return
    renderFrame(dt)
  }

  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries.some((entry) => entry.isIntersecting)
      if (visible === paused) {
        paused = !visible
        lastTime = performance.now()
      }
    },
    { threshold: 0.02 },
  )
  observer.observe(container)

  const onVisibility = () => {
    paused = document.hidden || paused
    lastTime = performance.now()
  }
  document.addEventListener('visibilitychange', onVisibility)
  // The container is the source of truth for size: it changes with the layout, not only
  // with the window.
  const resizeObserver = new ResizeObserver(applySize)
  resizeObserver.observe(container)
  applySize()
  lastTime = performance.now()
  frame = requestAnimationFrame(loop)

  const ndcBounds = (object: THREE.Object3D) => {
    const box = new THREE.Box3().setFromObject(object)
    const projected: [number, number][] = []
    for (const x of [box.min.x, box.max.x]) {
      for (const y of [box.min.y, box.max.y]) {
        for (const z of [box.min.z, box.max.z]) {
          const ndc = new THREE.Vector3(x, y, z).project(camera)
          projected.push([ndc.x, ndc.y])
        }
      }
    }
    const round = (value: number) => Number(value.toFixed(3))
    return {
      minX: round(Math.min(...projected.map((p) => p[0]))),
      maxX: round(Math.max(...projected.map((p) => p[0]))),
      minY: round(Math.min(...projected.map((p) => p[1]))),
      maxY: round(Math.max(...projected.map((p) => p[1]))),
    }
  }

  /**
   * Is this object actually visible from the camera, or is something in front of it?
   *
   * Projecting a position into the frame only proves it is inside the frustum: a mesh
   * behind an opaque floor still projects perfectly. This casts a ray through the object's
   * centre and reports which named object is hit first, which is the check that catches an
   * occluded fly.
   */
  const raycaster = new THREE.Raycaster()
  const visibilityOf = (object: THREE.Object3D) => {
    const box = new THREE.Box3().setFromObject(object)
    const centre = box.getCenter(new THREE.Vector3())
    const ndc = centre.clone().project(camera)
    raycaster.setFromCamera(new THREE.Vector2(ndc.x, ndc.y), camera)
    const hit = raycaster
      .intersectObjects(scene.children, true)
      .find((entry) => entry.object.visible)
    let node: THREE.Object3D | null = hit?.object ?? null
    while (node && !node.name && node.parent) node = node.parent
    return {
      hit: node?.name || 'unnamed',
      distance: hit ? Number(hit.distance.toFixed(2)) : null,
      centre: [Number(ndc.x.toFixed(3)), Number(ndc.y.toFixed(3))],
    }
  }

  const publishDiagnostics = () => {
    container.dataset.floor = JSON.stringify({
      render: { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles },
      camera: camera.position.toArray().map((value) => Number(value.toFixed(2))),
      stations: stations.map((station) => {
        const project = (object: THREE.Object3D) => {
          const vector = new THREE.Vector3()
          object.getWorldPosition(vector)
          const ndc = vector.project(camera)
          return [Number(ndc.x.toFixed(3)), Number(ndc.y.toFixed(3))]
        }
        return {
          side: station.side,
          mood: station.mood,
          fly: project(station.fly.body),
          screen: project(station.terminal.screen),
          wing: Number(station.fly.leftWing.rotation.z.toFixed(3)),
          flyNdc: ndcBounds(station.fly.root),
          // Worst case for clipping: the widest the wings can sweep, measured by
          // straightening them, so a frame mid-beat cannot quietly go off the edge.
          flyNdcWorst: (() => {
            const saved = [station.fly.leftWing.rotation.z, station.fly.rightWing.rotation.z]
            station.fly.leftWing.rotation.z = 0
            station.fly.rightWing.rotation.z = 0
            const bounds = ndcBounds(station.fly.root)
            station.fly.leftWing.rotation.z = saved[0]
            station.fly.rightWing.rotation.z = saved[1]
            return bounds
          })(),
          deskNdc: ndcBounds(station.desk),
          lampNdc: ndcBounds(station.lamp),
          cupNdc: ndcBounds(station.cup),
          visible: {
            fly: visibilityOf(station.fly.root),
            screen: visibilityOf(station.terminal.screen),
            keyboard: visibilityOf(station.keyboard.group),
          },
          typing: station.fly.typingTips.map((tip) => {
            const world = new THREE.Vector3()
            tip.getWorldPosition(world)
            const local = station.keyboard.group.worldToLocal(world.clone())
            return {
              local: [Number(local.x.toFixed(2)), Number(local.y.toFixed(2)), Number(local.z.toFixed(2))],
              overKeys: Math.abs(local.x) < 1.02 && Math.abs(local.z) < 0.34,
            }
          }),
        }
      }),
    })
  }

  return {
    update(next: FloorState) {
      const previous = state
      state = next
      stations.forEach((station, index) => {
        const arm = next.arms[index]
        if (!arm) return
        const changed = !previous || previous.arms[index].side !== arm.side || previous.bar !== next.bar
        station.mood = arm.mood
        if (changed && (arm.mood === 'buy' || arm.mood === 'sell' || arm.mood === 'veto')) {
          station.pulse = 1
        }
        drawTerminal(station.terminal, arm, next, clock)
      })
      // New state always draws a frame here rather than waiting for the animation loop.
      // The loop is paused while the floor is off-screen or the tab is hidden, and the
      // browser throttles animation frames in background tabs — either way a changed bar
      // has to reach the screen immediately instead of showing stale terminal contents.
      renderFrame(0)
      const now = performance.now()
      if (now - lastDiagnostics > 500) {
        lastDiagnostics = now
        publishDiagnostics()
      }
    },
    setPaused(next: boolean) {
      paused = next
      lastTime = performance.now()
    },
    diagnostics() {
      const project = (object: THREE.Object3D) => {
        const vector = new THREE.Vector3()
        object.getWorldPosition(vector)
        const ndc = vector.clone().project(camera)
        return { x: Number(ndc.x.toFixed(3)), y: Number(ndc.y.toFixed(3)), z: Number(ndc.z.toFixed(3)) }
      }
      let triangles = 0
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh
        if (mesh.isMesh && mesh.geometry) {
          const geometry = mesh.geometry as THREE.BufferGeometry
          triangles += geometry.index ? geometry.index.count / 3 : (geometry.attributes.position?.count ?? 0) / 3
        }
      })
      return {
        stations: stations.map((station) => ({
          side: station.side,
          fly: project(station.fly.body),
          screen: project(station.terminal.screen),
          wings: [
            Number(station.fly.leftWing.rotation.z.toFixed(3)),
            Number(station.fly.rightWing.rotation.z.toFixed(3)),
          ],
          mood: station.mood,
        })),
        camera: camera.position.toArray().map((v) => Number(v.toFixed(2))),
        canvas: [renderer.domElement.width, renderer.domElement.height],
        triangles: Math.round(triangles),
        render: { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles },
        paused,
        reducedMotion: reduced,
      }
    },
    dispose() {
      disposed = true
      cancelAnimationFrame(frame)
      observer.disconnect()
      resizeObserver.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pointermove', onPointerMove)
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh
        if (!mesh.isMesh) return
        mesh.geometry?.dispose()
        const material = mesh.material as THREE.Material | THREE.Material[]
        for (const entry of Array.isArray(material) ? material : [material]) {
          const mapped = entry as THREE.MeshStandardMaterial
          mapped.map?.dispose()
          entry.dispose()
        }
      })
      environment.texture.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    },
  }
}
