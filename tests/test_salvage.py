"""Salvage: rebuild a recording for a live session that was killed instead of stopped.

Docker going down mid-session is the case that motivated this. The bars and the checkpoint are
already on disk; the memory-resident parts are gone. These tests pin what can be rebuilt, and
that the parts which cannot are labelled rather than invented.
"""

import json

import pytest

from flyvsly.salvage import salvage_run

EPOCH = 1789149720
BAR = 60


def arm_record(equity: float, fills: int, fee: str, side: str = "HOLD", fill: dict | None = None, cash: float = 100.0):
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
            "cash": str(cash),
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


def write_session(root, observations, arms=None, status="running"):
    run = root / "20260911-180303-live"
    run.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "schema": "flyvsly.recording/v1",
        "run_id": run.name,
        "status": status,
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
    (run / "observations.jsonl").write_text(
        "".join(json.dumps(o) + "\n" for o in observations)
    )
    return run


def sample_observations():
    fill = {"mode": "paper", "status": "FILLED", "base": "0.00012", "quote": "9.25", "fee": "0.0555", "price": "77083.33"}
    return [
        observation(0, 77335.92, arm_record(100.0, 0, "0"), arm_record(100.0, 0, "0")),
        observation(1, 77173.79, arm_record(99.9366, 1, "0.0555", "BUY", fill), arm_record(99.93, 1, "0.0555", "BUY", fill)),
        observation(2, 77122.55, arm_record(99.9301, 1, "0.0555"), arm_record(99.8667, 2, "0.111", "BUY", fill)),
    ]


def test_salvage_rebuilds_a_recording_from_what_the_session_wrote(tmp_path):
    run = write_session(tmp_path, sample_observations())
    recording = salvage_run(run)

    assert (run / "recording.json").exists()
    assert (run / "manifest.json").exists()
    summary = recording["summary"]
    assert summary["bars"] == 3
    assert recording["run"]["id"] == run.name
    assert recording["run"]["wall_mode"].startswith("live, salvaged")

    # The curves come from the portfolio the session recorded, bar for bar.
    gordon = summary["arms"]["gordon"]
    assert gordon["curve"] == [100.0, 99.9366, 99.9301]
    assert gordon["final_equity"] == 99.9301
    assert gordon["fills"] == 1
    assert gordon["fees_paid"] == "0.0555"
    assert len(gordon["trades"]) == 1
    assert gordon["trades"][0]["price"] == "77083.33"
    assert gordon["trades"][0]["t"] == EPOCH + BAR
    assert summary["arms"]["warren"]["fills"] == 2
    assert summary["arms"]["warren"]["trades"][-1]["side"] == "BUY"

    # Bar alignment is the thing a rebuilt season gets wrong silently: bar i must be
    # observation i, or every curve is drawn against the wrong price.
    assert recording["season"]["bars"][0]["mid"] == 77335.92
    assert recording["season"]["bars"][2]["mid"] == 77122.55
    assert [bar["t"] for bar in recording["season"]["bars"]] == [EPOCH, EPOCH + BAR, EPOCH + 2 * BAR]

    assert summary["benchmarks"]["buy_and_hold"]["entry_price"]
    assert summary["comparison"]["leader"] in ("gordon", "warren", "tie")
    json.dumps(recording)


def test_salvage_says_which_parts_it_had_to_rebuild(tmp_path):
    run = write_session(tmp_path, sample_observations())
    rebuilt = salvage_run(run)["summary"]["salvaged"]
    assert "rebuilt" in rebuilt["arm_metadata"]
    assert rebuilt["checkpoint_status"] == "running"
    assert rebuilt["bars_traded_at_kill"] == 3
    # And the checkpoint stops claiming to be running.
    assert json.loads((run / "live_state.json").read_text())["status"] == "salvaged"


def test_salvage_uses_recorded_metadata_when_the_checkpoint_has_it(tmp_path):
    arms = [
        {"id": "gordon", "name": "Gordon Flykko", "role_label": "Memory updates ON", "learning": True},
        {"id": "warren", "name": "Warren Buzzett", "role_label": "Memory updates OFF", "learning": False},
    ]
    run = write_session(tmp_path, sample_observations(), arms=arms)
    recording = salvage_run(run)
    assert recording["summary"]["salvaged"]["arm_metadata"] == "recorded in the checkpoint"
    assert recording["arms"][0]["name"] == "Gordon Flykko"


def test_salvage_refuses_to_touch_a_finished_recording(tmp_path):
    run = write_session(tmp_path, sample_observations())
    salvage_run(run)
    with pytest.raises(FileExistsError, match="already has a recording"):
        salvage_run(run)
    # ...unless asked to, which is a deliberate act rather than a silent overwrite.
    assert salvage_run(run, force=True)["summary"]["bars"] == 3


def test_salvage_refuses_a_session_that_never_traded(tmp_path):
    run = write_session(tmp_path, [])
    with pytest.raises(ValueError, match="traded no bars"):
        salvage_run(run)


def test_salvage_counts_blocked_bars_and_keeps_the_last_memory(tmp_path):
    """A halted session is the case salvage exists for, and it must not read as quiet.

    Found by review: `blocked_bars` was hard-coded to 0 and `final_memory` came from the last
    bar rather than the last bar that carried one — so a session halted by its loss stop, where
    every remaining bar is blocked before any observation is taken, was reconstructed as a calm
    run with no memory summary.
    """
    blocked = arm_record(100.0, 0, "0", side="BLOCKED")
    blocked["execution"] = {
        "status": "BLOCKED",
        "reason": "Loss stop reached; holdings remain exposed",
        "fill": None,
    }
    blocked["signal"] = {}  # a blocked bar never reaches an observation
    observed = arm_record(99.99, 0, "0")
    run = write_session(
        tmp_path,
        [observation(0, 77000.0, observed, observed), observation(1, 77010.0, blocked, blocked)],
    )
    summary = salvage_run(run)["summary"]
    for arm in ("gordon", "warren"):
        assert summary["arms"][arm]["blocked_bars"] == 1, arm
        assert summary["arms"][arm]["final_memory"] is not None, arm
        assert summary["arms"][arm]["final_memory"]["changed_edges"] == 7
