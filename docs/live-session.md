# The live session: seven hours against the real market

On 2026-09-11 the engine traded a real market on paper for seven hours, one decision per
completed minute. Both flies lost, the learning fly lost more, and fees were larger than the
net loss. The return is not the interesting part. The interesting part is that the engine held
a one-minute cadence for 429 consecutive bars and that the session's own record survived the
host being killed mid-flight.

Everything below is read out of `runs/20260911-180303-live/recording.json` (itself rebuilt from
the `live_state.json` and `observations.jsonl` that session had already written) — the field
behind each number is named in the last section.

## What ran, and how it ended

```sh
flyvsly live --engine neural --product BTC-USDC --bar-seconds 60
```

| | value | where it comes from |
| --- | --- | --- |
| engine, mode | neural, paper only, `kind: competition` | `run.engine`, `run.kind` |
| market | BTC-USDC, 60-second bars | `run.live.product`, `run.bar_seconds` |
| feed | `flyvsly live`'s default venue (Kraken public candles) | CLI default `--source kraken`; see note below |
| warm-up | 120 bars before the first tradable bar | `run.live.warmup_bars` |
| bars traded | 429 | `run.bars` |
| first bar opens | 2026-09-11T18:02:00Z | `run.live.session_opened` |
| last bar opens | 2026-09-12T01:10:00Z (closes 01:11:00Z) | `live_state.json` → `last_bar` |
| span | 25,740 s = 429 minutes, exactly 429 × 60 | `run.duration_seconds` |
| bar spacing | every one of the 428 gaps exactly 60 s — **no minute missed** | `season.bars[].t` |
| rules | capital $100, order_limit $10, loss_stop $20, paper_fee 0.6%, 24 orders/day, gate required, `pnl` reinforcement | `run.rules`, `run.reinforcement_mode` |
| ended | killed mid-flight, not stopped | `run.wall_mode`, `summary.salvaged.reason` |

**It was killed, not stopped.** Docker Desktop — the Windows path this repo documents for
running the Linux-only engine — went down under the session. The checkpoint never saw a clean
stop: its status still reads `running` (`summary.salvaged.checkpoint_status`), and the recording
marks itself `truncated: true`. The session's durability design is what made the result
readable at all: each arm writes its own SQLite ledger every bar, and each observation is
appended to `observations.jsonl` and check-pointed *before* the bar is announced. `flyvsly
salvage` rebuilt `recording.json` from those two files (`summary.salvaged.from`), recovering all
429 bars it had traded (`bars_traded_at_kill`). One thing was lost: the 120 warm-up bars were
never stored, so the season's provenance records that "one placeholder bar stands in for it"
(`season.provenance.note`).

A note on the feed. The recording was rebuilt from a checkpoint that predates the venue field,
so its `season.provenance.source` reads `"unknown"`. What ran is the CLI's default source,
Kraken public candles; the product, bar length and every price in the season are recorded.

## The number

Both arms traded the same 429 bars and received byte-identical neural input each bar
(`run.inputs_identical_every_bar`, and `same_neural_input_both_arms` on all 429 observations),
so the only difference between them is the memory rule. Memory ON rewrote 3,618 of the 7,835
eligible KC→MBON efficacies during the session; memory OFF rewrote 0 (`final_memory`). It made
no difference worth having.

| | Gordon (memory ON) | Warren (memory OFF) | buy & hold | cash |
| --- | --- | --- | --- | --- |
| fills | 38 (25 BUY, 13 SELL) | 25 (18 BUY, 7 SELL) | 1 | 0 |
| fees paid | $2.1452 | $1.4270 | $0.5964 | $0 |
| final equity | $98.0306 | $98.8797 | $99.3720 | $100.0000 |
| return | **−1.969%** | **−1.120%** | −0.628% | 0.000% |

The memory-on fly lost **0.849 percentage points more** than the frozen one
(`summary.comparison.return_delta_pct` = −0.849104, `leader` = warren). That is the whole
directional result: on this path, learning did not help.

