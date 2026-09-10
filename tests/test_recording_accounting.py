"""The scoreboard numbers must survive their own arithmetic.

Every published recording is re-derived here from the trades it contains: cash and position
movements, fees at the configured rate, fills at the observed side of the book, and marked-
to-bid equity at each bar. If a recording and its summary ever disagree, the browser is
showing a number no ledger supports.

This runs against the recordings committed in `web/public/recordings/`, so it also guards
those published artifacts against corruption.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

RECORDINGS = sorted(Path("web/public/recordings").glob("*.json"))
PUBLISHED = [path for path in RECORDINGS if path.name not in ("index.json", "report.json")]

pytestmark = pytest.mark.skipif(
    not PUBLISHED, reason="no published recordings; run `flyvsly publish` first"
)

CENT = Decimal("0.00000001")


def load(path):
    return json.loads(path.read_text())


def bars_by_index(recording):
    return {index: bar for index, bar in enumerate(recording["season"]["bars"])}


@pytest.mark.parametrize("path", PUBLISHED, ids=lambda p: p.stem)
def test_trades_replay_to_the_recorded_final_equity(path):
    recording = load(path)
    bars = bars_by_index(recording)
    capital = Decimal(recording["summary"]["initial_capital"])
    fee_rate = Decimal(str(recording["run"]["rules"]["paper_fee"]))

    for arm in recording["arms"]:
        summary = recording["summary"]["arms"][arm["id"]]
        cash, position, fees = capital, Decimal(0), Decimal(0)

        for trade in summary["trades"]:
            size = Decimal(trade["base_size"])
            notional = Decimal(trade["quote_size"])
            fee = Decimal(trade["fee"])
            bar = bars[trade["i"]]

            expected_price = Decimal(bar["ask"] if trade["side"] == "BUY" else bar["bid"])
            assert Decimal(trade["price"]) == expected_price, (
                f"{arm['id']} bar {trade['i']}: filled at {trade['price']} but the book "
                f"showed {expected_price} for a {trade['side']}"
            )
            assert fee == notional * fee_rate, f"{arm['id']} bar {trade['i']}: wrong fee"
            assert notional == size * expected_price

            if trade["side"] == "BUY":
                cash -= notional + fee
                position += size
            else:
                assert position >= size, f"{arm['id']} bar {trade['i']}: sold more than held"
                cash += notional - fee
                position -= size
            fees += fee

        assert position >= 0 and cash >= 0, f"{arm['id']} ended with a negative balance"
        last_bid = Decimal(recording["season"]["bars"][-1]["bid"])
        exact = Decimal(
            recording["observations"][-1]["arms"][arm["id"]]["portfolio"]["equity"]
        )
        # The ledger is exact: replaying every fill reproduces the final marked-to-bid
        # equity to the last decimal place.
        assert cash + position * last_bid == pytest.approx(exact, abs=CENT)
        # `final_equity` in the summary is the same number rounded for display.
        assert abs(Decimal(str(summary["final_equity"])) - exact) <= Decimal("0.0000005")
        assert Decimal(summary["fees_paid"]) == fees
        assert summary["fills"] == len(summary["trades"])


@pytest.mark.parametrize("path", PUBLISHED, ids=lambda p: p.stem)
def test_each_bar_marks_the_book_the_way_the_ledger_does(path):
    """Per-bar equity must equal cash plus holdings valued at that bar's bid."""
    recording = load(path)
    bars = bars_by_index(recording)
    for observation in recording["observations"]:
        for arm_id, arm in observation["arms"].items():
            portfolio = arm["portfolio"]
            value = Decimal(portfolio["cash"])
            for size in portfolio["positions"].values():
                value += Decimal(size) * Decimal(bars[observation["i"]]["bid"])
            assert value == pytest.approx(Decimal(portfolio["equity"]), abs=CENT), (
                f"{arm_id} bar {observation['i']}: equity {portfolio['equity']} does not "
                f"match its own cash and positions"
            )


@pytest.mark.parametrize("path", PUBLISHED, ids=lambda p: p.stem)
def test_summary_curves_match_the_per_bar_ledger(path):
    recording = load(path)
    for arm in recording["arms"]:
        summary = recording["summary"]["arms"][arm["id"]]
        from_observations = [
            float(observation["arms"][arm["id"]]["portfolio"]["equity"])
            for observation in recording["observations"]
        ]
        assert len(summary["curve"]) == len(from_observations) == recording["summary"]["bars"]
        for recorded, derived in zip(summary["curve"], from_observations):
            assert recorded == pytest.approx(derived, abs=1e-6)


@pytest.mark.parametrize("path", PUBLISHED, ids=lambda p: p.stem)
def test_the_control_arm_never_changed_a_synapse(path):
    """Published neural runs must show a genuinely frozen control."""
    recording = load(path)
    if recording["run"]["engine"] != "neural":
        pytest.skip("procedural recordings have no synapses")
    on = recording["summary"]["arms"]["gordon"]
    off = recording["summary"]["arms"]["warren"]
    assert off["final_memory"]["enabled"] is False
    assert off["final_memory"]["changed_edges"] == 0
    assert on["final_memory"]["changed_edges"] > 0
    assert recording["run"]["inputs_identical_every_bar"] is True
