"""What a live session has already written when it dies, and what its buttons promise.

A recorded run is finished before anything reads it; a live one can be taken away at any
minute — Docker going down, a power cut, `docker rm -f` — so the question this file asks is
not what the loop computes but what a reader of the *files* finds afterwards. The loop's own
contract (one bar once, in market order, never a forming bar) is tests/test_live_session.py;
this file pins the durable record, the kill boundary, salvage's fidelity to that record, and
the hub's start/stop/failure surface.

Offline and deterministic: the clock and the candle source are injected, the stop callback is
the only way a session ends in these tests, and nothing sleeps on the wall clock.
"""

import json
import threading
from pathlib import Path

from flyvsly.arena import Arena
from flyvsly.config import ArenaConfig, ArenaRules, MarketSpec
from flyvsly.live import CandleFeed, COMPLETION_GRACE_SECONDS
from flyvsly.salvage import salvage_run
from flyvsly.server import RunHub

EPOCH = 1789137600
BAR = 60


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Exchange:
    """An endpoint whose clock advances one bar per poll, as the real one's does."""

    def __init__(self, clock: Clock, first_opened: int, minutes_per_call: int = 1, horizon: int = 40):
        self.clock = clock
        self.first_opened = first_opened
        self.minutes_per_call = minutes_per_call
        self.horizon = horizon

    def __call__(self):
        self.clock.now += self.minutes_per_call * BAR
        served = (self.clock.now - COMPLETION_GRACE_SECONDS - self.first_opened) // BAR
        rows = []
        for step in range(1, min(served, self.horizon) + 1):
            opened = int(self.first_opened + (step - 1) * BAR)
            close = 100.0 + step
            rows.append([opened, close - 1, close + 1, close, close, 0.25])
        return list(reversed(rows))


def live_arena(tmp_path, **rules):
    return Arena(
        ArenaConfig(
            rules=ArenaRules(**rules),
            market=MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR),
            engine="procedural",
            out=tmp_path,
            label="durability-test",
        )
    )


def live_feed(clock: Clock, first_opened: int, warmup: int = 3, **kwargs):
    return CandleFeed(
        MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR),
        warmup_bars=warmup,
        poll_seconds=0,
        fetch=Exchange(clock, first_opened, **kwargs),
        clock=clock,
    )


def read_run(run: Path) -> dict:
    """The checkpoint and every bar that survived, as the next reader of the directory sees them."""
    text = (run / "observations.jsonl").read_text()
    return {
        "state": json.loads((run / "live_state.json").read_text()),
        "raw": text,
        "lines": [json.loads(line) for line in text.splitlines() if line.strip()],
    }


# -- 1. the durable record of the bar loop ----------------------------------------------


def test_a_bar_is_on_disk_before_it_is_announced(tmp_path):
    """Durable first, announce second: a bar the page has seen must be one an auditor can read.

    The listener reads the checkpoint and the log *inside* the announcement, so it sees the
    state of the world at the instant the page is told about a bar. If the loop announced
    first and wrote after, every one of these reads would be one bar behind.
    """
    clock = Clock(EPOCH)
    arena = live_arena(tmp_path)
    run = tmp_path / "live-durable"
    seen = []

    def on_event(kind, payload):
        if kind != "live_progress":
            return
        on_disk = read_run(run)
        seen.append(
            {
                "announced": payload["bars"],
                "state_bars": on_disk["state"]["bars_traded"],
                "status": on_disk["state"]["status"],
                "last_bar": on_disk["state"]["last_bar"],
                "lines": len(on_disk["lines"]),
                "last_i": on_disk["lines"][-1]["i"],
                "last_t": on_disk["lines"][-1]["t"],
            }
        )

    arena.on_event = on_event
    recording = arena.run_live(
        live_feed(clock, EPOCH - 3 * BAR),
        run_id="live-durable",
        out_root=tmp_path,
        # The bar bound is a failsafe, not the stopping rule: if a listener ever blew up
        # inside the announcement the hub swallows it, and without a bound the starving feed
        # would spin forever instead of failing the test.
        stop=lambda: len(seen) >= 4 or arena.season.bars >= 12,
    )

    assert [entry["announced"] for entry in seen] == [1, 2, 3, 4]
    for entry in seen:
        assert entry["status"] == "running"
        assert entry["state_bars"] == entry["announced"]
        assert entry["lines"] == entry["announced"]
        assert entry["last_i"] == entry["announced"] - 1
        # The checkpoint and the log name the same bar, so the two artifacts cannot disagree
        # about where the session is.
        assert entry["last_bar"] == entry["last_t"]

    written = read_run(run)["lines"]
    assert [o["i"] for o in written] == [0, 1, 2, 3]
    assert [o["t"] for o in written] == [EPOCH + i * BAR for i in range(4)]
    # Exactly once each: the durable log is the recording's own observation list, in order,
    # not a superset that grew through appended checkpoints.
    assert written == recording["observations"]
    assert [o["t"] for o in written] == sorted({o["t"] for o in written})

    final = read_run(run)["state"]
    assert final["status"] == "finished"
    assert final["bars_traded"] == len(written) == 4