**Fees are the dominant term, and here is the arithmetic.** Gordon turned over $357.53 of
notional in 38 fills (`trades[].quote_size`) on a $100 account — more than three times the
account, round-tripped. At the upstream 0.6% fee that is 357.53 × 0.006 = **$2.1452**, i.e.
**2.15% of the $100 account burnt in fees alone**. Warren's $237.83 of notional paid $1.4270,
**1.43%** of the account. Add those fees back and the mark-to-bid equity change turns positive
for both — Gordon +$0.1758, Warren +$0.3067 — which is the arithmetic saying the fee bill, not
the direction calls, is what sank the accounts. Neither figure is a profit, and both accounts
still ended below $100. The fees are larger than the net losses they caused: Gordon's loss is
$1.9694 against $2.1452 of fees, Warren's $1.1203 against $1.4270. Buy & hold paid its one
$0.5964 entry fee and lost $0.6280, finishing ahead of both flies without making a single
decision.

Both flies also sat in the market almost without pause: a position on 428 of 429 bars
(`exposure_bars` 428, `deployment.holding_fraction` 0.9977), with 184 and 81 vetoes and no
blocked bars on either arm. Churn on a flat, drifting-down path is close to pure cost.

## What this can and cannot support

One session, 429 minutes, one market path, no repeat, no exam. It is a shape, not evidence —
the same treatment the README gives the three recorded seasons. In the recording's own words,
"one session cannot distinguish a consistent result from one lucky path"
(`summary.comparison.single_season_note`).

What it does support:

- The memory rule changed the brain and the behaviour at live cadence: 3,618 efficacies moved
  and the two flies made different decisions on the same bars.
- The live loop is durable: a process kill did not cost the session its record, and did not
  cost a single bar.
- Continuous 1-minute operation on this hardware is achievable with margin (below).

What it does not support:

- **Any claim of profitability.** Both flies lost, and so did buy & hold.
- **Any general result about the memory rule.** One path, one direction of outcome, and the
  difference is smaller than anything three offline seasons could confirm. Nothing here says
  the rule helps or hurts in general.
- **Any comparison to the offline seasons.** Different market, different length, different
  window; no number should be carried across.

## Can it run continuously?

Yes, on this host, with room to spare — and the session is the measurement.

| | per bar | where it comes from |
| --- | --- | --- |
| Gordon compute | mean 6.3872 s, median 6.2857 s, p99 8.075 s, worst 28.602 s | `observations[].arms.gordon.compute_seconds` |
| Warren compute | mean 7.0534 s, median 6.9172 s, p99 8.9044 s, worst 29.9088 s | `observations[].arms.warren.compute_seconds` |
| effective bar cost | 7.05 s mean — Warren was the slower arm on all 429 bars | max of the two arms, per bar |
| budget | 60 s per bar | `run.bar_seconds` |
| utilisation | 11.8% of the 25,740 s session in compute | sum of the slower-arm times |

The engine decides both arms on separate threads, so a bar costs the slower arm — here always
Warren, at 7.05 s mean. 428 of the 429 bars came in under 10 s; exactly one bar took 29.9 s,
still half the budget. Total compute across the session was 3,026 s against 25,740 s of wall
clock, so the loop spent ~88% of its life waiting for the exchange to close the next bar, never
racing it. Polling confirms the loop had slack: 1,914 polls (`run.live.polls`) at the feed's
15-second poll ceiling, well over one per bar. At the end the decision was 21.1 s behind the
exchange's clock (`run.live.lag_seconds_at_end`) — 35% of a bar, having absorbed the poll wait
plus the 7 s of compute.

The honest limits of that finding: it is one host (a 12-core Ryzen 9 3900XT, engine in Docker
Desktop on Windows 11) and one session, and that single 29.9-second bar is a reminder that a
backlog of slow bars is traded in market order rather than dropped. It answers "can this run for
hours at a one-minute cadence?" — yes, here, with the typical bar using about an eighth of its
minute — not "how many arms or what window length will fit".

## Continuing a session instead of writing it off

A kill used to end the experiment. `flyvsly salvage` turned the bars into a recording and the
next `flyvsly live` started a fresh session under a new run id, so every crash silently began a
new experiment. `flyvsly live --resume <run id>` continues the session instead, from what the
killed process left on disk — and it is honest about where each part of a session lives,
because they do not survive a kill equally:

