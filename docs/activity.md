# Making the flies trade, and keeping that honest

"How do we get them to trade a lot?" has a measurable answer. It is not the brain.

## The measurement

Across the three recorded 48-bar seasons, per fly:

| signal | observed |
| --- | --- |
| bars where the DNp20 difference exceeded the ±2 Hz threshold | **90–98%** |
| bars where the DNpe017 gate was open | **27–60%** |
| resulting proposals | 9–25 BUY, 2–4 SELL, 20–35 HOLD |
| vetoes | mostly "insufficient position" — a SELL with nothing to sell |

The brain is almost always strongly directional. What suppresses trading is upstream's
**gate**: the DNp20 difference may only act on a bar where at least one DNpe017 spike also
occurs. That is an engineered clause of the readout, not a property of the circuit.

## The levers, in order of effect

### 1. `--preset active` — the gate, and the daily cap

```
flyvsly run --preset active --bars 48 --repeats 3
```

| | `upstream` | `active` |
| --- | --- | --- |
| DNpe017 gate | required | **not required** |
| orders per UTC day | 24 | 100 |
| per-order cap | $10 | $10 (unchangeable) |

Measured on one shared synthetic season, 8 bars, both flies:

| preset | Gordon proposals | Warren proposals | executed |
| --- | --- | --- | --- |
| `upstream` | 4 BUY, 4 HOLD | 4 BUY, 2 SELL, 2 HOLD | 4 / 6 |
| `active` | 6 BUY, 2 SELL, **0 HOLD** | 6 BUY, 2 SELL, **0 HOLD** | **8 / 8** |

With the gate off, HOLD disappears: every bar produces an order the guard can act on. That is
the single biggest lever, and it uses the fly's own directional firing rather than inventing a
signal.

The $10 per-order cap **cannot be raised**. Upstream's `Settings` validates
`order_limit <= min(capital, 10)` and `capital <= 100`, and this project does not patch
vendored code. More size per order is not available to us; more *orders* and more *bars* are.

### 2. `--preset scalper` — many small orders

The other end of what upstream permits: $2 per order instead of $10. On a 48-bar season the
wallet, not the gate, becomes the limit — with $10 orders on $100 of capital a fly is fully
invested after roughly nine fills and every later BUY is vetoed for funds. Smaller orders let
it keep trading for the whole season (up to the 100-orders-a-day cap).

### 3. More bars

Cost is linear and dominated by the simulation, not the market: roughly 6 s per bar for both
flies on an M2. 480 bars is about 50 minutes per season per reinforcement mode.

### 4. `--decoder-threshold-hz`

Barely matters on its own: the difference already exceeds ±2 Hz on 90–98% of bars. It matters
once the gate is off, because it is then the only thing filtering proposals.

## Trading more is not trading better

In the A/B above, the busier preset lost more on the same season: Gordon went from −0.287% to
−0.604%. Every fill pays the 0.6% fee, and on a flat market churn is pure cost. This is worth
stating plainly, because "make them trade a lot" and "make them trade well" are different
requests and only the first one is a configuration change.

## The reinforcement controls

The pulse that drives the memory rule is engineered, and *how* it is scheduled decides what a
result can mean. `--reinforcement` selects:

| mode | the pulse follows | what a difference then means |
| --- | --- | --- |
| `pnl` | the fly's own marked-to-bid P&L change | as upstream: outcome-contingent, but confounded with fee drag and holding |
| `decoy` | the buy-and-hold benchmark's change | statistics matched, contingency removed: if memory-on still differs, the difference is not credit assignment |
| `shuffled` | a seeded permutation of a previous run's schedule (`--shuffle-reference <id or label>`) | the same pulses at unrelated times |
| `none` | nothing | isolates the memory rule from feedback entirely: does it move weights with no pulse at all? |

`decoy` is the honest control for the sentence "the memory rule uses the fly's own outcome".
`shuffled` keeps the exact multiset of pulses and breaks only their timing. `none` answers a
different question — whether the rule moves at all without a modulator.

All four are recorded per run in `run.reinforcement_mode`, and `none` of them change the
brain, the sensory path or the execution rules.

## Protocol hygiene

- `require_gate` and `reinforcement` are **our** extensions. They are stripped before
  upstream's `Settings` is constructed, carried on `ArenaRules`, recorded in every run, and
  asserted identical for both flies by `tests/test_activity_controls.py`.
- `ConfigurableDecoder` with `require_gate=True` is checked field-for-field against upstream's
  `Decoder` on every combination of counts and gate spikes. If that test ever fails, every
  measurement in this project is against a different readout than the one upstream documents.
- The gate change belongs to the *interface*, not the brain: spike counts, synaptic weights
  and the memory rule are untouched by it.