# -- 2. the kill boundary ---------------------------------------------------------------


def test_a_session_killed_between_bars_leaves_a_consistent_checkpoint(tmp_path):
    """If the process dies between two bars, the files it leaves describe a whole session.

    The kill is a stop callback flipping — not an exception — and the snapshot is taken at
    the moment it flips, because that is the instant the process would be gone: the checkpoint
    as the last bar left it, before any clean shutdown could touch it. The loop's finalise
    afterwards belongs to the clean-stop path, and the test checks that it rewrites the log
    rather than appending a second copy of the session to it.
    """
    clock = Clock(EPOCH)
    arena = live_arena(tmp_path)
    run = tmp_path / "live-killed"
    boundary = {}

    def stop():
        # Polled between bars. Two bars is enough to say "the session was trading"; flipping
        # here models the machine disappearing before the loop could reach its next bar. The
        # bar bound is a failsafe so a loop that stopped appending bars fails instead of
        # spinning on a starving feed.
        if len(arena.observations) >= 2 and not boundary:
            boundary.update(read_run(run))
            return True
        return arena.season.bars >= 12

    recording = arena.run_live(
        live_feed(clock, EPOCH - 3 * BAR), run_id="live-killed", out_root=tmp_path, stop=stop
    )

    # What the kill left behind.
    assert boundary["state"]["status"] == "running"
    assert boundary["state"]["bars_traded"] == 2
    assert boundary["state"]["bars_traded"] == len(boundary["lines"]) == 2
    # No half-written bar: the log ends on a newline and every line, the last one included,
    # is a complete observation.
    assert boundary["raw"].endswith("\n")
    assert [o["i"] for o in boundary["lines"]] == [0, 1]
    assert [o["t"] for o in boundary["lines"]] == [EPOCH, EPOCH + BAR]
    assert boundary["state"]["last_bar"] == boundary["lines"][-1]["t"]

    # And the session the stop ended is the same session: the finalise that a real kill
    # prevents rewrites the same log rather than appending a second copy of it.
    final = read_run(run)
    assert final["state"]["status"] == "finished"
    assert final["state"]["bars_traded"] == len(final["lines"]) == 2
    assert [o["i"] for o in final["lines"]] == [0, 1]
    assert final["lines"] == recording["observations"]


# -- 3. salvage is a function of the durable record -------------------------------------

KILLED_ID = "20260911-180303-live"


def arm_record(equity: float, fills: int, fee: str, side: str = "HOLD", fill: dict | None = None):
    return {
        "signal": {
            "signal_source": "neural",
            "side": side,
            "memory": {"enabled": True, "changed_edges": 7, "mean_efficacy": 0.99},
        },
        "decision": {"side": side, "explanation": {"kind": "neural", "steps": ["rule"], "result": side}},
        "execution": {
            "status": "FILLED" if fill else "HOLD",
            "reason": "paper fill" if fill else "no order",
            "fill": fill,
        },
        "portfolio": {
            "cash": "99.0",
            "positions": {"BTC-USDC": "0.0001" if fills else "0"},
            "equity": str(equity),
            "return_pct": (equity / 100 - 1) * 100,
            "fees_paid": fee,
            "fills": fills,
            "vetoes": 0,
            "halted": None,
        },
        "stimulus": {"kind": "pnl", "delta_usdc": "0"},
        "compute_seconds": 7.5,
    }


def observation(index: int, mid: float, gordon: dict, warren: dict) -> dict:
    return {
        "i": index,
        "t": EPOCH + index * BAR,
        "product": "BTC-USDC",
        "market": {"bid": str(mid - 2), "ask": str(mid + 2), "mid": mid},
        "frame_sha256": "0" * 64,
        "same_frame_both_arms": True,
        "same_neural_input_both_arms": True,
        "arms": {"gordon": gordon, "warren": warren},
    }


