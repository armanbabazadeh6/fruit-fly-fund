# Progress

Running log so the desktop session can pick up exactly where the laptop stopped.
Newest entries at the top.

## Status: vertical slice complete, first three-season result recorded

The two-arm competition runs on the real MaleCNS v1.0 engine on the M2 Air, the browser
experience renders recorded and live runs, and three seasons of real public BTC-USDC data
have been run end to end. What remains is more seasons on better hardware, plus the
controls listed below.

### First result

`flyvsly run --engine neural --market coinbase --bars 48 --repeats 3` — 48 one-minute
candles per season, three disjoint windows, both flies on the retained graph:

| | memory ON | memory OFF | buy & hold |
| --- | --- | --- | --- |
| mean return | −1.000% | −0.908% | −0.765% |
| paired delta | mean −0.092%, spread ±0.095%, range −0.225% … −0.009% | | |
| seasons won | 0 | 3 | — |
| changed KC→MBON efficacies | mean 3,442 | 0 (frozen) | — |

The mechanism fires and the experiments differ; the memory rule did not help in these three
seasons, and the paired difference is smaller than its own spread. Archived as recordings
`20260910-143345-r0`, `-143909-r1`, `-144513-r2` and published into
`web/public/recordings/`.

### Done

- **Upstream foundation.** Stonkfly vendored unmodified at commit
  `78ef3e05ab0fa086032098558d893667068944a0` under `vendor/`, MIT license and
  `THIRD_PARTY` preserved, attribution recorded in `UPSTREAM.md`. Coinbase execution
  modules are vendored but never imported; the SDKs are deliberately not dependencies, so
  the project cannot place an order.
- **Engine works on 8 GB.** `flyvsly prepare` downloads 1.1 GB, verifies SHA-256 locks and
  compiles the retained graph in 68.6 s with 1.33 GB peak RSS. 166,700 neurons,
  25,582,938 directed connections, 7,835 plastic KC→MBON efficacies.
- **Two-arm arena.** `flyvsly/arena.py`: one cached market season, one RGB frame per bar
  shared by both arms, one SQLite ledger each, arms on separate threads (the native kernel
  releases the GIL). Only `Settings.learning` differs, asserted at runtime by
  `flyvsly/fairness.py`.
- **Benchmarks.** Buy & hold and cash on the same bars and fees, with the flies' execution
  limits stated as an asymmetry in the UI rather than hidden.
- **Telemetry.** Per bar: spikes (DNp20 L/R, gate, KC, reward, aversive, total), neural vs
  wall time, memory changed-edge count and efficacy, the decision explanation with the
  measured values and the programmed rule, the order plan, the fill, and the portfolio.
  Written to `recording.json`, `manifest.json` and `observations.jsonl`.
- **Repetition.** `--repeats N` uses N disjoint seasons (different seeds, or whole-season
  window steps for Coinbase data). `flyvsly report` prints paired deltas, spread and win
  counts, with a note that few seasons cannot settle anything.
- **Server.** Standard library only, localhost, static bundle + JSON API + SSE live feed,
  plus `POST /api/runs` so the browser can start a run.
- **Browser experience.** Two animated flies at desks, live scoreboard, equity and price
  charts with benchmarks, per-bar neural telemetry, trade history, per-trade decision
  explainer, rivalry commentary labelled as entertainment, and a provenance panel with the
  fairness hash, the hardware and the disclaimers.
- **Procedural demo mode.** Clearly labelled in the recording, the API and every panel; it
  emits no spike fields at all, so it cannot be mistaken for the fly.
- **Tests.** 33 fast tests (fairness, execution parity against upstream's guard, market
  seasons, telemetry explanations, procedural backend) plus 5 gated full-engine tests that
  pass in 29 s: both arms on the real 166,700-neuron graph, byte-identical retinal input per
  bar, the control arm's efficacies frozen at 0 changed edges while the experimental arm's
  move, and the neural arm's output unchanged when handed a different price history.
- **Publishing.** `flyvsly publish` copies recordings next to the bundle so a static host
  shows real neural runs; `docs/screenshot.png` and `docs/screenshot-desks.png` are captured
  from the served page against the recorded neural run.

### Measured on the M2 Air (8 GB)

| | |
| --- | --- |
| prepare | 68.6 s, 1.33 GB peak RSS, 1.6 GB data dir |
| two brains resident | ~0.6–0.7 GB |
| one 500 ms neural observation | 2–8 s per fly, both arms on two threads |
| 48-bar season | ~5–11 min wall |

See `docs/hardware.md`.

### 3D trading floor

Two modeled flies at their desks, each in front of an amber terminal, with a wall board
behind them showing the market and both equity curves. Built in `web/src/three/`:

- `models.ts` — procedural low-poly fly (thorax, abdomen, head, compound eyes, antennae,
  six two-segment legs, veined translucent wings), desk, terminal with a canvas-texture
  screen, lamp with a real spotlight, mug, and steam. No downloaded assets.
- `floor.ts` — scene, camera, lighting (RoomEnvironment IBL, shadow-casting key light,
  per-station lamp spot and monitor glow), the render loop, and the terminal/wall-board
  painters. The floor pauses while off-screen and draws one frame on every state update.
- `TradingFloor.tsx` — React wrapper: WebGL detection, lazy `import()` of three.js, the
  per-fly readout strip, an HTML ticker tape, and the 2D desk fallback when WebGL is absent.
- `floor-check.html` + `src/floor-check.ts` — a harness that renders the scene alone with
  framing controls (`?spread=&distance=&height=&fly=`) and prints what it drew, so camera
  changes are made from measurements rather than by eye.

