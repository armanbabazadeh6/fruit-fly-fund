"""A live session: it trades the bars the exchange closes, in order, exactly once each.

The clock and the candle source are both injected, so a "session" here costs milliseconds
and is fully deterministic. What is being tested is the loop's contract — every completed bar
is traded once, in market order, never a forming one — not the network.
"""

import json

import pytest

from flyvsly.arena import Arena
from flyvsly.config import ArenaConfig, ArenaRules, MarketSpec
from flyvsly.live import COMPLETION_GRACE_SECONDS, CandleFeed

EPOCH = 1789137600
BAR = 60


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Exchange:
    """An endpoint whose clock advances a minute per call, as the real one's does."""

    def __init__(self, clock: Clock, first_opened: int, minutes_per_call: int = 1, horizon: int = 40):
        self.clock = clock
        self.first_opened = first_opened
        self.minutes_per_call = minutes_per_call
        self.horizon = horizon
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.clock.now += self.minutes_per_call * BAR
        # Everything the exchange has closed by now, newest first, in the endpoint's shape.
        served = (self.clock.now - COMPLETION_GRACE_SECONDS - self.first_opened) // BAR
        rows = []
        for step in range(1, min(served, self.horizon) + 1):
            opened = int(self.first_opened + (step - 1) * BAR)
            close = 100.0 + step
            rows.append([opened, close - 1, close + 1, close, close, 0.25])
        return list(reversed(rows))


def arena_for(tmp_path, **rules):
    config = ArenaConfig(
        rules=ArenaRules(**rules),
        market=MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR),
        engine="procedural",
        out=tmp_path,
        label="live-test",
    )
    return Arena(config)


def feed_for(clock: Clock, first_opened: int, warmup: int = 3, **kwargs):
    return CandleFeed(
        MarketSpec(kind="coinbase", product="BTC-USDC", bars=64, bar_seconds=BAR),
        warmup_bars=warmup,
        poll_seconds=0,
        fetch=Exchange(clock, first_opened, **kwargs),
        clock=clock,
    )


def test_live_session_trades_each_closed_bar_once_in_market_order(tmp_path):
    clock = Clock(EPOCH)
    feed = feed_for(clock, EPOCH - 3 * BAR)
    arena = arena_for(tmp_path)
    traded = []

    def on_event(kind, payload):
        if kind == "live_progress":
            traded.append(payload["bars"])

    arena.on_event = on_event
    recording = arena.run_live(
        feed, run_id="live-1", out_root=tmp_path, stop=lambda: len(traded) >= 4
    )

    observations = recording["observations"]
    assert len(observations) == 4
    assert [o["i"] for o in observations] == [0, 1, 2, 3]
    timestamps = [o["t"] for o in observations]
    assert timestamps == sorted(timestamps)
    assert timestamps[0] == EPOCH
    assert len(set(timestamps)) == 4

    record = recording["run"]
    assert record["wall_mode"].startswith("live:")
    assert record["live"]["bars_traded"] == 4
    assert record["live"]["warmup_bars"] == 3
    assert record["live"]["session_opened"] == EPOCH
    assert record["run"] if "run" in record else True
    assert recording["summary"]["live"]["bars_traded"] == 4
    assert recording["summary"]["bars"] == 4
    # A live session is still a fair competition: the only difference stays `learning`.
    assert record["starting_conditions"]["fairness"]["differing_fields"] == ["learning"]
    json.dumps(recording)
    assert (tmp_path / "live-1" / "recording.json").exists()
    assert (tmp_path / "live-1" / "observations.jsonl").exists()


def test_backlog_is_traded_in_order_rather_than_dropped(tmp_path):
    """A machine slower than the bar interval falls behind; it must not skip or reorder."""
    clock = Clock(EPOCH)
    # Five minutes pass between polls: five bars close while nothing is being decided.
    feed = feed_for(clock, EPOCH - 3 * BAR, minutes_per_call=5)
    arena = arena_for(tmp_path)
    traded = []

    def on_event(kind, payload):
        if kind == "live_progress":
            traded.append(payload)

    arena.on_event = on_event
    recording = arena.run_live(
        feed, run_id="live-backlog", out_root=tmp_path, stop=lambda: len(traded) >= 5
    )
    observations = recording["observations"]
    # One poll closed five bars, and all five were traded, oldest first, none skipped.
    assert [o["i"] for o in observations] == [0, 1, 2, 3, 4]
    opened = recording["run"]["live"]["session_opened"]
    assert [o["t"] for o in observations] == [opened + i * BAR for i in range(5)]
    assert traded[0]["available"] == 5
    assert recording["run"]["live"]["bars_traded"] == 5
    # Falling behind is recorded, not hidden: the exchange keeps moving while we decide.
    assert traded[-1]["lag_seconds"] > 0


def test_live_session_stops_between_bars_and_still_writes_its_recording(tmp_path):
    clock = Clock(EPOCH)
    feed = feed_for(clock, EPOCH - 3 * BAR)
    arena = arena_for(tmp_path)
    seen = []

    def on_event(kind, payload):
        if kind == "live_progress":
            seen.append(payload["lag_seconds"])

    arena.on_event = on_event
    recording = arena.run_live(feed, run_id="live-stop", out_root=tmp_path, stop=lambda: True)
    # stop() was already true, so nothing was traded and the artifacts still exist.
    assert recording["observations"] == []
    assert recording["summary"]["bars"] == 0
    assert (tmp_path / "live-stop" / "manifest.json").exists()
    assert seen == []


def test_live_session_refuses_modes_that_need_the_whole_season(tmp_path):
    clock = Clock(EPOCH)
    arena = arena_for(tmp_path, reinforcement="decoy")
    with pytest.raises(ValueError, match="needs the whole season"):
        arena.run_live(feed_for(clock, EPOCH - 3 * BAR), run_id="live-decoy", out_root=tmp_path)


def test_live_session_is_never_an_exam(tmp_path):
    clock = Clock(EPOCH)
    config = ArenaConfig(
        rules=ArenaRules(),
        market=MarketSpec(kind="coinbase", bars=64, bar_seconds=BAR),
        engine="procedural",
        out=tmp_path,
        must_not_overlap="some-earlier-run",
    )
    with pytest.raises(ValueError, match="cannot be an exam"):
        Arena(config).run_live(
            feed_for(clock, EPOCH - 3 * BAR), run_id="live-exam", out_root=tmp_path
        )
