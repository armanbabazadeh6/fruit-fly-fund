"""Continuing a live session that was killed, rather than writing it off.

`salvage` turns a kill into a recording; this file is about the other thing an operator wants
after a crash: the same session, still trading. What a kill leaves behind decides what a
continuation can be, so these tests are written against artifacts on disk — a checkpoint, a
log, the arms' own SQLite — and not against the loop's memory of the session. The two things
that must never happen are a bar traded twice and an account quietly restarted, so those are
what most of this file is about.

Offline and deterministic: the clock and the candle source are injected, a kill is the stop
callback raising, and nothing sleeps on the wall clock.
"""

import json
import sqlite3
import types

import pytest

from flyvsly.arena import Arena
from flyvsly.backends.procedural import ProceduralBackend
from flyvsly.config import ArenaConfig, ArenaRules, MarketSpec
from flyvsly.live import COMPLETION_GRACE_SECONDS, CandleFeed
from flyvsly.salvage import load_resume
from flyvsly.server import RunHub

EPOCH = 1789137600
BAR = 60


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Exchange:
    """An endpoint that keeps serving every bar it has closed, as the real one's page does.

    Returning the old bars is the point: a resumed session whose window was rebuilt wrongly
    gets the bars it already traded handed to it again, and only the log says which those are.
    """

    def __init__(self, clock: Clock, first_opened: int, minutes_per_call: int = 1, horizon: int = 60):
        self.clock = clock
        self.first_opened = first_opened
        self.minutes_per_call = minutes_per_call
        self.horizon = horizon

    def __call__(self):
        self.clock.now += self.minutes_per_call * BAR
        served = (self.clock.now - COMPLETION_GRACE_SECONDS - self.first_opened) // BAR
        rows = []
        for step in range(1, min(int(served), self.horizon) + 1):
            opened = int(self.first_opened + (step - 1) * BAR)
            close = 100.0 + step
            rows.append([opened, close - 1, close + 1, close, close, 0.25])
        return list(reversed(rows))


class Killed(Exception):
    """How a kill looks from inside the loop: the process simply stops being there."""


def kill_after(arena, bars):
    def stop():
        if len(arena.observations) >= bars:
            raise Killed
        return False

    return stop


def live_arena(tmp_path, **rules):
    return Arena(
        ArenaConfig(
            rules=ArenaRules(**rules),
            market=MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR),
            engine="procedural",
            out=tmp_path,
            label="resume-test",
        )
    )


def live_feed(clock, first_opened, warmup=3, exchange=None, **kwargs):
    return CandleFeed(
        MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR),
        warmup_bars=warmup,
        poll_seconds=0,
        fetch=exchange or Exchange(clock, first_opened, **kwargs),
        clock=clock,
    )


def kill_a_session(tmp_path, run_id, bars=3, warmup=3, brain_every=0):
    """A session that trades `bars` bars and is then killed between two of them.

    The kill is the stop callback raising, which is the same shape as the process going away:
    no clean shutdown, no recording, and the checkpoint left saying `running`.
    """
    clock = Clock(EPOCH)
    exchange = Exchange(clock, EPOCH - warmup * BAR)
    arena = live_arena(tmp_path)
    feed = live_feed(clock, EPOCH - warmup * BAR, warmup=warmup, exchange=exchange)
    with pytest.raises(Killed):
        arena.run_live(
            feed,
            run_id=run_id,
            out_root=tmp_path,
            stop=kill_after(arena, bars),
            brain_every=brain_every,
        )
    return clock, exchange


