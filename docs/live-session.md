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

## Reading more than one session

One session is a shape. The point of running live for days is to accumulate several, and
`flyvsly live-report` is the reading for that:

```sh
flyvsly live-report                      # one table per comparable group, human-readable
flyvsly live-report --json               # the same aggregate as JSON
flyvsly live-report --table --write results/live-report.md   # markdown, for committing
```

It reads every run under `--runs` whose manifest carries a `run.live` block — the marker
`flyvsly live` writes and `flyvsly salvage` preserves — and puts one row per session: bars
traded, the market span those bars cover, how the session ended, both arms' returns, both
arms' fill counts and fees, and the same buy & hold benchmark the single session reports.
Then one pooled line per group: the mean paired difference (memory on − memory off), how many
sessions each arm won, and the fee bill per arm and in total. Every number is read from the
session's own `summary`; the paired difference is the difference of the two arms'
`return_pct`, never re-derived from an equity curve, because a salvaged curve was rebuilt from
an observation log and can disagree with the accounts.

**It refuses to pool things that cannot be compared.** Sessions are averaged together only
when they ran the same product, bar length, engine, run kind and rule set (`run.rules`).
Anything else is reported as a separate group, and the report names the field that separated
them — `2 session(s) fall into 2 groups and are not pooled across them: bar lengths (60s,
300s)`. For the same reason a session with no recorded product, bar length or rule set, or one
that traded no bars, is listed as excluded with its reason instead of being folded in.

**It refuses to call a kill a finish line.** A session stopped on purpose reads `stopped`; one
the host killed, that `flyvsly salvage` rebuilt, reads `salvaged`, and the reading note that
travels with the table says a salvaged session's last bar is not a finish line. A stop that
landed inside a backlog drain reads `stopped · short`: the session stopped on purpose but the
bars the exchange closed after its last decision were never traded.

With one live session on disk this is the whole output:

```text
live sessions: 1 of 2 run(s) read, 1 comparable group(s)

BTC-USDC 60s · neural · competition · rules capital 100, order_limit 10, daily_orders 24, paper_fee 0.006, interval_seconds 60, require_gate True, reinforcement pnl — 1 session

  session               bars  span      ended   gordon   warren  buy & hold    delta  fills on/off  fees on/off
  --------------------  ----  -----  --------  -------  -------  ----------  -------  ------------  -----------
  20260911-180303-live  429   7h09m  salvaged  -1.969%  -1.120%     -0.628%  -0.849%  38/25         2.145/1.427

  pooled: mean paired delta -0.849% ± 0.000%  ·  wins on/off/tie 0/1/0  ·  fees 2.145 on + 1.427 off = 3.572 total
  one session: a single market path, not evidence.

Memory-on wins N of M sessions is a count, not evidence. A handful of live sessions is a shape,
not a result: each covers a few hours on a market path no other session shares, the pooled mean
gives every session one vote regardless of how long it ran, and every session ends where the
operator stopped it or the host died — a salvaged session's last bar is not a finish line. No run
here demonstrates profitable learning.
```

(The reading note is one paragraph; it is wrapped here to fit the page. `2 run(s) read` is the
one recorded season beside the live session.)

The pooled mean gives every session one vote, not one vote per bar: a session left running for
a week would otherwise outweigh a short one purely because it ran longer, and how long a
session happened to run is not part of the experiment.

**What the aggregate cannot support at this sample size.** With one session it is a table of
one row and the pooled line is that row: the spread is 0 by construction, not because the
result is consistent, and the report says so in as many words. Even with several sessions it
would not become evidence of anything profitable. Live sessions run on different, disjoint
market windows; none has a natural finish line, so what is being averaged is a set of stopping
decisions as much as a strategy; and the sessions are not independent draws from any
distribution this report knows. What it *can* support is comparison between the two arms
within the accumulated set — the paired difference and its spread, the win count, and the fee
bill the rule generated per session — which is the same claim `flyvsly report` makes over
repeated seasons and no stronger.

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

## Running a session unattended