def killed_observations() -> list[dict]:
    """Three bars with a different fill story per arm, so a curve read off the wrong arm shows."""
    fill = {
        "mode": "paper",
        "status": "FILLED",
        "base": "0.00012",
        "quote": "9.25",
        "fee": "0.0555",
        "price": "77083.33",
    }
    return [
        observation(0, 77335.92, arm_record(100.0, 0, "0"), arm_record(100.0, 0, "0")),
        observation(
            1, 77173.79, arm_record(99.9366, 1, "0.0555", "BUY", fill), arm_record(99.93, 1, "0.0555", "BUY", fill)
        ),
        observation(2, 77122.55, arm_record(99.9301, 1, "0.0555"), arm_record(99.8667, 2, "0.111", "BUY", fill)),
    ]


def write_killed_session(root: Path, observations, arms=None) -> Path:
    """A directory as a killed session left it: a running checkpoint and the bars it traded."""
    run = root / KILLED_ID
    run.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "schema": "flyvsly.recording/v1",
        "run_id": run.name,
        "status": "running",
        "product": "BTC-USDC",
        "bar_seconds": BAR,
        "warmup_bars": 120,
        "session_opened": EPOCH - BAR,
        "bars_traded": len(observations),
        "last_bar": observations[-1]["t"] if observations else None,
        "lag_seconds": 24.0,
        "polls": 40,
    }
    if arms is not None:
        checkpoint["arms"] = arms
        checkpoint["rules"] = {"capital": "100", "order_limit": "10", "daily_orders": 24}
        checkpoint["engine"] = "neural"
        checkpoint["kind"] = "competition"
    (run / "live_state.json").write_text(json.dumps(checkpoint))
    (run / "observations.jsonl").write_text("".join(json.dumps(o) + "\n" for o in observations))
    return run


def expected_trades(arm_id: str, observations) -> list[dict]:
    """The trades the observations themselves describe, read straight off the durable record."""
    trades = []
    for o in observations:
        record = o["arms"][arm_id]
        fill = record["execution"].get("fill")
        if not fill:
            continue
        trades.append(
            {
                "i": o["i"],
                "t": o["t"],
                "side": record["decision"]["side"],
                "product": o["product"],
                "base_size": fill["base"],
                "price": fill["price"],
                "quote_size": fill["quote"],
                "fee": fill["fee"],
                "reason": record["execution"]["reason"],
                "equity_after": record["portfolio"]["equity"],
                "memory_changed_edges": record["signal"]["memory"]["changed_edges"],
            }
        )
    return trades


def test_recorded_arm_metadata_gives_curves_and_trades_the_observations_agree_with(tmp_path):
    """The rebuilt summary must be a reading of the log, not a guess about it.

    The checkpoint still knows which flies flew, so the summary has to key its curves and its
    trade lists to those arms and to nothing else: a curve read off the other arm, a trade
    taken from the wrong index, or a fill silently dropped shows up here as a mismatch.
    """
    arms = [
        {"id": "gordon", "name": "Gordon Flykko", "role_label": "Memory updates ON", "learning": True},
        {"id": "warren", "name": "Warren Buzzett", "role_label": "Memory updates OFF", "learning": False},
    ]
    observations = killed_observations()
    run = write_killed_session(tmp_path, observations, arms=arms)

    summary = salvage_run(run)["summary"]

    assert sorted(summary["arms"]) == ["gordon", "warren"]
    for arm in arms:
        arm_id = arm["id"]
        rebuilt = summary["arms"][arm_id]
        assert rebuilt["curve"] == [float(o["arms"][arm_id]["portfolio"]["equity"]) for o in observations]
        assert rebuilt["trades"] == expected_trades(arm_id, observations)
        last = observations[-1]["arms"][arm_id]["portfolio"]
        assert rebuilt["fills"] == last["fills"]
        assert rebuilt["fees_paid"] == last["fees_paid"]
        # A trade's equity_after is that same bar's point on the curve, not some later one.
        for trade in rebuilt["trades"]:
            assert float(trade["equity_after"]) == rebuilt["curve"][trade["i"]]
    # The two arms are not interchangeable: bar 2 belongs to warren alone.
    assert len(summary["arms"]["warren"]["trades"]) == 2
    assert len(summary["arms"]["gordon"]["trades"]) == 1
    assert summary["salvaged"]["arm_metadata"] == "recorded in the checkpoint"


def test_a_checkpoint_without_metadata_says_the_arms_were_rebuilt(tmp_path):
    """A checkpoint older than the metadata cannot name the flies; the summary must say so."""
    run = write_killed_session(tmp_path, killed_observations())
    recording = salvage_run(run)
    summary = recording["summary"]

    assert "rebuilt" in summary["salvaged"]["arm_metadata"]
    assert "defaults" in summary["salvaged"]["arm_metadata"]
    # The flies are named from the live defaults, and each says its description is a rebuild
    # rather than something the session recorded.
    assert {arm["id"] for arm in recording["arms"]} == {"gordon", "warren"}
    assert all(arm["backend"]["rebuilt"] for arm in recording["arms"])
    # The rebuilt labels do not change what the curves are allowed to be.
    for arm_id in ("gordon", "warren"):
        assert summary["arms"][arm_id]["curve"] == [
            float(o["arms"][arm_id]["portfolio"]["equity"]) for o in recording["observations"]
        ]


