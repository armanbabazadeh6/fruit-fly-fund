# The fairness contract

The claim this project can defend is narrow: *apart from the memory rule, the two flies had
the same competition*. That is easy to assert in prose, so it is enforced in code and
recorded in every run.

## What is identical

`flyvsly/fairness.py` constructs both arms from one `ArenaRules` object and refuses to
continue unless the two upstream `Settings` objects differ in exactly one field:

```
differing_fields: ["learning"]
identical_fields: capital, products, order_limit, loss_stop, fee_reserve, slippage,
                  spread_limit, daily_orders, interval_seconds, max_quote_age, neural_ms,
                  neural_bin_ms, pulse_ms, pulse_current, reward_deadband,
                  decoder_threshold_hz, paper_fee
identical_fields_sha256: <hash of the frozen set>
```

`tests/test_fairness.py` covers this, including a negative case: changing anything else
raises rather than silently skewing the comparison.

Beyond configuration:

- **One season per run.** The bars are generated or fetched once, cached to
  `data/markets/`, and both arms read the same list. A Coinbase season is anchored to the
  exchange's newest completed candle, not to the local clock, so it replays identically on
  another machine.
- **One frame per bar.** The 320×180 RGB array is rendered once and the same object is
  handed to both arms. Each neural observation also reports `input_sha256`; every
  recording carries `inputs_identical_every_bar`, and the browser shows it. If the two arms
  ever received different bytes, the badge would say so.
- **Fresh charts, shared input.** Each arm's visual pathway has its own state, so the same
  frame can drive different dynamics — that is the experiment, not an unfairness.
- **Separate accounts.** Each fly has its own SQLite ledger, cash, positions and order
  history. Neither can see the other.

## Where replay mode differs from upstream's live worker

Documented rather than hidden, and identical for both arms:

| Upstream live worker | Fly vs. Fly replay | Why |
| --- | --- | --- |
| Waits in wall-clock time between observations | Advances market time explicitly, one completed bar per observation | A season is 48–480 bars; waiting 60 s of wall time per bar would make it unusable |
| `Guard` reads `time.time()` | `ReplayGuard` injects a virtual clock; sizing still calls upstream's `Guard.plan` | Cooldown, daily-order and quote-age rules must be measured in market time. `tests/test_execution_parity.py` pins the planned order to upstream's for the same inputs |
| Re-fetches the book after neural integration and vetoes a move beyond slippage | Uses the bar's quote, since a one-minute bar has no intra-bar book | Both arms face the same quote and the same slippage cap |
| A guard veto stops the whole worker | A veto blocks that bar only; the run continues | A season needs many observations, and the guard runs before the neural step either way, so no arm sees a bar the other did not |
| Checkpoints the brain every tick | One checkpoint per arm, at the end of the last season of a run, and only when `--save-brains` names a directory | There is no `--checkpoint-every`: a mid-run checkpoint is ~7 MB per fly per bar uncompressed and buys nothing for a replay, which is never resumed. `save_brains` also refuses to overwrite, so a campaign cannot silently replace the brains an earlier result came from |
| Can place real orders through Coinbase | Cannot: `stonkfly.actions` is never imported, and the Coinbase SDKs are not dependencies | Paper only, by construction rather than by flag |

One consequence worth stating: if an arm trips the 20 USDC loss stop, upstream's guard
halts that ledger permanently for the rest of the season. That is the rule, applied equally,
and the recording reports the halt in the scoreboard instead of quietly continuing.

## Benchmarks

`buy_and_hold` makes one unrestricted fill at the first bar's ask and then holds, paying
the same 0.6% fee. `cash` never trades. Buy & hold obeys **none** of the flies' execution
limits — no $10 order cap, no 60-second cooldown, no 24-orders-per-day limit — which is an
advantage we state in the UI rather than smoothing over. It is context, not a like-for-like
competitor, and the deployment figures (bars holding, time in market) are shown next to
every return so a barely-invested fly is not compared as if it were fully invested.

## Repetition

`--repeats N` runs N seasons of the same construction:

- Synthetic: a different seed per repeat, so the price paths are unrelated.
- Coinbase: the window steps back one season per repeat, and the step counts *bars*, not
  minutes. This distinction matters: the exchange omits minutes with no trades, so a 48-bar
  season spans about 64 minutes, and stepping back 48 minutes left each season sharing roughly
  a dozen bars with the next. Before v2 of the market cache that is what happened, so early
  campaigns in this repository overlap their neighbours. `--must-not-overlap <run id or label>`
  now makes the property checkable: a season that shares any bar with the named recording is
  refused rather than run.

`flyvsly report` groups them and prints the paired difference (memory-on minus memory-off)
per season, the mean, the spread, and the win counts. With a handful of seasons the spread
usually swamps the mean; the report says so rather than picking the best season.
