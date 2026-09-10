# Fly vs. Fly

[![CI](https://github.com/armanbabazadeh6/FruitFlyBrain/actions/workflows/ci.yml/badge.svg)](https://github.com/armanbabazadeh6/FruitFlyBrain/actions/workflows/ci.yml)
[![Pages](https://github.com/armanbabazadeh6/FruitFlyBrain/actions/workflows/pages.yml/badge.svg)](https://armanbabazadeh6.github.io/FruitFlyBrain/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Paper trading only](https://img.shields.io/badge/trading-paper%20only-ffb454.svg)](#what-is-and-is-not-claimed)

**[Watch it run](https://armanbabazadeh6.github.io/FruitFlyBrain/)** — the browser
experience replays recorded neural runs with no server and no dataset. The page opens on
the two flies at their terminals, trading.

Two fruit-fly neural simulations, one paper-trading account each, identical market and
identical rules. **Gordon Flykko** applies Stonkfly's experimental memory updates.
**Warren Buzzett** is the same simulation with those updates frozen. The question is
whether the memory rule makes a measurable difference — not whether it helps.

Built on [Stonkfly](https://github.com/nftechie/stonkfly) (MIT, vendored unmodified in
`vendor/`) and the [MaleCNS v1.0](https://male-cns.janelia.org/) male fly connectome:
166,700 neurons, 25,582,938 directed connections, integrated at 0.1 ms by upstream's C++
kernel. A fixed engineered decoder turns DNp20 firing into buy/sell/hold; the fly sees a
320×180 RGB chart, never a price list.

**Paper trading only.** No exchange account, no API key, no real order, no credential
handling anywhere in this project.

![Two flies trading a season](docs/floor.gif)

*Both flies working a recorded season. The terminals are driven by real telemetry: each desk
reacts to its own fills, and every number on a screen comes from the recording.*

## First result, and how to read it

Three disjoint seasons of real public BTC-USDC one-minute candles, 48 bars each, both flies
on the retained graph:

| | memory ON (Gordon) | memory OFF (Warren) | buy & hold |
| --- | --- | --- | --- |
| mean return over 3 seasons | −1.000% | −0.908% | −0.765% |
| paired difference (on − off) | mean −0.092%, spread ±0.095%, range −0.225% … −0.009% | | |
| seasons won | 0 | 3 | — |
| mean changed KC→MBON efficacies | 3,442 | 0 (frozen) | — |

The memory rule demonstrably did something: it rewrote ~3,400 of the 7,835 eligible
efficacies every season, and the two flies diverged in their decisions. It did not help.
The paired difference is smaller than its own spread across three seasons, both flies lost
to a plain buy-and-hold, and fees plus the inability to short dominate everything else.
That is the honest state of it, and three seasons is far too few to conclude anything
either way: reproduce it with `--repeats 6` and more bars before reading the mean.

![The trading floor](docs/screenshot-floor.png)

A **3D / 8-bit** toggle switches the scene between the rendered floor and a pixel-art pass
(289 distinct colours against 36,981, measured): the retro mode looks like the artwork that
inspired the project, with the live numbers kept in crisp text above the canvas because
pixelation makes the on-screen terminals decorative.

![8-bit mode](docs/screenshot-8bit.png)

## What is and is not claimed

- The neural state, spikes, synaptic efficacies and memory rule statistics are real
  simulation output from the retained connectome.
- The reward/aversive pulses are **engineered** feedback about portfolio value. This is not
  pain, pleasure, consciousness or a fly understanding money.
- The DNp20 decoder is a fixed engineered interface, not a discovered "buy neuron".
- Whether memory updates help is undetermined. Upstream states plainly that profitable
  learning has not been demonstrated, and nothing here changes that.
- Rivalry commentary is entertainment. Procedural demo mode is never neural activity.

`docs/` has the detail: [model](docs/model.md), [fairness](docs/fairness.md),
[telemetry](docs/telemetry.md), [hardware](docs/hardware.md).

## Run it

Python 3.11+, a C++17 compiler, macOS or Linux (WSL for Windows).

```sh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
flyvsly prepare              # ~1.1 GB download, 69 s and 1.33 GB peak RSS on an M2 Air
flyvsly doctor --measure     # what this machine can actually run
```

Run the competition, paper only:

```sh
# Real public Coinbase BTC-USDC candles, 48 one-minute bars, three disjoint seasons.
flyvsly run --engine neural --market coinbase --bars 48 --repeats 3 --label season-coinbase-48

# Offline and reproducible: synthetic seasons on a fixed seed.
flyvsly run --engine neural --bars 24 --seed 5

# Interface-only: the procedural stand-in, labelled everywhere as not neural.
flyvsly run --engine procedural --bars 480 --seed 21

flyvsly list                 # recordings, with each fly's return
flyvsly report               # paired deltas across repeated seasons
flyvsly publish              # copy recordings next to the web bundle for static hosting
```

Every run writes `runs/<id>/`: `recording.json` (everything the browser shows),
`observations.jsonl`, `manifest.json`, and one SQLite ledger per fly.

## Watch it

```sh
cd web && npm install && npm run build && cd ..
flyvsly serve                # http://127.0.0.1:7777
```

`flyvsly serve` serves the built page, lists recordings, streams a running arena over
Server-Sent Events, and can start runs from the UI. The badge in the header always says
which data is on screen: a live neural run, a recorded neural run, or the procedural demo.

The page shows a 3D trading floor — two modeled flies at their desks in front of amber
terminals, under a wall board showing the market and both equity curves — plus a live
scoreboard, equity and price charts with a buy-and-hold and cash benchmark, per-bar spike
and memory telemetry, full trade history, and a per-trade explanation of exactly which
measured signals and which programmed rule produced it.

Every number on a terminal screen or the wall board comes from the recording on screen:
there is no decorative data. The flies are procedural models built from primitives at load time
(`web/src/three/models.ts`): banded abdomen, bristles, halteres, faceted compound eyes,
translucent veined wings, and forelegs rigged to type on individual key caps — the key
under each arm lights as it strikes. No third-party art is downloaded or bundled.
three.js is loaded lazily and never downloaded by a browser without WebGL, which falls back
to the 2D desk illustration instead.

`/floor-check.html` renders the 3D scene by itself, with framing controls and a diagnostic
readout — useful for changing the camera without guessing.

## Cost, measured

On the M2 MacBook Air (8 GB): graph preparation peaks at 1.33 GB RSS; each brain costs
roughly 0.3 GB resident, so two flies fit in about 0.7 GB; one 500 ms neural observation
costs 2–8 s per fly, and the two flies run on separate threads. A 48-bar season therefore
takes roughly 5–10 minutes, dominated by simulation, not by the market or the browser.
See [docs/hardware.md](docs/hardware.md) for the desktop checklist.

## Layout

```
flyvsly/          the arena: two arms, shared market, benchmarks, telemetry, server, CLI
vendor/stonkfly/  upstream Stonkfly at a pinned commit, unmodified (MIT)
web/              Vite + React + TypeScript browser experience
tests/            fairness, execution parity, market seasons, telemetry, gated full-engine test
docs/             model, fairness, telemetry and hardware notes
runs/  data/      local only, git-ignored
```

## Tests

```sh
python -m pytest -q                                  # 33 fast tests
FLYVSLY_NEURAL_TEST=1 python -m pytest -q -m slow    # + the real 166,700-neuron check
```

The gated test proves the control arm's 7,835 eligible efficacies never move while the
experimental arm's do, that both flies received byte-identical retinal input on every bar,
and that the neural arm's output is unchanged when the arena passes it a different price
history.

## Attribution

`UPSTREAM.md` records the pinned Stonkfly commit, what we take from upstream, and what we
add. Upstream's own caveats and validation status are preserved in `vendor/docs/`.