def log_of(run):
    text = (run / "observations.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def ledger_of(run, arm):
    with sqlite3.connect(run / f"{arm}.sqlite") as db:
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        orders = db.execute("SELECT status, plan FROM orders").fetchall()
    return {"tick": int(json.loads(meta["tick"])), "orders": orders}


def resume_arena(tmp_path, **rules):
    """The arena a continuation runs on: its own config, the session's own directory."""
    return live_arena(tmp_path, **rules)


# -- 1. a bar already in the log is never traded again -----------------------------------


def test_a_continuation_trades_only_bars_the_log_does_not_have(tmp_path):
    """The log is the boundary. Every bar below it is adopted; every bar above it is new.

    The venue is still serving the bars the killed session traded, so a continuation that
    rebuilt its window from the venue alone would hand them to the flies a second time. The
    observations themselves are compared, not just their count: a bar traded again shows up as
    a duplicate timestamp and a different decision on it.
    """
    clock, exchange = kill_a_session(tmp_path, "live-resumed")
    run = tmp_path / "live-resumed"
    killed = log_of(run)
    assert [o["i"] for o in killed] == [0, 1, 2]
    assert json.loads((run / "live_state.json").read_text())["status"] == "running"
    assert not (run / "recording.json").exists()

    resume = load_resume(run)
    assert resume.bars_traded == 3
    assert [o["t"] for o in resume.observations] == [o["t"] for o in killed]

    arena = resume_arena(tmp_path)
    resumed = arena.run_live(
        live_feed(clock, EPOCH - 3 * BAR, exchange=exchange),
        run_id=resume.run_id,
        out_root=tmp_path,
        resume=resume,
        stop=lambda: len(arena.observations) >= 5,
    )

    written = resumed["observations"]
    assert [o["i"] for o in written] == [0, 1, 2, 3, 4]
    times = [o["t"] for o in written]
    assert times == sorted(set(times)) == [EPOCH + i * BAR for i in range(5)]
    # The bars it adopted are the bars that were recorded, unchanged.
    assert written[:3] == killed
    assert resumed["run"]["live"]["bars_traded"] == 5
    assert resumed["run"]["live"]["session_opened"] == EPOCH
    assert log_of(run) == written
    # ...and the session is closed now, so it cannot be continued a second time by accident.
    assert json.loads((run / "live_state.json").read_text())["status"] == "finished"


def test_the_checkpoint_a_continuation_leaves_names_the_bar_it_continued_from(tmp_path):
    """The state file has to let the next reader tell a continued session from a fresh one."""
    clock, exchange = kill_a_session(tmp_path, "live-resumed-state")
    run = tmp_path / "live-resumed-state"
    resume = load_resume(run)

    arena = resume_arena(tmp_path)
    recording = arena.run_live(
        live_feed(clock, EPOCH - 3 * BAR, exchange=exchange),
        run_id=resume.run_id,
        out_root=tmp_path,
        resume=resume,
        stop=lambda: len(arena.observations) >= 4,
    )
    resumed = recording["run"]["resumed"]
    assert resumed["continued_from_bar"] == 3
    assert resumed["continued_from_t"] == EPOCH + 2 * BAR
    assert resumed["session_opened"] == EPOCH
    assert resumed["killed_at"]["status"] == "running"
    assert resumed["killed_at"]["bars_traded"] == 3
    assert "reopened" in resumed["accounts"]
    assert "ever written down" in resumed["not_recovered"]
    assert recording["summary"]["resumed"] == resumed


# -- 2. the accounts and the curves continue ---------------------------------------------


def test_a_continuation_reopens_the_ledgers_rather_than_creating_them(tmp_path):
    """A killed session's account is a file, and the continuation has to keep it.

    Enough bars are traded to fill once, so cash and positions at the join are not the
    starting capital: a continuation that created its ledgers afresh would show a curve that
    jumps back to $100, and a tick counter that starts again. The tick is the sharp end of
    this — it counts the bars this account has been marked on, across both processes.
    """
    clock, exchange = kill_a_session(tmp_path, "live-resumed-account", bars=3, warmup=24)
    run = tmp_path / "live-resumed-account"
    killed = log_of(run)
    for arm_id in ("gordon", "warren"):
        assert ledger_of(run, arm_id)["tick"] == 3
    assert killed[0]["arms"]["gordon"]["execution"]["fill"], "the first bar was expected to fill"
    first_equity = [float(o["arms"]["gordon"]["portfolio"]["equity"]) for o in killed]
    assert first_equity[0] != 100.0, "a fill has to have moved the account off its starting cash"
    orders_before = len(ledger_of(run, "gordon")["orders"])
    assert orders_before, "the killed session's own intents are the ones the check must pass"

    resume = load_resume(run)
    arena = resume_arena(tmp_path)
    recording = arena.run_live(
        live_feed(clock, EPOCH - 24 * BAR, warmup=24, exchange=exchange),
        run_id=resume.run_id,
        out_root=tmp_path,
        resume=resume,
        stop=lambda: len(arena.observations) >= 5,
    )

    curve = recording["summary"]["arms"]["gordon"]["curve"]
    assert len(curve) == 5
    assert curve[:3] == first_equity
    assert recording["summary"]["arms"]["gordon"]["fills"] >= 1
    assert len(ledger_of(run, "gordon")["orders"]) > orders_before
    for arm_id in ("gordon", "warren"):
        # Five bars marked on the same account the killed session was marking.
        assert ledger_of(run, arm_id)["tick"] == 5


# -- 3. which brain the flies restarted with ---------------------------------------------


def test_a_continuation_without_a_brain_snapshot_says_the_brain_was_restarted(tmp_path):
    """The learned efficacies only ever lived in memory. A recording may not pretend otherwise.

    The procedural engine keeps no learned state at all, and this session checkpointed
    nothing, so the honest statement is the same for both arms, for different reasons: one had
    nothing to lose, the other lost what it had.
    """
    clock, exchange = kill_a_session(tmp_path, "live-resumed-nobrain")
    run = tmp_path / "live-resumed-nobrain"
    resume = load_resume(run)
    assert resume.brains == {}

    arena = resume_arena(tmp_path)
    recording = arena.run_live(
        live_feed(clock, EPOCH - 3 * BAR, exchange=exchange),
        run_id=resume.run_id,
        out_root=tmp_path,
        resume=resume,
        stop=lambda: len(arena.observations) >= 4,
    )

    brains = recording["run"]["resumed"]["brains"]
    assert brains["gordon"]["mode"] == "baseline"
    assert "in memory" in brains["gordon"]["reason"]
    assert brains["warren"]["mode"] == "baseline"
    assert "frozen" in brains["warren"]["reason"]


class StubController:
    """The part of a fly's brain a continuation touches: where its weights are kept."""

    def __init__(self, learning: bool):
        self.restored: list[str] = []
        self.brain = types.SimpleNamespace(weights_frozen=not learning)
        self._learning = learning

    def restore(self, path):
        self.restored.append(str(path))
        # What upstream's restore does: the flag comes back out of the checkpoint, and for a
        # fly that was learning that flag is False.
        self.brain.weights_frozen = not self._learning


class StubBackend:
    """A stand-in for the neural engine: its shape, without the 1.6 GB graph.

    The signals it emits are the procedural backend's own, so the bar loop sees exactly the
    shape it sees in any other test; what is stubbed is only the brain — the weights and the
    controller that can save and restore them. This is the one thing in these tests that can
    put a brain on disk, which is what makes the "resumed from a checkpoint" branch reachable
    offline.
    """

    engine = "neural"
    label = "stub brain"
    population_description = None
    readout = None

    def __init__(self, settings):
        self.settings = settings
        self.learning = bool(settings.learning)
        self.controller = StubController(self.learning)
        self._signals = ProceduralBackend(settings)

    def describe(self):
        return {"signal_source": "stub", "label": self.label, "memory_updates_applied": self.learning}

    def observe(self, frame, reinforcement, visible_history=None):
        return self._signals.observe(frame, reinforcement, visible_history)

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stub brain\n")

    def reset(self, keep_memory=False):
        pass


def test_a_continuation_restores_the_brain_the_session_checkpointed(tmp_path, monkeypatch):
    """A session that checkpointed its brain can be continued with it, and says which bar.

    The snapshot is at most one cadence old, so the recording names the bar it was taken at
    rather than implying it is the brain the process died holding.
    """
    made = []

    def build(engine, settings, data_root, seed, rules=None):
        backend = StubBackend(settings)
        made.append(backend)
        return backend

    monkeypatch.setattr("flyvsly.arena._build_backend", build)
    clock = Clock(EPOCH)
    exchange = Exchange(clock, EPOCH - 12 * BAR)
    arena = Arena(
        ArenaConfig(
            rules=ArenaRules(),
            market=MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR),
            engine="neural",
            out=tmp_path,
            label="brain-resume",
        )
    )
    with pytest.raises(Killed):
        arena.run_live(
            live_feed(clock, EPOCH - 12 * BAR, warmup=12, exchange=exchange),
            run_id="live-resumed-brain",
            out_root=tmp_path,
            stop=kill_after(arena, 5),
            brain_every=4,
        )
    run = tmp_path / "live-resumed-brain"
    snapshot = run / "brains" / "gordon.npz"
    assert snapshot.is_file()
    checkpoint = json.loads((run / "live_state.json").read_text())
    assert checkpoint["brains"]["gordon"]["bar"] == 4
    assert checkpoint["brains"]["gordon"]["file"] == "brains/gordon.npz"
    assert checkpoint["on_restart"]["brain"].startswith("checkpointed to brains/ every 4 bars")
    # The killed session's own brains are the ones that were built for it.
    assert [b.learning for b in made] == [True, False]
    assert made[0].controller.restored == []

    resume = load_resume(run)
    assert resume.brains["gordon"]["bar"] == 4
    assert resume.brains["gordon"]["weights"]["file"] == str(snapshot)
    assert resume.brains["warren"]["bar"] == 4

    second = Arena(
        ArenaConfig(
            rules=ArenaRules(),
            market=MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR),
            engine="neural",
            out=tmp_path,
            label="brain-resume",
        )
    )
    recording = second.run_live(
        live_feed(clock, EPOCH - 12 * BAR, warmup=12, exchange=exchange),
        run_id=resume.run_id,
        out_root=tmp_path,
        resume=resume,
        stop=lambda: len(second.observations) >= 6,
        brain_every=0,
    )

    brains = recording["run"]["resumed"]["brains"]
    assert brains["gordon"]["mode"] == "checkpoint"
    assert brains["gordon"]["bar"] == 4
    assert brains["gordon"]["behind_bars"] == 1
    assert brains["gordon"]["sha256"] == resume.brains["gordon"]["weights"]["sha256"]
    # Both flies' weights went back into their own arm, and only those two arms.
    assert made[2].controller.restored == [str(snapshot)]
    assert made[3].controller.restored == [str(run / "brains" / "warren.npz")]
    # A resumed competition keeps learning: the exam path would have frozen these weights.
    assert [made[2].controller.brain.weights_frozen, made[3].controller.brain.weights_frozen] == [
        False,
        True,
    ]