| what | survives a kill? | where it is |
| --- | --- | --- |
| the market window | rebuildable | `live_state.json` (`session_opened`, `bar_seconds`, `warmup_bars`) plus the bars in `observations.jsonl` |
| the accounts | yes, per bar | each arm's own `gordon.sqlite` / `warren.sqlite` |
| the brain | only as far as it was checkpointed | `brains/<arm>.npz`, written every `--brain-every` bars (default 60) |

**The market window.** The bars the session traded are read back from `observations.jsonl`
(`flyvsly/live.py:336`), not fetched again: those mids are the ones the flies decided on, and
substituting the venue's close for an observed mid would make a resumed decision incomparable
with the one recorded for the same bar. In front of them goes the newest `warmup_bars` bars the
venue closed *before* the session's first traded bar; past the end of the venue's page one
placeholder bar stands in and the season's provenance says so. The window ends at the last
logged bar and the bar count starts there too (`flyvsly/arena.py:911`), so a bar already in the
log cannot be offered again — `LiveSeason.advance` also refuses any timestamp the season holds
(`flyvsly/live.py:192-207`).

**The accounts.** This is the part that survives a kill outright, and it is worth being exact
about what that means. `Ledger` opens its SQLite file with `CREATE TABLE IF NOT EXISTS` and
writes the starting cash, positions and anchor **only when the `settings` key is absent**
(`vendor/stonkfly/ledger.py:14-42`); re-opening a ledger whose settings signature does not
match is refused rather than adapted (`ledger.py:39-42`). Each bar's money movement is one
`BEGIN IMMEDIATE` transaction — `settle` writes cash, positions and the order's settlement
together (`ledger.py:120-166`), and `commit_tick` writes the anchor, checkpoint and tick
together (`ledger.py:168`). So the claim is: **the account on disk is the account as of the
last bar that committed, and reopening it does not reset it** — the tick counter is read across
a kill and a continuation in `tests/test_live_resume.py`.

What that does *not* guarantee is that a bar was completed. A kill inside the order path can
leave an intent reserved and unfilled: `reserve` refuses to start a second order while one is
pending (`ledger.py:81-88`), the guard blocks the bar while it is unresolved
(`vendor/stonkfly/risk.py:24`), and the paper broker's own answer is `reconcile`, which settles
it from the immutable plan (`broker.py:35-38`). Nothing in the live loop calls it, so a resume
does not quietly do so either: if any arm's ledger holds an intent for a bar the log never
recorded, `load_resume` refuses the session (`flyvsly/salvage.py:435`) rather than trading that
bar twice or accounting for its fill twice. The check reads the arms' own SQLite files
read-only (`salvage.py:378-408`).

**The brain.** The learned efficacies live in memory and nowhere else, so a session that
checkpoints nothing has nothing to continue from. A live session therefore writes each fly's
brain into `<run>/brains/<arm>.npz` every `--brain-every` bars, replacing the previous snapshot
(the latest state is what a continuation wants, not a history of it), and it writes the
snapshot *before* the checkpoint that names it (`flyvsly/arena.py:1152-1171`, called at
`arena.py:964`). A continuation restores the newest snapshot its checkpoint names and records
which bar that was, how many bars behind the kill it is, and the file's digest — the same
`weights` provenance block a trained brain is recorded with. When there is no snapshot it
records that the fly restarted from the baseline graph and why (`arena.py:1045-1113`).

Two things about that restore are deliberate. It uses upstream's `restore`
(`vendor/stonkfly/neural/brain.py:393`), which verifies the checkpoint's provenance against the
brain it is loading into (model, build, rule digest, graph digests), and **not** `starting.apply`
— that function freezes the weights it puts back (`flyvsly/starting.py:179`), which is right for
an exam and wrong here: it would silently turn the fly whose learning the run is measuring into
a frozen one. The resumed arm's own `weights_frozen` flag is checked against `learning` for the
same reason (`arena.py:1093-1100`).

So: **a resumed brain is not the brain that was killed.** It is the newest snapshot the session
wrote, up to one `--brain-every` cadence old, and the recording states that rather than implying
otherwise. What no resume can recover is the decision the flies were making when the process
died, and any learning after the last snapshot; the recording says so in `run.resumed` (once per
continuation, with the earlier ones nested under `earlier`).

