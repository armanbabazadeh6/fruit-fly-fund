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
import { buildOffice } from './office'
import { drawTerminal } from './terminal'
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
  brainActivity?: number[]
  /** Measured spike counts of the recorded population for this bar, in `cellTypes` order. */
  population?: number[]
  /** The run's cell identities, so the brain can draw one dot per measured cell. */
  cellTypes?: string[]
  halted: boolean
  /** Most recent fills at or before the current bar, newest first, for the desk tape. */
  trades: { side: string; label: string }[]
  /** The last actual fill, which is what the screen reacts to. */
  lastFill: { i: number; side: string; base: string; price: string } | null
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
  /** Trade reaction: the id of the last event seen and when its flash started. */
  eventId: string
  flashStart: number
  lastFlash: number
  arm: FloorArmState | null
  state: FloorState | null
}

export interface FloorHandle {
  update(state: FloorState): void
  setPixelMode(enabled: boolean): void
  setTradeCam(enabled: boolean): void
  setPaused(paused: boolean): void
  setBrainMode(enabled: boolean): void
  setView(view: 'floor' | 'gordon' | 'warren'): void
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
  const spread = options.spread ?? 2.75
  const cameraDistance = options.distance ?? 8.6
  const cameraHeight = options.height ?? 3.4
  const fov = options.fov ?? 34
  const flyScale = options.flyScale ?? 1.06
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.3
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.width = '100%'
  renderer.domElement.style.height = '100%'
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x070b11)
  // Far enough that the wall board (which sits behind the desks) is not washed out.
  scene.fog = new THREE.Fog(0x6d859a, 35, 95)

  const pmrem = new THREE.PMREMGenerator(renderer)
  const environment = pmrem.fromScene(new RoomEnvironment(), 0.05)
  scene.environment = environment.texture
  scene.environmentIntensity = .38
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
  const rim = new THREE.DirectionalLight(0x71e3cf, 2.3)
  rim.position.set(0, 4, -3)
  scene.add(rim)
  scene.add(buildOffice())

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(60, 34),
    new THREE.MeshStandardMaterial({ color:0x263d3c, map: gridTexture(), roughness: 0.72, metalness: 0.3 }),
  )
  // The floor sits at the height of the desk feet. It used to sit at y = 0 while the
  // stations were pushed 1.02 below it, which buried both flies under an opaque plane.
  floor.rotation.x = -Math.PI / 2
  floor.position.y = DESK_FEET_Y
  floor.receiveShadow = true
  floor.name = 'floor'
  scene.add(floor)

  // Retro pass: render the whole scene into a small target, then upscale it with
  // nearest-neighbour filtering and a quantised palette so it reads as 8-bit pixel art.
  // The terminal screens lose their legibility at this resolution, which is why the live
  // numbers are repeated in HTML above the canvas.
  const pixel = { enabled: false, scale: 3.6, levels: 9 }
  // Trade cam follows whichever fly just traded: the camera eases toward that desk for a
  // few seconds, then returns. Purely a framing move — it changes nothing about the run.
  const tradeCam = { enabled: false, focus: -1, until: -99, x: 0, zoom: 1 }
  let view: 'floor' | 'gordon' | 'warren' = 'floor'
  let brainMode = false
  let target: THREE.WebGLRenderTarget | null = null
  const quadCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)
  const quadMaterial = new THREE.ShaderMaterial({
    uniforms: {
      tDiffuse: { value: null },
      levels: { value: pixel.levels },
      pixels: { value: new THREE.Vector2(1, 1) },
    },
    vertexShader: `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = vec4(position.xy, 0.0, 1.0);
      }
    `,
    fragmentShader: `
      uniform sampler2D tDiffuse;
      uniform float levels;
      varying vec2 vUv;
      // 4x4 Bayer matrix: dithering before quantisation stops gradients banding, which is
      // what separates 8-bit art from a posterised photograph.
      float bayer(vec2 position) {
        int x = int(mod(position.x, 4.0));
        int y = int(mod(position.y, 4.0));
        int index = x + y * 4;
        float matrix[16];
        matrix[0] = 0.0;  matrix[1] = 8.0;  matrix[2] = 2.0;  matrix[3] = 10.0;
        matrix[4] = 12.0; matrix[5] = 4.0;  matrix[6] = 14.0; matrix[7] = 6.0;
        matrix[8] = 3.0;  matrix[9] = 11.0; matrix[10] = 1.0; matrix[11] = 9.0;
        matrix[12] = 15.0; matrix[13] = 7.0; matrix[14] = 13.0; matrix[15] = 5.0;
        float value = 0.0;
        for (int i = 0; i < 16; i++) {
          if (i == index) value = matrix[i];
        }
        return value / 16.0 - 0.5;
      }
      void main() {
        vec3 colour = texture2D(tDiffuse, vUv).rgb;
        float offset = bayer(gl_FragCoord.xy) / levels;
        colour = floor((colour + offset) * levels + 0.5) / levels;
        // A touch of extra contrast so the darks stay inky, as in 16-bit console art.
        colour = clamp((colour - 0.5) * 1.08 + 0.5, 0.0, 1.0);
        gl_FragColor = vec4(colour, 1.0);
      }
    `,
    depthTest: false,
    depthWrite: false,
  })
  const quadScene = new THREE.Scene()
  quadScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), quadMaterial))

  const buildTarget = () => {
    target?.dispose()
    target = new THREE.WebGLRenderTarget(
      Math.max(80, Math.round(width / pixel.scale)),
      Math.max(60, Math.round(height / pixel.scale)),
      { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, depthBuffer: true },
    )
    quadMaterial.uniforms.pixels.value.set(target.width, target.height)
  }

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
    fly.root.position.set(-side * 0.3, 0.12, 0.1)
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
    // Depth chosen so the whole keyboard stays on the desk top (which spans z -0.95..0.95)
    // while the forelegs, whose reach is fixed by the shoulder height, land mid-deck.
    keyboard.group.position.set(-side * 0.06, 0.09, 0.65)
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
      eventId: '',
      flashStart: -99,
      lastFlash: 0,
      arm: null,
      state: null,
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
    buildTarget()
  }

  const onPointerMove = (event: PointerEvent) => {
    const rect = renderer.domElement.getBoundingClientRect()
    pointer.x = ((event.clientX - rect.left) / rect.width - 0.5) * 2
    pointer.y = ((event.clientY - rect.top) / rect.height - 0.5) * 2
  }
  if (!reduced) window.addEventListener('pointermove', onPointerMove)

  /** 1 at the moment of the event, decaying to 0 over the given duration. */
  const flashLevel = (station: Station) => {
    // Long enough to be unmissable, short enough that an active season does not strobe.
    const duration = station.eventId.startsWith('fill') ? 1.6 : 1.0
    if (station.flashStart < 0) return 0
    return Math.max(0, Math.min(1, 1 - (clock - station.flashStart) / duration))
  }

  const animateWings = (station: Station, dt: number) => {
    const mood = station.mood
    const busy = mood === 'buy' || mood === 'sell'
    // The two flies used to flap at wildly different rates — one manic, one still — because
    // an active bar ran at 34 rad/s against an idle 6.4. Both now buzz, with the difference
    // legible but not a wind machine.
    const speed = mood === 'halted' ? 0 : busy ? 19 : 8
    const amplitude = mood === 'halted' ? 0 : busy ? 0.5 : 0.22
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
    for (const row of station.keyboard.caps) for (const cap of row) {
      (cap.material as THREE.MeshStandardMaterial).emissiveIntensity = 0
    }
    station.fly.brain.update(
      station.arm?.population ?? [],
      reduced ? 0 : clock,
      station.arm?.cellTypes,
    )
    station.fly.typingArms.forEach((arm, index) => {
      const strike = Math.max(0, Math.sin(tapPhase + index * Math.PI))
      arm.rotation.set(0,0,0)
      const row = station.keyboard.caps[2]
      const cap = row?.[index === 0 ? 4 : 9 + ((Math.floor(clock * tapRate) % 3) - 1)]
      if (cap) {
        // Solve each articulated foreleg to the actual key position, including desk,
        // keyboard and fly transforms. The foot now physically reaches the lit key.
        station.fly.root.updateWorldMatrix(true,true)
        station.keyboard.group.updateWorldMatrix(true,true)
        const keyPosition = cap.getWorldPosition(new THREE.Vector3())
        keyPosition.y += .025 + (1-strike)*.10
        const target = arm.worldToLocal(keyPosition)
        const elbow = target.clone().multiplyScalar(.48)
        elbow.x += index===0 ? -.15 : .15
        elbow.y += .13
        const wrist = target.clone().lerp(elbow,.12)
        const points = [new THREE.Vector3(),elbow,wrist,target]
        const lengths = [.26,.22,.1]
        for(let segment=0;segment<3;segment++) {
          const bone=arm.children[segment]
          const delta=points[segment+1].clone().sub(points[segment])
          bone.position.copy(points[segment]).add(points[segment+1]).multiplyScalar(.5)
          bone.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),delta.clone().normalize())
          bone.scale.y=delta.length()/lengths[segment]
        }
        station.fly.typingTips[index].position.copy(target)
        arm.children[4].position.copy(elbow)
        arm.children[5].position.copy(wrist)
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

    // A trade reaction is drawn on the screen itself, and the screen has to keep being
    // repainted while it decays: the terminal is a texture, not a DOM element.
    const flash = flashLevel(station)
    if ((flash > 0.001 || station.lastFlash > 0.001) && station.arm && station.state) {
      drawTerminal(station.terminal, station.arm, station.state, clock, flash)
    }
    station.lastFlash = flash
  }

  const renderFrame = (dt: number) => {
    clock += reduced ? 0 : dt
    for (const station of stations) animateWings(station, reduced ? 0 : dt)
    {
      const fit = Math.max(0.86, Math.min(3.4, 1.65 / camera.aspect))
      const focused = tradeCam.enabled && tradeCam.focus >= 0 && tradeCam.until > clock
      const selected = view === 'gordon' ? -1 : view === 'warren' ? 1 : 0
      const wantX = selected ? selected * spread : focused ? stations[tradeCam.focus].side * spread * .6 : 0
      const wantZoom = selected ? .61 : focused ? .9 : 1
      tradeCam.x += (wantX - tradeCam.x) * (reduced ? 1 : .065)
      tradeCam.zoom += (wantZoom - tradeCam.zoom) * (reduced ? 1 : .065)
      const distance = cameraDistance * fit * tradeCam.zoom
      camera.position.x += (tradeCam.x + pointer.x * 0.55 - camera.position.x) * 0.045
      camera.position.y += (cameraHeight * fit * tradeCam.zoom + pointer.y * -0.28 - camera.position.y) * 0.045
      camera.position.z += (distance - camera.position.z) * 0.045
      camera.lookAt(tradeCam.x * (selected ? 1 : .6), selected ? .7 : LOOK_AT_Y, 0)
    }
    if (pixel.enabled && target) {
      renderer.setRenderTarget(target)
      renderer.render(scene, camera)
      renderer.setRenderTarget(null)
      quadMaterial.uniforms.tDiffuse.value = target.texture
      quadMaterial.uniforms.levels.value = pixel.levels
      renderer.render(quadScene, quadCamera)
    } else {
      renderer.render(scene, camera)
    }
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
    const bounds = container.getBoundingClientRect()
    paused = document.hidden || bounds.bottom <= 0 || bounds.top >= window.innerHeight
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

  /**
   * One diagnostics object, used by both the DOM channel and the debug hook.
   *
   * There used to be two of these — one for the `data-floor` attribute and one behind
   * `window.__flyvslyFloor` — and they drifted, which is how a "the flies are gone" bug
   * survived a green check. Anything reported about the scene lives here.
   */
  const buildDiagnostics = () => {
    let triangles = 0
    scene.traverse((object) => {
      const mesh = object as THREE.Mesh
      if (mesh.isMesh && mesh.geometry) {
        const geometry = mesh.geometry as THREE.BufferGeometry
        triangles += geometry.index
          ? geometry.index.count / 3
          : (geometry.attributes.position?.count ?? 0) / 3
      }
    })
    return {
      pixelArt: { ...pixel },
      brainMode,
      view,
      tradeCam: {
        enabled: tradeCam.enabled,
        focus: tradeCam.focus,
        x: Number(tradeCam.x.toFixed(2)),
        zoom: Number(tradeCam.zoom.toFixed(3)),
      },
      render: { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles },
      camera: camera.position.toArray().map((v) => Number(v.toFixed(2))),
      canvas: [renderer.domElement.width, renderer.domElement.height],
      triangles: Math.round(triangles),
      paused,
      reducedMotion: reduced,
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
          brain: { cells: station.fly.brain.cellCount, lit: station.fly.brain.litCount },
          lampNdc: ndcBounds(station.lamp),
          cupNdc: ndcBounds(station.cup),
          visible: {
            fly: visibilityOf(station.fly.root),
            screen: visibilityOf(station.terminal.screen),
            keyboard: visibilityOf(station.keyboard.group),
          },
          flash: Number(flashLevel(station).toFixed(2)),
          event: station.eventId,
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
    }
  }

  const publishDiagnostics = () => {
    container.dataset.floor = JSON.stringify(buildDiagnostics())
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
        station.arm = arm
        station.state = next
        if (changed && (arm.mood === 'buy' || arm.mood === 'sell' || arm.mood === 'veto')) {
          station.pulse = 1
        }
        // React to the event, not to the bar: a fill of the same bar must not re-trigger.
        const eventId = arm.lastFill
          ? `fill:${arm.lastFill.i}`
          : arm.exec === 'VETO'
            ? `veto:${next.bar}`
            : arm.exec === 'BLOCKED'
              ? `blocked:${next.bar}`
              : ''
        if (eventId && eventId !== station.eventId) {
          station.eventId = eventId
          station.flashStart = clock
          if (tradeCam.enabled && eventId.startsWith('fill')) {
            tradeCam.focus = index
            tradeCam.until = clock + 3.4
          }
        }
        drawTerminal(station.terminal, arm, next, clock, flashLevel(station))
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
    setPixelMode(enabled: boolean) {
      if (pixel.enabled === enabled) return
      pixel.enabled = enabled
      renderer.setPixelRatio(enabled ? 1 : Math.min(window.devicePixelRatio || 1, 2))
      applySize()
      renderFrame(0)
      publishDiagnostics()
    },
    setBrainMode(enabled: boolean) {
      brainMode = enabled
      for (const station of stations) station.fly.brain.root.visible = enabled
      renderFrame(0)
      publishDiagnostics()
    },
    setView(next) { view = next; renderFrame(0); publishDiagnostics() },
    setTradeCam(enabled: boolean) {
      tradeCam.enabled = enabled
      if (!enabled) {
        tradeCam.focus = -1
        tradeCam.until = -99
      }
      renderFrame(0)
      publishDiagnostics()
    },
    setPaused(next: boolean) {
      paused = next
      lastTime = performance.now()
    },
    diagnostics() {
      return buildDiagnostics()
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
        if (!mesh.geometry) return
        mesh.geometry?.dispose()
        const material = mesh.material as THREE.Material | THREE.Material[]
        for (const entry of Array.isArray(material) ? material : [material]) {
          const mapped = entry as THREE.MeshStandardMaterial
          mapped.map?.dispose()
          entry.dispose()
        }
      })
      environment.texture.dispose()
      target?.dispose()
      quadMaterial.dispose()
      ;(quadScene.children[0] as THREE.Mesh).geometry.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    },
  }
}
