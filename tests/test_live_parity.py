"""A live session and a replay of its own bars must decide the same thing.

`flyvsly live` decides each bar as the exchange closes it; a recorded season decides the same
bars in one accelerated pass. If the two disagree, every comparison this project draws between
a live session and a recorded one is suspect — and nothing had ever checked it. A session's own
log is the fixture: the bars it traded with their mids, and the decision each fly made on each
of them. The replay rebuilds the market window from that log and runs the arena over it again.

Two levels, held to two different standards:

* the procedural engine is a pure function of the price history, so replayed over the same
  window it must reproduce its decisions exactly — that level needs no graph and no network;
* the neural engine's synapses move with every bar's reinforcement, so a replay starts from a
  *different brain* unless the session checkpointed one, and the warm-up chart that fed the
  first bars is not in the log either. The gated tests below assert what is actually
  reproducible and record the rest.

The line a log cannot cross is the same for both engines: a replay from the log alone
reproduces the fly's chart only once ``market_frame``'s 100-bar window is entirely inside the
log, and it never reproduces the warm-up that preceded the first logged bar. The salvaged
7-hour session is the real fixture; ``FLYVSLY_LIVE_RUN`` can point at a copy of it.
"""

import dataclasses
import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from flyvsly.arena import Arena
from flyvsly.config import ArenaConfig, ArenaRules, MarketSpec
from flyvsly.live import COMPLETION_GRACE_SECONDS, CandleFeed
from flyvsly.market import Season
from stonkfly.display import market_frame

BAR = 60
EPOCH = 1789137600
WARMUP = 25
# What ``stonkfly.display.market_frame`` draws: ``history[-100:]``. A replay reproduces a
# logged frame only once the log holds that many bars behind the bar being decided, so bar
# ``FRAME_BARS - 1`` is the first reproducible one.
FRAME_BARS = 100
SIDES = {"BUY", "SELL", "HOLD", "BLOCKED"}
ARMS = ("gordon", "warren")

# The real session: 429 bars with their mids, and the decision each fly made. Absent from a
# fresh checkout (runs/ is gitignored), so the fixture tests skip rather than invent one.
SALVAGED = Path("runs/20260911-180303-live")

ENABLED = os.environ.get("FLYVSLY_NEURAL_TEST") == "1"
DATA_ROOT = os.environ.get("FLYVSLY_DATA", "data")

requires_engine = pytest.mark.skipif(
    not ENABLED, reason="set FLYVSLY_NEURAL_TEST=1 to run the full connectome test"
)
requires_graph = pytest.mark.skipif(
    not os.path.exists(os.path.join(DATA_ROOT, "graph.npz")),
    reason="run `flyvsly prepare` first",
)


class Clock:
    def __init__(self, now: float):
        self.now = float(now)

    def __call__(self) -> float:
        return self.now


class Venue:
    """A candle endpoint that closes one deterministic bar per call.

    The closes are a fixed function of the bar index — a ramp that turns every 40 bars — so the
    procedural rule has something to buy, hold and sell over a short window, and asking for the
    same bars again returns exactly the same ones. That is all a replay needs of a venue.
    """

    def __init__(self, clock: Clock, first_opened: int, per_call: int = BAR, horizon: int = 400):
        self.clock = clock
        self.first_opened = int(first_opened)
        self.per_call = int(per_call)
        self.horizon = int(horizon)

    @staticmethod
    def close(step: int) -> float:
        phase = step % 40
        ramp = 0.5 * phase if phase < 20 else 0.5 * (40 - phase)
        return round(100.0 + ramp + 0.3 * math.sin(step), 2)

    def __call__(self):
        self.clock.now += self.per_call
        served = int((self.clock.now - COMPLETION_GRACE_SECONDS - self.first_opened) // BAR)
        rows = []
        for step in range(1, min(served, self.horizon) + 1):
            close = self.close(step)
            rows.append([int(self.first_opened + (step - 1) * BAR), close - 1, close + 1, close, close, 0.25])
        return list(reversed(rows))


def spec() -> MarketSpec:
    return MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR)


def arena_for(out: Path, engine: str) -> Arena:
    return Arena(
        ArenaConfig(rules=ArenaRules(), market=spec(), engine=engine, out=out, label="parity")
    )


def run_live_session(tmp_path: Path, run_id: str, bars: int, engine: str = "procedural"):
    """Trade a fake venue live, one decision per completed bar, and return its log directory."""
    clock = Clock(EPOCH)
    feed = CandleFeed(
        spec(),
        warmup_bars=WARMUP,
        poll_seconds=0,
        fetch=Venue(clock, EPOCH - WARMUP * BAR),
        clock=clock,
    )
    arena = arena_for(tmp_path, engine)
    seen = []
    arena.on_event = lambda kind, payload: (
        seen.append(payload) if kind == "live_progress" else None
    )
    recording = arena.run_live(
        feed, run_id=run_id, out_root=tmp_path, stop=lambda: len(seen) >= bars
    )
    return tmp_path / run_id, recording