def test_the_checkpoint_a_live_session_writes_carries_the_metadata_salvage_needs(tmp_path):
    """A real session's own checkpoint must let salvage keep the arms it actually flew.

    The two halves of this feature live in different modules: the arena decides what the
    checkpoint carries, and salvage decides whether that is enough to avoid inventing arm
    metadata. A checkpoint that quietly stopped carrying it would only show up here.
    """
    clock = Clock(EPOCH)
    arena = live_arena(tmp_path)
    traded = []
    arena.on_event = lambda kind, payload: traded.append(payload) if kind == "live_progress" else None
    recording = arena.run_live(
        live_feed(clock, EPOCH - 3 * BAR),
        run_id="live-salvaged",
        out_root=tmp_path,
        stop=lambda: len(traded) >= 3 or arena.season.bars >= 12,
    )

    # The recording exists, so salvage is being asked to replace it: force is the caller
    # saying "I know this directory has one, rebuild it from the checkpoint anyway".
    salvaged = salvage_run(tmp_path / "live-salvaged", force=True)

    assert salvaged["summary"]["salvaged"]["arm_metadata"] == "recorded in the checkpoint"
    assert [arm["id"] for arm in salvaged["arms"]] == [arm["id"] for arm in recording["arms"]]
    assert [arm["name"] for arm in salvaged["arms"]] == [arm["name"] for arm in recording["arms"]]
    for arm in salvaged["arms"]:
        arm_id = arm["id"]
        assert salvaged["summary"]["arms"][arm_id]["curve"] == [
            float(o["arms"][arm_id]["portfolio"]["equity"]) for o in salvaged["observations"]
        ]


# -- 4. the hub's control surface -------------------------------------------------------


def waiting_thread() -> tuple[threading.Thread, threading.Event]:
    """A thread that is alive until the test releases it, so "a run is in progress" is real."""
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    return thread, release


def test_a_live_run_refuses_a_second_start_without_disturbing_the_first(tmp_path):
    hub = RunHub(tmp_path, "data")
    thread, release = waiting_thread()
    hub.thread = thread
    hub.state = {"status": "running", "run_id": "20260911-180303-live", "live": True}
    try:
        assert hub.start_live({}) == {"started": False, "reason": "A run is already in progress."}
        # The refusal is a refusal: the session that is running keeps its thread and its state.
        assert hub.thread is thread
        assert hub.state["status"] == "running"
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_stopping_a_live_session_is_a_request_and_stopping_nothing_is_reported(tmp_path):
    hub = RunHub(tmp_path, "data")
    idle = hub.stop_live()
    assert idle["stopped"] is False
    assert "No live session" in idle["reason"]

    thread, release = waiting_thread()
    hub.thread = thread
    hub.state = {"status": "running", "run_id": "20260911-180303-live", "live": True}
    try:
        assert hub.stop_live() == {"stopped": True}
        assert hub.state["status"] == "stopping"
        # Asked, not killed: the session is still the one deciding its bar.
        assert thread.is_alive()
        assert hub.thread is thread
        assert hub.stop_live() == {"stopped": True}
    finally:
        release.set()
        thread.join(timeout=5)


def test_a_failed_live_session_is_reported_in_the_hub_and_on_the_wire(tmp_path):
    """A session that dies before its first bar must fail loudly, not sit on "starting" forever.

    The hub's state is what a page polls; the broadcast is what a page that is already open
    sees. Both have to carry the reason, or the only symptom is a spinner.
    """
    hub = RunHub(tmp_path, "data")
    channel = hub.subscribe()
    assert hub.start_live({"engine": "not-an-engine"})["started"] is True
    hub.thread.join(timeout=10)

    assert not hub.thread.is_alive()
    assert hub.state["status"] == "failed"
    assert "not-an-engine" in hub.state["error"]
    assert hub.state["trace"]

    events = []
    while not channel.empty():
        events.append(channel.get_nowait())
    assert events[-1][0] == "run_failed"
    assert events[-1][1]["status"] == "failed"
    assert "not-an-engine" in events[-1][1]["error"]

    # The failed session is over, so a stop request reports rather than raising.
    assert hub.stop_live()["stopped"] is False