# -- 4. refusals, before anything starts -------------------------------------------------


def write_killed(root, name, observations, **checkpoint):
    run = root / name
    run.mkdir(parents=True, exist_ok=True)
    state = {
        "schema": "flyvsly.recording/v1",
        "run_id": name,
        "status": "running",
        "product": "BTC-USDC",
        "venue": "kraken",
        "bar_seconds": BAR,
        "warmup_bars": 120,
        "session_opened": EPOCH,
        "bars_traded": len(observations),
        "last_bar": observations[-1]["t"] if observations else None,
        "polls": 40,
        **checkpoint,
    }
    (run / "live_state.json").write_text(json.dumps(state))
    (run / "observations.jsonl").write_text("".join(json.dumps(o) + "\n" for o in observations))
    return run


def observation(index, mid):
    """One logged bar, with everything a continuation reads back out of it."""
    return {
        "i": index,
        "t": EPOCH + index * BAR,
        "product": "BTC-USDC",
        "market": {"bid": str(mid - 2), "ask": str(mid + 2), "mid": mid},
        "frame_sha256": "0" * 64,
        "arms": {
            arm: {
                "signal": {"signal_source": "neural", "side": "HOLD", "memory": {"changed_edges": 2}},
                "decision": {"side": "HOLD", "explanation": {"kind": "neural", "steps": ["rule"]}},
                "execution": {"status": "HOLD", "fill": None},
                "portfolio": {
                    "cash": "100",
                    "positions": {"BTC-USDC": "0"},
                    "equity": "100",
                    "return_pct": 0.0,
                    "fees_paid": "0",
                    "fills": 0,
                    "vetoes": 0,
                    "halted": None,
                },
                "stimulus": {"kind": "pnl", "delta_usdc": "0"},
                "compute_seconds": 0.5,
            }
            for arm in ("gordon", "warren")
        },
    }