def read_observations(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "observations.jsonl").read_text().splitlines()
        if line.strip()
    ]


def replay_with_venue(tmp_path: Path, observations: list[dict], engine: str = "procedural") -> dict:
    """Rebuild the market from the log, with the warm-up the venue still serves, and replay it.

    This is the continuation path (`CandleFeed.resume`): the warm-up is the bars the venue
    closed before the session's first traded bar, and the traded bars are the log's own mids.
    It reconstructs the window the session actually saw, which is what makes the replay the
    same experiment rather than a similar one.
    """
    late = Clock(EPOCH + (len(observations) + 10) * BAR)
    feed = CandleFeed(
        spec(),
        warmup_bars=WARMUP,
        poll_seconds=0,
        fetch=Venue(late, EPOCH - WARMUP * BAR),
        clock=late,
    )
    live = feed.resume(observations)
    return arena_for(tmp_path, engine).run(
        run_id="replay", season=live.window, out_root=tmp_path
    )


def placeholder_window(observations: list[dict]) -> Season:
    """Rebuild the window from the log alone: one placeholder bar for the lost warm-up.

    The same rebuild `flyvsly.salvage` uses, because a session's warm-up was never written
    down. Bar ``i`` still means observation ``i``; the placeholder only fills the seat the
    first warm-up bar would have taken.
    """
    closes = [float(o["market"]["mid"]) for o in observations]
    times = [int(o["t"]) for o in observations]
    return Season(
        dataclasses.replace(spec(), bars=len(closes)),
        [closes[0]] + closes,
        [times[0] - BAR] + times,
        {"mode": "live", "rebuilt_from": "observations.jsonl"},
    )


def frame_hashes(season: Season) -> list[str]:
    hashes = []
    for i in range(season.bars):
        quote = season.quote(i)
        frame = market_frame(season.spec.product, season.history(i), quote.bid, quote.ask)
        hashes.append(hashlib.sha256(frame.tobytes()).hexdigest())
    return hashes


def frames(observations: list[dict]) -> list[str]:
    return [o["frame_sha256"] for o in observations]


def sides(observations: list[dict]) -> dict[str, list[str]]:
    return {arm: [o["arms"][arm]["decision"]["side"] for o in observations] for arm in ARMS}


def salvaged_fixture() -> Path | None:
    override = os.environ.get("FLYVSLY_LIVE_RUN")
    run_dir = Path(override) if override else SALVAGED
    return run_dir if (run_dir / "observations.jsonl").exists() else None


def test_a_live_session_replays_to_the_same_decisions(tmp_path):
    """The procedural engine, rebuilt with the warm-up it saw: exact, bar for bar.

    The venue still serves the warm-up here, so the replay's window is the live one. Both arms
    must make the recorded decision on every bar, and the frames handed to them must hash
    identically — a decision that agreed by accident would not survive the frame check.
    """
    run_dir, _ = run_live_session(tmp_path, "live-parity", bars=12)
    logged = read_observations(run_dir)
    replayed = replay_with_venue(tmp_path, logged)["observations"]

    assert [(o["t"], o["market"]["mid"]) for o in replayed] == [
        (o["t"], o["market"]["mid"]) for o in logged
    ]
    assert frames(replayed) == frames(logged)
    assert sides(replayed) == sides(logged)
    # The fixture is only evidence if it actually decided something.
    assert any(side != "HOLD" for side in sides(logged)["gordon"])


def test_a_replay_from_the_log_alone_is_blind_where_the_warm_up_fed(tmp_path):
    """Without the venue's warm-up the early bars are a different chart, and they decide differently.

    This is the honest limit of a log-only replay: bar 0 of the reconstruction has two bars of
    history behind it, so the procedural rule — which needs more than its 20-bar lookback —
    holds, while the live session bought. Once 100 logged bars sit behind the bar, the frame is
    exactly the one recorded, because that is all ``market_frame`` reads.
    """
    run_dir, _ = run_live_session(tmp_path, "live-log-only", bars=FRAME_BARS + 5)
    logged = read_observations(run_dir)
    window = placeholder_window(logged)
    replayed = arena_for(tmp_path, "procedural").run(
        run_id="log-only-replay", season=window, out_root=tmp_path
    )["observations"]

    logged_frames, replayed_frames = frames(logged), frames(replayed)
    assert logged_frames[0] != replayed_frames[0]
    # From here the 100-bar frame window is wholly inside the log, so the frames must agree.
    assert replayed_frames[FRAME_BARS - 1 :] == logged_frames[FRAME_BARS - 1 :]
    # And the loss is not cosmetic: with too little history the rule cannot score, so it holds
    # where the session, which saw its warm-up, bought.
    assert sides(replayed)["gordon"][0] == "HOLD"
    assert sides(logged)["gordon"][0] != "HOLD"


