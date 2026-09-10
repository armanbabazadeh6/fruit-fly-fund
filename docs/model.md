# What is actually modeled

Fly vs. Fly adds a comparison on top of Stonkfly's simulation. It does not add physiology.
The authority on the model is upstream's own account, preserved unmodified at
[`vendor/docs/model.md`](vendor/docs/model.md); this page states what the two-arm
experiment does with it and where our interface stops.

## The simulation, in one paragraph

MaleCNS v1.0 supplies anatomy, not a living fly: 166,700 retained neurons and 25,582,938
directed connections, integrated at 0.1 ms by a C++ leaky integrate-and-fire kernel with a
20 ms membrane time constant, 5 ms synaptic decay, −45 mV threshold, 1.8 ms transmission
delay and 2.2 ms refractory period. A 320×180 RGB chart of recent prices stimulates 3,335
mapped R1–R6 cells and 811 mapped R8 cells. Mean right-minus-left DNp20 firing, gated by
any DNpe017 spike, is decoded into buy, sell or hold at ±2 Hz. Positive marked-to-bid
portfolio changes deliver a 200 ms pulse into 15 PAM11 cells; negative changes pulse the
two PPL101 cells. The candidate memory rule adapts 7,835 existing KC→MBON07/11 efficacies
from actual spike counts and those pulses.

Every one of those choices is upstream's, is declared in upstream's model document, and is
either an explicit modeling assumption or a literature-motivated hypothesis. None of them
is a measurement of a trading strategy.

## The experimental variable

The two flies are identical in every configured field except one:

| | Gordon Flykko | Warren Buzzett |
| --- | --- | --- |
| `learning` | `True` | `False` |
| Connectome, sensory mapping, dynamics | identical | identical |
| Reward/aversive pulses | identical | identical |
| Decoder, thresholds, cooldown, limits, fees | identical | identical |
| Market bars and RGB input | identical (verified by hash per bar) | identical |

With `learning=False`, upstream's `MemoryBrain.step` still computes the traces but the
weight-write path is frozen, so the 7,835 eligible efficacies remain at the reconstructed
baseline for the whole season. The gated test `tests/test_neural_arena.py` asserts exactly
that against the real graph.

This is upstream's own control switch, not a mechanism we invented. What we add is the
discipline of running both arms on one cached market series with one ledger each, and
recording enough telemetry to see what each arm actually did.

## The engineered interfaces we did not touch

- **Vision.** A rendered chart, sampled at inferred receptor coordinates, is not a
  calibrated compound eye. Upstream's own probe noted that a dark chart barely activated
  Kenyon cells and that display sensitivity is a major confound. We keep upstream's light
  background and change nothing about the mapping.
- **Reinforcement.** Profit and loss are turned into a binary pulse above a 0.01 USDC
  deadband. It is feedback about portfolio value, not evidence that the last action caused
  the change: holding through a price move produces either signal. Fees count as a loss.
- **Decoding.** A fixed threshold on two cell populations is an interface. Persistent
  turning-like bias in the network therefore becomes persistent buying, which is a known
  and visible failure mode in the recordings rather than a hidden one.
- **Time.** Each observation advances 500 ms of neural time regardless of how much market
  time the bar represents. Market-to-neural time is deliberately compressed and is not fly
  physiology.

## What would count as learning

Reproducing upstream's list, because our repetition design only addresses the first item:

1. Held-out chronological market replay, not tuning on the same seasons.
2. Frozen-weight and shuffled-reinforcement controls. We have the frozen-weight control;
   the shuffled-reinforcement control is a run we have not built.
3. Equal budgets, identical fees — done.
4. Retention: does an advantage persist on later, unseen seasons?
5. Loss of benefit after resetting the learned efficacies.
6. Beating cash and simple exposure baselines. Buy & hold is recorded in every run for
   exactly this reason.
7. Not selecting a lucky run. `flyvsly report` reports the paired spread across seasons;
   a mean difference inside that spread means nothing.

Until 1–5 exist, "the memory rule changed weights" is a mechanism check, and any
performance difference between the flies is a description of one path through one market.

## Failure modes the recordings already show

- Both flies can spend entire seasons unable to trade profitably because they cannot short:
  a SELL proposal with no inventory is vetoed, and the fly has no other action.
- The $10 per-order cap and 60-second cooldown mean exposure builds slowly; a fly can end a
  season having been in the market a small fraction of the time. Every recording reports
  that fraction rather than only the return.
- Fees are charged on every fill. A churning fly pays for its activity, and the trade
  history makes the cost visible.
- The two arms diverge only once weights differ, which can take several bars; before that
  they are the same run with two ledgers.