The seven hours above ended because the host died, and nothing put the session back. That watch
is now `scripts/live-supervisor.sh`, which runs `flyvsly live` under `flyvsly.supervise`:

```sh
scripts/live-supervisor.sh --engine neural --product BTC-USDC --poll 15
```

Every argument that is not one of the supervisor's own is passed to the session, so the flags
are the session's flags; `--` passes everything after it through as well. The supervisor's own
options all have a default, so the line above is a complete unattended run:

| option | default | what it does |
| --- | --- | --- |
| `--runs DIR` | `runs` | where sessions, the log and the stop file go |
| `--log FILE` | `<runs>/live-supervisor.jsonl` | the append-only record, one JSON object per decision |
| `--stop-file FILE` | `<runs>/live.stop` | `touch` it to finish: the session stops after the bar it is on and the supervisor does not restart; delete it before the next run |
| `--session-log FILE` | `<runs>/live-supervisor.session.log` | the session's own output, kept next to the record |
| `--max-failures N` | 5 | consecutive crashes before the supervisor gives up |
| `--backoff`, `--backoff-ceiling` | 5 s, 300 s | the retry wait, doubling from `--backoff` to the ceiling |
| `--healthy-seconds` | 600 | a run this long clears the crash ladder |

The policy is deliberately dull. Exit 0 or a stop file is an expected end: the supervisor stops
with it, exit 0, and restarts nothing. Anything else is a crash: the session is restarted after
5 s, 10 s, 20 s and so on up to 300 s, because the failures that happen are transient venue and
host ones — a slow minute, a refused prime, a container the host killed. Five crashes in a row,
none of them lasting `--healthy-seconds`, is a fault a retry will not fix, so the supervisor
gives up, says so in the log, and exits 2; a run that lasted ten minutes clears the ladder, so a
week of good trading followed by one bad night is not five failures. Ctrl-C is the operator,
not a crash: the console delivers it to the session as well, so the session stops after the bar
it is on, and an interrupt that reaches the supervisor rather than the session is recorded as
`interrupted` and exits 130.

What a restart is: a new process, and therefore a new session — `flyvsly live` mints a run id
from the clock, so the restart opens a fresh run directory with its own $100 paper accounts and
its own recording. Nothing from the session that died is lost (`flyvsly salvage` rebuilds its
recording from the checkpoint it had already written), but the restart's equity curve does not
continue the dead one's. Where each outcome is written down, one line each — the values below are
the shape of a record, not a session that ran:

```sh
tail -f runs/live-supervisor.jsonl
{"ts": "2026-09-12T01:14:03Z", "event": "start", "attempt": 1, "command": ["…", "live"]}
{"ts": "2026-09-12T01:14:07Z", "event": "exit", "attempt": 1, "exit_code": 1, "runtime_seconds": 3.8, "expected": false, "reason": "crash"}
{"ts": "2026-09-12T01:14:12Z", "event": "restart", "attempt": 2, "failures": 1, "backoff_seconds": 5.0, "last_exit_code": 1}
```

**What this has not been verified against.** No real session has run under the supervisor: it
needs the live venue and the 1.6 GB graph, so the policy above is pinned by tests rather than by
hours of market, and `pytest tests/test_supervise.py -q` is the check — a clean exit is not
retried, a crash is retried with a growing wait, the ceiling and the give-up are enforced, and
the script itself is driven end to end against a fake session that crashes once and then exits 0
(the fake is what `FLYVSLY_SESSION` is for; it is also how the Docker form is run). The one
thing the supervisor cannot do for you is notice that a *running* session has stopped trading
without dying — for that, watch `runs/live-supervisor.session.log` and the browser.
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

## Checking a replay against the session it replays

`flyvsly live` and a recorded season trade the same bars through the same arena, but nothing had
ever checked that a live session and a replay of *its own* bars decide the same thing. If those
two can disagree, every live-versus-recorded comparison this project makes rests on an
assumption nobody tested. `tests/test_live_parity.py` tests it against a session's own log —
the 429 bars above, with their mids and the decision each fly made on each. The answer depends
on the engine:

