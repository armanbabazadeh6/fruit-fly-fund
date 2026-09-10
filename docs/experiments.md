# The experiment: exam, reset, and a readout fitted from the fly

Three questions, three protocols. Each answers something the ordinary competition cannot, and
each is cheap enough to run on a laptop.

## 1. Exam instead of homework

**The problem.** In a normal run a fly learns *and* is graded on the same market path. Whatever
difference appears could be a real effect or could be an artifact of that one path — the answer
key was in the room the whole time.

**The protocol.**

```sh
# learn on two real seasons
flyvsly run --preset scalper --bars 48 --repeats 2 --label train \
  --save-brains runs/brains/train

# then freeze both brains and run on a season neither has seen
flyvsly run --preset scalper --bars 48 --kind exam --window-offset 96 \
  --starting gordon=trained:runs/brains/train/gordon.npz \
  --starting warren=baseline --label exam-fixed
```

During the exam **neither fly learns**: both run with `learning=False`, so no weight moves.
The only difference between them is the brain each one carried in — one trained on earlier
seasons, one at the reconstructed baseline. `run.kind` records this, and the fairness block
says `differing_fields: ["starting_weights"]` instead of `["learning"]`.

**What a result means.** If the trained fly does not beat the fresh one on unseen seasons, the
memory did not generalise, whatever it did during training. If it does, that is the first
evidence in this project that the rule is doing something useful rather than merely something.
The exam season is kept away from the training seasons by `--window-offset`, and a run refuses
to start if either fly has no starting point worth calling one.

## 2. Reset: does the difference live in the weights?

**The problem.** Suppose the trained fly does better in an exam. Before crediting the memory
rule, check that the difference is actually *in* the efficacies the rule touched.

```sh
flyvsly run --preset scalper --bars 48 --kind reset --window-offset 96 \
  --starting gordon=reset:runs/brains/train/gordon.npz \
  --starting warren=baseline --label exam-reset
```

`reset` restores the trained checkpoint and then puts the learned KC→MBON efficacies back to
`baseline_plastic`, leaving everything else as it was.

**This makes a sharp prediction, not a vague one.** A reset brain must be *element-wise
identical* to a never-trained brain: the plastic efficacies are the only thing the rule writes,
so wiping them must reproduce the baseline exactly. `tests/test_starting.py` asserts that, and
the recording reports `changed_before_reset` — how many efficacies differed before the wipe —
as the evidence that the checkpoint really carried learning.

If a reset fly behaved differently from a fresh one, something *other* than those weights would
be carrying memory, and that would be a significant finding in itself.

## 3. A readout fitted from the fly's own brain

**The problem.** The trading rule is drawn by hand: mean right DNp20 firing minus mean left,
against ±2 Hz, gated on a DNpe017 spike. A human chose that. If it is the wrong readout, the
memory rule could be working perfectly and still show nothing.

**The protocol.**

```sh
# every neural bar already records a population vector of 256 cells; fit a model to it
flyvsly fitreadout --runs runs --label train --arm warren --out models/readout.json --horizon 1

# then decide from the model instead of the threshold
flyvsly run --preset scalper --bars 48 --kind exam --window-offset 96 \
  --readout models/readout.json --starting gordon=trained:... --starting warren=baseline
```

- **Features** come from the fly, not from prices: the spike counts of every annotated
  descending neuron, every MBON, every DAN, the decoder cells, and a deterministic sample of
  Kenyon cells, capped at 256 (`flyvsly/population.py`). Nothing about the market enters the
  vector.
- **Target** is the sign of the price change `horizon` bars ahead (`--horizon`, default 1).
- **Fitted** by logistic regression with L2, numpy only, on a **temporal split**: the earliest
  75% of bars train, the latest 25% are held out. A random split would let the model train on
  the future and be graded on the past.
- **Trained on the control arm** by default, because fitting on the arm the memory rule is
  changing would entangle the readout with the thing under test.
- **Never fitted online.** The model is loaded once, frozen, and the same file decides for both
  flies; each fly supplies its own activity. `run.readout` records the model's own metrics with
  the run.
- **Cross-season targets are dropped.** Each season's tail is trimmed before pooling, so no
  sample pairs the last bar of one season with the first close of another.

**How to read it.** The panel and the recording always show accuracy *next to the base rate* —
the rate you would get by always guessing the majority direction. A model at or below its base
rate is worse than guessing, and is presented that way. **A readout that cannot beat chance is
a result**, not a failure of the harness: it says the fly's spikes, binned at one minute and
read this way, do not predict the next minute's price direction.

## What none of these establish

- That any fly is profitable. Every configuration so far loses to buy & hold on the same bars,
  and fees dominate.
- That the ReadoutPanel's most-weighted cells *cause* anything. A large weight means the model
  leans on that cell.
- That the memory rule is how a real fly learns. The pulses are engineered, the decoder is an
  interface, and the algorithm is a candidate rule applied to a reconstruction.

## Running the whole thing

```sh
scripts/experiment.sh [bars] [train_seasons] [preset]
```

It trains, fits the readout, and runs the exam, the reset control and the readout exam, then
writes `results/report.md`.