def test_the_salvaged_session_reproduces_its_frames_where_the_log_covers_the_window():
    """The real 7-hour fixture, one level down from decisions: are its inputs reproducible?

    The session's warm-up was never stored, so bars 0-98 cannot be rebuilt and their frames do
    not match. From bar 99 the reconstruction is byte-identical to what the flies were shown.
    This is checkable without the connectome, and it is the half of parity the log can support.
    """
    run_dir = salvaged_fixture()
    if run_dir is None:
        pytest.skip(
            "no salvaged live session on disk; set FLYVSLY_LIVE_RUN to a run directory"
        )
    logged = read_observations(run_dir)
    replayed_frames = frame_hashes(placeholder_window(logged))

    assert frames(logged)[0] != replayed_frames[0]
    assert replayed_frames[FRAME_BARS - 1 :] == frames(logged)[FRAME_BARS - 1 :]
    assert len(logged) > FRAME_BARS


@pytest.mark.slow
@requires_engine
@requires_graph
def test_a_neural_replay_repeats_the_inputs_but_not_the_brain(tmp_path):
    """The neural level: what a replay can and cannot repeat, asserted rather than assumed.

    With the graph, this runs the real fixture through a fresh pair of brains and compares.
    The market half is reproducible — the frames match from bar 99 — but the decision half is
    not: the recorded session's synapses had moved by the end (memory updates never reached the
    disk on their own, and no ``brains/`` snapshot exists), so a replay from baseline is a
    different experiment at the same bars. Decision agreement is measured and reported; it is
    deliberately not asserted, because asserting it would claim a reproduction the log cannot
    carry.
    """
    run_dir = salvaged_fixture()
    if run_dir is None:
        pytest.skip(
            "no salvaged live session on disk; set FLYVSLY_LIVE_RUN to a run directory"
        )
    logged = read_observations(run_dir)[:120]
    window = placeholder_window(logged)
    recording = Arena(
        ArenaConfig(rules=ArenaRules(), market=spec(), engine="neural", out=tmp_path),
        data_root=DATA_ROOT,
    ).run(run_id="neural-parity", season=window, out_root=tmp_path)
    replayed = recording["observations"]

    assert len(replayed) == len(logged)
    assert set(s for arm in ARMS for s in sides(replayed)[arm]) <= SIDES
    assert frames(replayed)[FRAME_BARS - 1 :] == frames(logged)[FRAME_BARS - 1 :]
    # The brain the replay started from is not the brain the session ended with: no snapshot
    # was written, and the learning arm's efficacies had moved.
    assert not (run_dir / "brains").exists()
    moved = [
        o["arms"]["gordon"]["signal"]["memory"]["changed_edges"]
        for o in logged
        if o["arms"]["gordon"]["signal"].get("memory")
    ]
    assert any(count for count in moved if count)

    agree = sum(
        sides(replayed)[arm] == sides(logged)[arm] for arm in ARMS
    )
    print(
        f"neural replay of {len(logged)} bars: {agree}/2 arms matched the log's decisions "
        "exactly; a replay from baseline is expected to diverge once learned weights matter"
    )


def usable_bash():
    """A bash that can run the campaign script, or None. Same probe as the supervisor's test."""
    candidates = [
        os.environ.get("FLYVSLY_BASH"),
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
        shutil.which("sh"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "a=(ok); printf %s ${a[0]}"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except OSError:
            continue
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return candidate
    return None


def test_the_campaign_script_prints_its_plan_without_starting_anything(tmp_path):
    """`--dry-run` is the honest offline check for a script that needs a live venue to run."""
    bash = usable_bash()
    if bash is None:
        pytest.skip("no usable bash on this machine to run scripts/live-campaign.sh")
    root = Path(__file__).resolve().parents[1]
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [bash, "scripts/live-campaign.sh", "--dry-run", "2", "3"],
        cwd=root,
        env={**os.environ, "RUNS": str(runs), "OUT": str(tmp_path / "out.md")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "dry run: nothing started" in proc.stdout
    # One supervisor command per session, and the pooled report at the end.
    assert proc.stdout.count("would run:") == 2
    assert "live-report" in proc.stdout
    assert not runs.exists()


def test_the_campaign_script_refuses_a_size_that_is_not_a_number(tmp_path):
    bash = usable_bash()
    if bash is None:
        pytest.skip("no usable bash on this machine to run scripts/live-campaign.sh")
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [bash, "scripts/live-campaign.sh", "many", "3"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "sessions must be a positive integer" in proc.stderr