- **Procedural, fully offline.** The procedural backend is a pure function of the chart it has
  been shown, so a replay over the same window must reproduce every decision exactly. It does.
  `pytest tests/test_live_parity.py::test_a_live_session_replays_to_the_same_decisions` also
  hashes the frames each arm was handed, so agreement cannot come from two wrong paths
  cancelling out, and it needs no graph and no network.
- **Neural, gated.** The neural arm's synapses move with every bar's reinforcement, and its
  warm-up chart was never written down. A replay therefore starts from a different brain at
  the same bars, and exact decision parity is not a property the log can carry. The gated test
  asserts the half that *is* reproducible — the market inputs — and reports the decision
  agreement rather than forcing it green; it needs `FLYVSLY_NEURAL_TEST=1` and a prepared
  graph (`FLYVSLY_DATA=<data>`), and skips with that reason otherwise.

What no replay from a log can reproduce, for either engine:

| lost when the session was killed | consequence |
| --- | --- |
| the 120-bar warm-up chart | bars 0–98 of a rebuild are a different chart: their frames do not match the log, and the procedural rule holds at bar 0 where the session, which saw its warm-up, bought |
| the neural brain between checkpoints | a replay from baseline is a different experiment; only `--brain-every` snapshots narrow the gap, and they restore one bar's brain, not the per-bar history |
| the venue's own history | `CandleFeed.resume` can rebuild the warm-up only while the venue still serves those bars; Kraken keeps a page of candles, not a memory |

The line is exact and checked. `market_frame` draws `history[-100:]`, so once 100 logged bars
stand behind the bar being decided the rebuilt frame is byte-identical to the one recorded — bar
99 onward in the salvaged session, and bars 0–98 are exactly the ones the missing warm-up fed.
`test_the_salvaged_session_reproduces_its_frames_where_the_log_covers_the_window` asserts that
boundary against the real fixture. It skips on a fresh checkout, where `runs/` is absent, and
takes `FLYVSLY_LIVE_RUN=<run directory>` to point at a copy.

## More than one live session

One session is one path through one stretch of market, and `flyvsly live-report` has nothing to
pool until there is more than one. `scripts/live-campaign.sh M K` runs M live sessions of K
traded bars back to back under `scripts/live-supervisor.sh`, then writes the pooled report to
`results/live-campaign.md`:

    scripts/live-campaign.sh 5 180          # five sessions of three hours, unattended
    scripts/live-campaign.sh --dry-run 5 180
    scripts/live-campaign.sh --help

Each session ends when its own checkpoint (`<run>/live_state.json`) records K traded bars: the
campaign watches the newest checkpoint written since the session started, touches the shared
stop file, and lets the bar in flight finish so the session still writes a clean recording.
Nothing is cut off mid-decision. A session that crashes is restarted by the supervisor as usual;
the campaign follows the restarted session's checkpoint because it watches the newest one, not
a run id it cannot know in advance. The watchdog log and each session's own output are kept
under `runs/live-campaign-<stamp>/`.

It will not start a second session on top of a running one. A lock in `runs`
(`live-campaign.lock`) holds the campaign's pid and is refused while that pid is alive, and an
already-running `flyvsly live` is refused before the first session starts. Ctrl-C touches the
stop file, lets the current bar finish, cleans the lock up and exits 130. `--dry-run` prints the
plan and the exact supervisor command per session without starting one.

**A first campaign should be small.** At a 60 s bar, M sessions of K bars occupy about M×K
minutes of wall time, so the useful first run is M=5, K=180: five three-hour sessions, about
fifteen hours, fits an overnight run and gives `live-report` five rows — the smallest number
that is a distribution rather than a point. The observed session produced roughly nine fills an
hour for the learning arm, so 180 bars is enough for a comparison to have something in it,
while still being five consecutive hours of one product in one market. Consecutive sessions
share a regime, not just a market: the report describes that window and nothing wider, and five
paths through it do not become five markets.