def write_intents(run, arm, bars):
    """An arm's ledger as the order path leaves it: one intent per bar it decided to trade."""
    with sqlite3.connect(run / f"{arm}.sqlite") as db:
        db.execute(
            "CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT NOT NULL, created REAL NOT "
            "NULL, plan TEXT NOT NULL, exchange_id TEXT, settlement TEXT)"
        )
        for index, bar in enumerate(bars):
            db.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?)",
                (
                    f"intent-{index}",
                    "SETTLED",
                    float(index),
                    json.dumps({"neural_observation": {"bar": index, "t": bar}}),
                    None,
                    None,
                ),
            )


def test_a_finished_session_is_not_resumed(tmp_path):
    """A recording is evidence of an experiment that is over; a continuation would rewrite it."""
    clock = Clock(EPOCH)
    arena = live_arena(tmp_path)
    arena.run_live(
        live_feed(clock, EPOCH - 3 * BAR),
        run_id="live-finished",
        out_root=tmp_path,
        stop=lambda: len(arena.observations) >= 2,
    )
    run = tmp_path / "live-finished"
    assert (run / "recording.json").exists()

    with pytest.raises(FileExistsError, match="recording.json"):
        load_resume(run)

    hub = RunHub(tmp_path, "data")
    answer = hub.start_live({"resume": "live-finished"})
    assert answer["started"] is False
    assert "recording.json" in answer["reason"]
    assert answer["error"] == answer["reason"]
    assert hub.state["status"] == "failed"
    assert "recording.json" in hub.state["error"]
    # Nothing was started in its place, and the finished session's own files are untouched.
    assert hub.thread is None
    assert json.loads((run / "live_state.json").read_text())["status"] == "finished"


