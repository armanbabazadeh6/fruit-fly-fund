# Progress

Running log so the desktop session can pick up exactly where the laptop stopped.
Newest entries at the top.

## RESUME HERE

Paused with a clean tree at `9ffce9c`. The two remaining exam runs (`exam-reset`,
`exam-readout`) were still going when we stopped; if they finished, `runs/` has them and
`results/report.md` needs committing.

**Two bugs found in the exam feature, both fixed**

1. `personas_for()` relabelled the experimental fly "trained brain, frozen" but left
   `learning=True` on it, so the exam learned *during* the exam — the opposite of the protocol —
   while the recording claimed `both_frozen: true`. That is the worst failure mode this project
   has: a recording asserting a property its run did not have. Both exam personas now freeze
   both flies, and `assert_exam_is_fair` inspects the arms' settings instead of restating the
   intention: it raises if either arm can learn, or if anything but the starting weights differs.
2. `starting_conditions()` built its comparison from a hardcoded learning pair rather than the
   settings the run would use, so an exam described a run that was not the one being launched.
   The guard from fix (1) caught this immediately on the next attempt and refused to start the
   run — which is what a guard is for. The arena now passes the settings its personas produce.

Both are tested, including the case where the wrong settings are handed in. The two invalid
recordings were deleted rather than reported.

**Docs corrected after an audit**

A read-only audit checked every number and claim in the docs against the recordings. Fixed:
the two campaigns' "disjoint seasons" claim (they overlap, and the spread across them
understates market variation), a `docs/fairness.md` table describing a `--checkpoint-every`
flag that does not exist, a `docs/hardware.md` line claiming different default bar counts per
engine, a README test count of 33 when the suite is 124, and a `docs/telemetry.md` schema
example that reused a real run id with numbers that were not that run's.

**Where the three requested features stand**

1. **Exam (held-out)** — built and tested, but **not yet run validly** (see the bug above).
   What is verified on real data: a training run learned on the newest season and checkpointed
   both brains, and an exam applies a trained checkpoint, records its sha256 and reports
   `changed_before_reset: 3386`. Re-run next session with the fix in place; the earlier 12-bar
   numbers are void and were deleted.
2. **Reward timing** — `--reinforcement pnl|decoy|shuffled|none` all work. `pnl` is measured
   over three seasons with the busy preset: memory-on −1.464%, memory-off −1.180%, paired
   −0.284%, 1 win in 3, both behind buy & hold. `decoy`, `shuffled` and `none` have not been
   run to completion yet.
3. **A readout fitted from the fly** — built, tested, and fitted once for real. It recorded a
   memorised fit: training accuracy 1.0 on 34 bars with 256 features, holdout 0.583 against a
   base rate of 0.583 on 12 bars. `fitreadout` warns about exactly this. A fit worth reading
   needs many more seasons.

**Next, in order**

1. Re-run the exams with the fix in place. Brains from the newest season already exist, so
   the exam and the reset can be run directly:

   ```sh
   PY=.venv/bin/python
   for spec in 'exam-fixed:trained:exam' 'exam-reset:reset:reset'; do
     label=${spec%%:*}; rest=${spec#*:}; start=${rest%%:*}; kind=${rest##*:}
     $PY -m flyvsly run --engine neural --market coinbase --bars 48 --preset scalper \
       --kind $kind --window-offset 48 --label $label --out runs \
       --starting gordon=$start:runs/brains/train/gordon.npz --starting warren=baseline
   done
   ```

   The readout exam still needs `--readout models/readout.json --readout-margin 5.66`, and the
   margin check that rejected it is fixed. On the desktop, prefer
   `BRAINS=runs/brains/train2 scripts/experiment.sh 48 4 scalper`.
2. Run the remaining reinforcement modes (`decoy`, `shuffled`, `none`) on the same seasons.
3. Refit the readout from those seasons and re-run the readout exam.
4. Commit `results/report.md` and update the README's result section with the new table.

## Status: vertical slice complete, first three-season result recorded

The two-arm competition runs on the real MaleCNS v1.0 engine on the M2 Air, the browser
experience renders recorded and live runs, and three seasons of real public BTC-USDC data
have been run end to end. What remains is more seasons on better hardware, plus the
controls listed below.