Cost: three.js is split into its own 124 KB (gzip) chunk, fetched only after WebGL is
confirmed. The scene is 104 draw calls and ~11k triangles; it animates wings, posture,
lamps, steam and pointer parallax, and stops entirely under `prefers-reduced-motion`.

Framing was chosen from measured NDC footprints, since a headless check cannot look at the
picture: with `spread=3.5, distance=8.6, height=3.4, flyScale=0.72` the flies sit at
|x| ≈ 0.41, their screens at |x| ≈ 0.56, lamps and mugs stay inside |x| < 0.9, and the desk
fronts bleed off the bottom edge on purpose. The rendered stage measures mean luminance 59
with 20k distinct colours — lit, contrasted, and not crushed to black.

Three defects surfaced while verifying it, all fixed:

1. The diagnostics publish compared `performance.now()` against a zero baseline, so the
   first (and for a paused recording, only) update never published.
2. A paused or off-screen floor never redrew when new state arrived, so scrolling back
   showed stale terminal contents. `update()` now always draws a frame.
3. Vite silently dropped the harness page's script tag when the `rollupOptions.input` key
   matched the emitted chunk name. Renaming the key fixed it.

### Live streaming, verified end to end

`flyvsly serve` runs the arena in a thread and streams it over Server-Sent Events. A page
that loads mid-run attaches at the live edge: the hub replays the run metadata, the season
bars, both arm descriptions and every bar so far, then continues live, and the client
dedupes by bar index. Badge lifecycle, verified by driving the real page:

| state | badge |
| --- | --- |
| idle server, recorded run loaded | `recorded neural run` |
| run starting (neural, ~10 s of brain building) | `live neural run — starting` |
| bars streaming | `live neural run — streaming` |
| run finished, handed to its recording | `recorded neural run` |
| procedural engine at any point | `… procedural … not neural` |

Two defects were found and fixed while verifying this, both recorded here because they were
only visible with a real browser and a real run in flight:

1. The live view rendered before `arms_ready` arrived, so a run started from the UI blanked
   the page. The live view is now gated on both arms and the season being present, and the
   previous recording stays on screen with a "starting" badge until then.
2. The replay session outlived the run, so a page loaded *after* a run finished attached to
   it as if it were still streaming. The hub now clears the replay buffer when a run ends;
   finished runs are served from their recording.

### Verified in a browser

Loaded the built bundle against `flyvsly serve` and against a plain static server:

- Zero console or page errors on both paths.
- Header badge reads "recorded neural run" for a real run; the offline fallback reads
  "procedural demo — not neural" and its telemetry panels render momentum score, threshold,
  volatility and lookback instead of spikes, with no spike field anywhere in the DOM.
- Both decision explainers show the same `input_sha256` for a bar (proof the two flies were
  shown identical market input) alongside different `spike_sha256` values, the measured
  Hz/threshold/gate values, the verbatim rule, and the order plan.
- All sampled text meets WCAG AA contrast after the palette fix (lowest measured 4.97:1).
- No panel overflow, no horizontal page overflow, all four equity series painted.

### Open, in priority order

1. **Run longer seasons on the desktop.** The laptop can afford 48 bars per season; 480
   would be ~10× the wall time. More bars is the single biggest improvement to the
   experiment, and `--repeats` should go to 6+ so the paired spread is meaningful.
2. **Shuffled-reinforcement control.** Upstream's list of what would count as learning
   includes it and we do not have it: same memory rule, but the reward pulses permuted in
   time. Without it, a memory-on difference cannot be separated from "any pulse at all
   changed the weights".
3. **Held-out replay.** Seasons are currently consumed in one pass with no train/test split,
   so the current output is descriptive only. Any claim needs a frozen-weight evaluation on
   later, unseen seasons.
4. **Retention and reset tests.** Does a difference persist into a new season with learning
   frozen? Does resetting the 7,835 efficacies remove it? Both are cheap to add on top of
   the existing arena: `MemoryBrain.checkpoint`/`restore`/`reset` already support it.
5. **Aggregate the report into the UI.** `flyvsly report` output exists and the header shows
   a one-line summary, but the per-season paired spread deserves its own chart.
7. **Longer Coinbase history.** The public endpoint here serves a fixed recent window;
   `--start` accepts any ISO window the exchange still has, which is how to reach further
   back for more independent seasons.

### Exact commands used

```sh
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e '.[test]'
flyvsly prepare
flyvsly run --engine neural --market coinbase --bars 48 --repeats 3 --label season-coinbase-48
flyvsly run --engine neural --bars 24 --seed 5
flyvsly run --engine procedural --bars 480 --seed 21
flyvsly report
python -m pytest -q
FLYVSLY_NEURAL_TEST=1 python -m pytest -q -m slow
cd web && npm install && npm run build && cd .. && flyvsly serve
```

### Traps hit, so the desktop session does not hit them again

- **Darwin `ru_maxrss` is bytes**, Linux is kilobytes. The probe code divides accordingly;
  do not "fix" the units.
- **SQLite connections are thread-bound.** Each arm's ledger must be created on the thread
  that uses it; that is why `_ArmThread` builds its own `Arm`.
- **The Coinbase candle endpoint ignores `end` when used alone.** Always send both `start`
  and `end`. It also omits minutes with no trades, so a window can come back short and the
  fetch widens it backwards.
- **Do not anchor any window to the local clock.** Seasons are anchored to the exchange's
  newest completed candle, which is also why a recording replays identically on another
  machine.
- **`--fast`-style wall skipping and upstream's 60 s cooldown cannot coexist** on a
  one-minute bar: the cooldown would veto nearly every order. Market time is advanced
  explicitly instead (see `docs/fairness.md`).
