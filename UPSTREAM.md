# Upstream attribution

Fly vs. Fly is built directly on **Stonkfly**, which is itself built on **DOOMFLY**.

## Stonkfly

- Source: <https://github.com/nftechie/stonkfly>
- Pinned commit: `78ef3e05ab0fa086032098558d893667068944a0` (2026-09-09, "Initial commit: Stonkfly connectome trading experiment")
- License: MIT (`vendor/LICENSE.stonkfly`)
- Vendored into this repository at `vendor/stonkfly/`, unmodified.

The vendored copy is the neural engine, paper broker, ledger and risk guard this project
runs. Nothing in `vendor/` is our work, and we do not patch it: the two experimental arms
are selected by Stonkfly's own `Settings.learning` flag, which upstream already exposes as
its frozen-memory control.

What we take from upstream:

| Upstream component | How Fly vs. Fly uses it |
| --- | --- |
| `stonkfly.neural.*` (MaleCNS v1.0 import, LIF kernel, KC→MBON memory rule, R8 display adapter) | The simulated fly. Both competitors run this, one with `learning=False`. |
| `stonkfly.neural.controller.Decoder` | The fixed DNp20 readout that turns spikes into buy/sell/hold. |
| `stonkfly.display.market_frame` | The 320×180 RGB chart that is the fly's only view of the market. |
| `stonkfly.reinforcement.reinforcement` | The engineered reward/aversive pulse scheduler. |
| `stonkfly.config.Settings`, `stonkfly.ledger.Ledger`, `stonkfly.risk.Guard`, `stonkfly.broker.PaperBroker` | Account, order intent, execution limits, Decimal fills and fees. |

What Fly vs. Fly adds (all in `flyvsly/`, `web/`, `docs/`):

- A two-arm arena that feeds both flies an identical cached market series, identical fees,
  identical execution rules and identical starting balances, differing only in `learning`.
- Buy-and-hold and cash benchmarks over the same bars and fees.
- A telemetry recording format, a replay/live server, and the browser experience.
- A procedural demo backend for interface work, which is labelled as not-neural everywhere
  it appears and can never be recorded as a neural run.
- Repetition across multiple market seasons, so one lucky run is visible as one lucky run.

Upstream's own caveats apply unchanged and are repeated in `docs/model.md` and
`vendor/docs/model.md`: engineered reinforcement is not pain, the decoder is a fixed
engineered interface rather than discovered "buy neurons", and **profitable learning has
not been demonstrated** by upstream and is not demonstrated here.

## DOOMFLY

Stonkfly's connectome importer, inferred visual projection, spiking kernel and plasticity
implementation are adapted by its author from <https://github.com/nftechie/doomfly>
(MIT). That chain of attribution is preserved in `vendor/THIRD_PARTY.stonkfly.md` and is
not ours to alter.

## MaleCNS v1.0 dataset

- Portal: <https://male-cns.janelia.org/>
- Announcement: <https://www.research.google/blog/a-connectomics-milestone-mapping-the-complete-male-fruit-fly-brain/>
- Terms: Creative Commons Attribution 4.0 on the release. Downloaded separately by
  `flyvsly prepare`; never redistributed in this repository.

If you publish results from this project, cite the MaleCNS release and its paper, and cite
Stonkfly and DOOMFLY for the simulation stack.

## Deliberate omissions

`stonkfly.actions` and `stonkfly.broker.CoinbaseBroker` (Coinbase AgentKit / Advanced
Trade execution) are vendored for completeness but are **not installed as dependencies and
never imported** here. Fly vs. Fly places no real orders and needs no account credentials.