### First result

`flyvsly run --engine neural --market coinbase --bars 48 --repeats 3` — 48 one-minute
candles per season, three windows that **overlap their neighbours** — the offset between them
counted minutes while a 48-bar season spans ~64, so each shared about 12 bars with the next.
Within-season comparisons are unaffected (both flies trade the same bars); cross-season
independence is not what this table's spread suggests. Row-counted offsets and a
`--must-not-overlap` guard exist now, and the runs below predate them:

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
- **Repetition.** `--repeats N` uses N seasons (different synthetic seeds, or a whole-season
  row-counted step for Coinbase data, plus `--must-not-overlap` to prove the season is
  disjoint from the run it is graded against). Runs made before the row-counted offset overlap
  their neighbours, which is recorded here rather than quietly re-scored.
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

The fly carries the anatomy the connectome actually has: banded abdomen with ring segments,
bristles placed from a deterministic hash, halteres behind the wings, tarsi on every leg,
faceted compound eyes drawn to a canvas texture, and a shaded grey shell. Middle and hind
legs rest on the desk; the forelegs are rigged as typing arms whose tarsi land on individual
key caps, and the key under each arm lights as it strikes. `diagnostics()` reports each
typing tip's position in keyboard space and whether it is over the keys, and bounds the
widest wing sweep, so "typing" and "not clipped mid-beat" are measured rather than assumed.

Wing rates were levelled: an active bar used to run at 34 rad/s against an idle 6.4, which
made one fly look manic next to a still one. Both now buzz continuously (19 against 8) with
smaller excursions, and the typing rate follows the same rule.

The wall board was removed. It competed for the same vertical budget as the terminal, which
is now 39% wider and 44% taller with a 768x448 screen texture, and its content duplicates
the HTML ticker and charts below.

Cost: three.js is split into its own 126 KB (gzip) chunk, fetched only after WebGL is
confirmed. The scene is 298 draw calls and ~19.6k triangles; it animates wings, typing,
posture, lamps, steam and pointer parallax, and stops entirely under `prefers-reduced-motion`.

Framing was chosen from measured NDC footprints, since a headless check cannot look at the
picture: with `spread=3.5, distance=8.6, height=3.4, flyScale=0.82` the flies are 0.53 NDC
wide at |x| ≈ 0.55, their screens sit above them at |x| ≈ 0.67, lamps and mugs stay inside
|x| < 1, and the desk fronts bleed off the bottom edge on purpose. The stage renders at mean
luminance 53 with 32k distinct colours — lit, contrasted, and not crushed to black.

**The occlusion bug, and the lesson.** The first version passed every check I had and was
still wrong on screen: the stations were positioned at `y = -1.02` while the floor plane sat
at `y = 0`, so an opaque grid plane covered both desks and both flies. Projecting an object
into the frustum proves nothing about whether it can be seen — a mesh under a floor projects
perfectly. `diagnostics()` now casts a ray through each fly's and each screen's centre and
reports which *named* object is hit first, so visibility is measured rather than assumed. If
you change the scene, check `visible.fly.hit === 'fly'` in the harness, not just the NDC
bounds.

Layout defects found by an automated DOM overlap scan (pairs of text elements whose
rectangles intersect), not by eye — 35 overlapping pairs, now zero:

- `.dx-rule` was a flex row, so a long label pushed its value out of the cell, out of the
  panel, and on top of the neighbouring panel's text. It is a grid with `minmax(0, 1fr)` now,
  and the label ellipsises instead of overflowing.
- `.topbar-report` and `.topbar-note` were both assigned `grid-area: note`, so the run
  summary sat directly on top of the run note. They have separate rows.
- `.source-detail` could not ellipsise without a shrinkable parent.

Four defects surfaced while verifying the 3D scene, all fixed:

0. The flies and desks were buried under the floor plane (above), and the wall board's top
   edge was clipped by the letterbox stage — the panel is now 640px tall and the board is
   shallow enough to fit entirely.

1. The diagnostics publish compared `performance.now()` against a zero baseline, so the
   first (and for a paused recording, only) update never published.