def test_a_session_that_is_not_there_is_not_resumed(tmp_path):
    hub = RunHub(tmp_path, "data")
    answer = hub.start_live({"resume": "20260911-180303-live"})
    assert answer["started"] is False
    assert "no live_state.json" in answer["reason"]
    assert hub.state["error"] == answer["reason"]
    assert hub.thread is None
    with pytest.raises(FileNotFoundError, match="no live_state.json"):
        load_resume(tmp_path / "20260911-180303-live")
    with pytest.raises(FileNotFoundError):
        load_resume(tmp_path / "nowhere")


def test_a_session_killed_inside_a_bar_is_refused_rather_than_double_counted(tmp_path):
    """The one kill a continuation cannot honestly absorb.

    An intent is reserved before the fill and before the bar reaches the log, so a kill inside
    the order path leaves an order for a bar the log does not hold. Continuing would either
    trade that bar again or account for its fill twice, and both are worse than saying no.
    """
    run = write_killed(tmp_path, "live-midbar", [observation(0, 100.0), observation(1, 101.0)])
    write_intents(run, "gordon", [EPOCH, EPOCH + BAR, EPOCH + 2 * BAR])

    with pytest.raises(ValueError, match="killed inside a bar"):
        load_resume(run)

    # An intent for a bar the log *does* hold is the normal case, and must not be refused.
    clean = write_killed(tmp_path, "live-between", [observation(0, 100.0), observation(1, 101.0)])
    write_intents(clean, "gordon", [EPOCH, EPOCH + BAR])
    assert load_resume(clean).bars_traded == 2


def test_a_bar_the_log_was_still_writing_is_not_a_bar_that_was_traded(tmp_path):
    """A kill inside the append leaves a partial line; the bar it describes never happened.

    Dropping it is what makes the difference between a log that ends in a whole observation and
    one that ends in half of one, and the recording says which of the two it read.
    """
    clock, exchange = kill_a_session(tmp_path, "live-torn")
    run = tmp_path / "live-torn"
    with (run / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"i": 3, "t": ')

    resume = load_resume(run)
    assert resume.dropped_tail is True
    assert resume.bars_traded == 3

    arena = resume_arena(tmp_path)
    recording = arena.run_live(
        live_feed(clock, EPOCH - 3 * BAR, exchange=exchange),
        run_id=resume.run_id,
        out_root=tmp_path,
        resume=resume,
        stop=lambda: len(arena.observations) >= 4,
    )
    assert recording["run"]["resumed"]["dropped_partial_line"] is True
    assert log_of(run) == recording["observations"]
    assert [o["i"] for o in recording["observations"]] == [0, 1, 2, 3]


# -- 5. the window a continuation is rebuilt on ------------------------------------------


def test_the_rebuilt_window_ends_at_the_bars_the_session_traded(tmp_path):
    """The window is the log's own bars, in front of whatever warm-up the venue still has."""
    clock = Clock(EPOCH)
    feed = live_feed(clock, EPOCH - 3 * BAR)
    logged = [observation(0, 110.0), observation(1, 111.0)]
    season = feed.resume(logged)

    assert season.warmup_bars == 3
    assert season.bars == 2
    assert [season.timestamp(i) for i in range(2)] == [EPOCH, EPOCH + BAR]
    # The mids are the ones the flies decided on, not the venue's closes for the same minutes.
    assert [season.mid(i) for i in range(2)] == [110.0, 111.0]
    assert season.next_bar_opened == EPOCH + 2 * BAR
    assert "before this session's first traded bar" in season.provenance["warmup"]
    assert season.provenance["resumed_bars"] == 2
    # The bars it already holds cannot come back in through a poll.
    assert season.advance([(EPOCH, 999.0), (EPOCH + BAR, 999.0), (EPOCH + 2 * BAR, 112.0)]) == 1
    assert season.mid(2) == 112.0


def test_a_warmup_the_venue_no_longer_has_is_labelled_as_one_placeholder(tmp_path):
    """Kraken keeps a page of history, not a memory; a long outage must not invent a chart."""
    clock = Clock(EPOCH + 500 * BAR)
    feed = live_feed(clock, EPOCH + 440 * BAR, warmup=3)
    season = feed.resume([observation(0, 110.0), observation(1, 111.0)])

    assert season.warmup_bars == 1
    assert season.bars == 2
    assert [season.timestamp(i) for i in range(2)] == [EPOCH, EPOCH + BAR]
    assert "placeholder" in season.provenance["warmup"]
    assert season.timestamp(0) == EPOCH