**What is not verified here.** The brain half of this cannot be exercised without the retained
graph: `data/` is gitignored, so no test in this repository runs the real engine across a kill.
`tests/test_live_resume.py` drives the restore with a stub backend that saves and restores a
file, which pins the wiring — which fly is handed which snapshot, that a resumed competition is
not frozen, and what the recording then says — but not that upstream's `restore` accepts a real
mid-run snapshot of a live session. That check belongs on the host that has the graph: continue
a real session with `--resume` and read `run.resumed.brains` in the recording it writes.

```sh
flyvsly live --resume 20260911-180303-live        # continue, with its own rules and venue
flyvsly live --brain-every 60                     # a new session that can be continued
```

A continuation takes its rules, engine, product, venue, warm-up and run id from the checkpoint
it is continuing (`flyvsly/server.py:278-321`): a request to continue an experiment may not
change it. Two requests are refused before anything starts, with the reason returned to the API
caller and put in the hub's state (`server.py:200-262`): a run id that does not exist, and one
that already has a `recording.json`. The second is the important one — a recording is evidence
of an experiment that is over, and the session documented above is exactly that case: it was
salvaged, so `--resume` refuses it. Continuing it would rewrite the record of a finished run.

## Where every number comes from

All paths are inside `runs/20260911-180303-live/recording.json` unless the file is named.

| number | field |
| --- | --- |
| 429 bars, 60 s bars, 120 warm-up, BTC-USDC | `run.bars`, `run.bar_seconds`, `run.live.warmup_bars`, `run.live.product` |
| 2026-09-11T18:02:00Z, 2026-09-12T01:10:00Z | `run.live.session_opened`, `live_state.json:last_bar` |
| 25,740 s | `run.duration_seconds` |
| no gap; all 428 spacings = 60 s | derived from `season.bars[].t` |
| killed, salvaged, truncated | `run.wall_mode`, `summary.salvaged.reason`, `run.truncated` |
| rebuilt from checkpoint + observations, 429 bars at kill, status `running` | `summary.salvaged.from`, `.bars_traded_at_kill`, `.checkpoint_status` |
| placeholder for the warm-up | `season.provenance.note` |
| venue `unknown` in the rebuilt record | `season.provenance.source` |
| identical inputs both arms | `run.inputs_identical_every_bar`, `observations[].same_neural_input_both_arms` |
| rules (100 / 10 / 20 / 0.6% / 24 / gate / pnl) | `run.rules`, `run.decoder_gate_required`, `run.reinforcement_mode` |
| 3,618 of 7,835 efficacies changed (ON), 0 (OFF) | `summary.arms.<id>.final_memory` |
| 38 / 25 fills, sides, $357.53 / $237.83 notional | `summary.arms.<id>.fills`, `.trades[].side`, `.trades[].quote_size` |
| $2.1452 / $1.4270 fees | `summary.arms.<id>.fees_paid` |
| $98.0306 / $98.8797 final equity, −1.969% / −1.120% | `summary.arms.<id>.final_equity`, `.return_pct` |
| +$0.1758 / +$0.3067 before fees | derived: `final_equity` − 100 + `fees_paid` |
| 428/429 held, 0.9977 holding fraction | `summary.arms.<id>.exposure_bars`, `.deployment.bars_holding`, `.holding_fraction` |
| 184 / 81 vetoes, 0 blocked bars | `summary.arms.<id>.vetoes`, `.blocked_bars` |
| buy & hold: 1 fill, $0.5964 fee, $99.3720 final, −0.628% | `summary.benchmarks.buy_and_hold.fills`, `.entry_fee`, `.curve[-1]` |
| cash: $100.0000 | `summary.benchmarks.cash.curve[-1]` |
| −0.849104 pp, leader warren, one-path caveat | `summary.comparison.return_delta_pct`, `.leader`, `.single_season_note` |
| 6.3872 s / 7.0534 s mean, 28.602 s / 29.9088 s worst compute | `observations[].arms.<id>.compute_seconds` |
| 6.3872 s (Gordon's mean) | `run.seconds_per_bar_mean` |
| 1,914 polls, 21.105 s lag at end | `run.live.polls`, `run.live.lag_seconds_at_end` |