2. A paused or off-screen floor never redrew when new state arrived, so scrolling back
   showed stale terminal contents. `update()` now always draws a frame.
3. Vite silently dropped the harness page's script tag when the `rollupOptions.input` key
   matched the emitted chunk name. Renaming the key fixed it.

### Thermal reality on the laptop

Measured, and worth planning around: bars start at 5.5-7 s on a cool M2 Air, degrade to 13-16 s
after an hour of continuous load, and reached 25-35 s (once 82 s) after two hours with a browser
rendering the 3D floor alongside. The engine is memory-bandwidth-bound, so concurrent runs slow
each other well beyond the spare-core count. A twelve-run campaign that should take an hour took
three. Anything longer than a two-season campaign belongs on the desktop; on the laptop, prefer
many short seasons.

### Verified end to end on real data

- A training run on the newest real BTC-USDC season completed with 256-cell population vectors
  on every bar and both brains checkpointed (7.1 MB each: the weight array is mostly repeated
  contact magnitudes, so it compresses hard). `changed_before_reset: 3386` is what the trained
  checkpoint carried, which is the number the reset control turns on.
- An exam run applied those weights and recorded `starting_weights` with the checkpoint's
  sha256 and `differing_fields: ["starting_weights"]` — the fairness block switches away from
  `learning` exactly as designed, with the control fly on the reconstructed baseline.
- A readout was fitted from the fly's own activity. Its own metrics are the honest headline:
  **training accuracy 1.0 on 34 bars with 256 features, holdout 0.583 against a base rate of
  0.583 on 12 bars** — a memorised fit, and `fitreadout` now says so in warnings. That is a
  demonstration that the pipeline works, not a model to trust; a real fit needs many more
  seasons and belongs on the desktop.

### Exam, reset and the fitted readout

Three protocols, all in `docs/experiments.md`:

- **exam** (`--kind exam`) freezes both flies and gives one the weights it learned in an earlier
  season, so the only variable is the brain it carried in. `run.kind` records this and the
  fairness block switches from `differing_fields: ["learning"]` to `["starting_weights"]`.
- **reset** (`--kind reset`) does the same and then wipes the learned KC→MBON efficacies back to
  `baseline_plastic`. This predicts something checkable rather than something vague: a reset
  brain must be element-wise identical to a never-trained one, and `tests/test_starting.py`
  asserts it.
- **a readout fitted from the fly** (`flyvsly fitreadout`) — 256 cells of the fly's own spike
  counts, logistic regression with L2, numpy only, temporal split, fitted on the control arm,
  shared by both flies at run time and never fitted online. `ReadoutPanel` shows its accuracy
  beside its base rate and says "no better than guessing" when that is the truth.
- `scripts/experiment.sh` runs the whole sequence and writes `results/report.md`.

Population vectors joined the telemetry for this: every neural bar records the spike counts of
the selected cells, so a readout can be fitted from a run that has already happened.

### Activity levers and the reinforcement controls

The flies were quiet because of the gate, not the brain — see `docs/activity.md` for the
measurement (threshold exceeded on 90-98% of bars, gate open on 27-60%). `--preset active`
drops the gate requirement and raises the daily order cap to upstream's max of 100; measured
on one shared season, HOLD went from 50% of bars to 0% and executed trades doubled. The
per-order cap stays at $10 because upstream's `Settings` validates `order_limit <=
min(capital, 10)` and this project does not patch vendored code.

`ConfigurableDecoder` is checked field-for-field against upstream's decoder whenever the gate
is required, so no existing measurement is against a silently different readout.

`--reinforcement pnl|decoy|shuffled|none` separates "the rule used the fly's own outcome"
from "any dopamine pulse moves these synapses": `decoy` drives the pulse from the benchmark
instead, `shuffled` permutes a previous run's own schedule (by run id or label), `none` sends
nothing. `scripts/campaign.sh` runs all four modes over the same disjoint seasons and writes
`results/report.md`.

UI: fill markers on the equity chart (a triangle per trade, pointing the way the order went),
and a trade cam that eases the camera to whichever fly just filled.

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
