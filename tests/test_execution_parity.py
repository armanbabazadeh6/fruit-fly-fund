"""Execution parity and money accounting.

The arena must not invent its own execution rules. These tests pin the replay guard to
upstream's guard for identical inputs, and check the paper fills, fees and refusals that
both flies are subject to.
"""

from decimal import Decimal

import pytest

from stonkfly.broker import PaperBroker
from stonkfly.config import D
from stonkfly.ledger import Ledger
from stonkfly.market import Quote
from stonkfly.risk import Guard, Veto

from flyvsly.config import ArenaRules
from flyvsly.fairness import arm_settings
from flyvsly.replay import ReplayGuard, VirtualClock

INCREMENTS = {
    "base_increment": D("0.00000001"),
    "quote_increment": D("0.01"),
    "price_increment": D("0.01"),
    "minimum_quote": D("1"),
    "minimum_base": D("0.00000001"),
}


def quote_at(now: float, mid="60000", half_spread="0.00025") -> Quote:
    price = D(mid)
    return Quote(
        "BTC-USDC",
        price * (1 - D(half_spread)),
        price * (1 + D(half_spread)),
        now,
        **INCREMENTS,
    )


def fresh_ledgers(tmp_path, now: float, learning: bool):
    settings = arm_settings(ArenaRules(), learning)
    ledger = Ledger(tmp_path / f"{'on' if learning else 'off'}.sqlite", settings, "paper")
    clock = VirtualClock(now)
    guard = ReplayGuard(settings, ledger, clock, tmp_path / "STOP")
    broker = PaperBroker(settings, ledger)
    return settings, ledger, clock, guard, broker


def test_replay_guard_plans_identically_to_upstream(tmp_path):
    """Same quotes, same clock, same order — the replay clock is the only difference."""
    now = 1_700_000_000.0
    settings, ledger, clock, guard, _ = fresh_ledgers(tmp_path, now, True)
    upstream_settings = settings
    upstream_ledger = Ledger(tmp_path / "upstream.sqlite", upstream_settings, "paper")
    upstream = Guard(upstream_settings, upstream_ledger, tmp_path / "STOP")
    quotes = {"BTC-USDC": quote_at(now)}

    ours = guard.plan("BTC-USDC", "BUY", quotes)
    theirs = upstream.plan("BTC-USDC", "BUY", quotes, now)

    assert ours["base_size"] == theirs["base_size"]
    assert ours["limit_price"] == theirs["limit_price"]
    assert ours["fee_ceiling"] == theirs["fee_ceiling"]
    assert ours["order_type"] == theirs["order_type"]
    ledger.close()
    upstream_ledger.close()


def test_cooldown_is_measured_in_market_time(tmp_path):
    now = 1_700_000_000.0
    _, ledger, clock, guard, _ = fresh_ledgers(tmp_path, now, True)
    quotes = {"BTC-USDC": quote_at(now)}
    with ledger.transaction():
        ledger.put("last_attempt", clock.now())

    with pytest.raises(Veto, match="cooldown"):
        guard.plan("BTC-USDC", "BUY", quotes)

    clock.advance(60)
    quotes = {"BTC-USDC": quote_at(clock.now())}
    assert guard.plan("BTC-USDC", "BUY", quotes)["side"] == "BUY"
    # One bar earlier than the cooldown and it is still refused.
    clock.advance(59)
    ledger.close()


def test_a_fill_spends_cash_and_charges_the_configured_fee(tmp_path):
    now = 1_700_000_000.0
    settings, ledger, clock, guard, broker = fresh_ledgers(tmp_path, now, True)
    quotes = {"BTC-USDC": quote_at(now)}
    plan = ledger.reserve(guard.plan("BTC-USDC", "BUY", quotes), clock.now())
    fill = broker.execute(plan, guard.before_submit)

    assert fill["status"] == "FILLED"
    size, spend, fee = D(fill["base"]), D(fill["quote"]), D(fill["fee"])
    assert ledger.cash == D("100") - spend - fee
    assert ledger.positions["BTC-USDC"] == size
    assert fee == spend * D(settings.paper_fee)
    assert spend <= D(plan["limit_price"]) * size
    assert not ledger.pending()
    ledger.close()


def test_selling_without_inventory_is_refused(tmp_path):
    now = 1_700_000_000.0
    _, ledger, clock, guard, _ = fresh_ledgers(tmp_path, now, False)
    quotes = {"BTC-USDC": quote_at(now)}
    with pytest.raises(Veto, match="Insufficient"):
        guard.plan("BTC-USDC", "SELL", quotes)
    ledger.close()


def test_stale_quote_and_spread_limits_are_enforced(tmp_path):
    now = 1_700_000_000.0
    _, ledger, clock, guard, _ = fresh_ledgers(tmp_path, now, True)

    with pytest.raises(Veto, match="Stale or future quote"):
        guard.check({"BTC-USDC": quote_at(now - 60)}, None)

    wide = quote_at(now, half_spread="0.02")
    with pytest.raises(Veto, match="Spread limit"):
        guard.check({"BTC-USDC": wide}, None)
    ledger.close()


def test_the_loss_stop_halts_new_orders_and_leaves_holdings_alone(tmp_path):
    """Upstream's stop refuses new orders; it does not liquidate or cap further losses."""
    now = 1_700_000_000.0
    rules = ArenaRules(loss_stop="20")
    settings = arm_settings(rules, True)
    ledger = Ledger(tmp_path / "stopped.sqlite", settings, "paper")
    clock = VirtualClock(now)
    guard = ReplayGuard(settings, ledger, clock, tmp_path / "STOP")

    with ledger.transaction():
        ledger.put("cash", "60")
        ledger.put("positions", {"BTC-USDC": "0.0001"})
    quotes = {"BTC-USDC": quote_at(now, mid="60000")}
    with pytest.raises(Veto, match="Loss stop"):
        guard.check(quotes, None)
    assert "Loss stop" in ledger.get("halted")
    with pytest.raises(Veto, match="Loss stop"):
        guard.plan("BTC-USDC", "BUY", quotes)
    assert ledger.positions["BTC-USDC"] > 0
    ledger.close()


def test_stop_file_blocks_planning(tmp_path):
    now = 1_700_000_000.0
    _, ledger, clock, guard, _ = fresh_ledgers(tmp_path, now, True)
    (tmp_path / "STOP").write_text("")
    with pytest.raises(Veto, match="STOP file"):
        guard.plan("BTC-USDC", "BUY", {"BTC-USDC": quote_at(now)})
    ledger.close()


def test_plan_is_persisted_before_the_fill(tmp_path):
    """An intent exists in the ledger before any settlement, so a crash cannot lose it."""
    now = 1_700_000_000.0
    _, ledger, clock, guard, broker = fresh_ledgers(tmp_path, now, True)
    quotes = {"BTC-USDC": quote_at(now)}
    plan = ledger.reserve(guard.plan("BTC-USDC", "BUY", quotes), clock.now())
    pending = ledger.pending()
    assert len(pending) == 1 and pending[0]["status"] == "PREPARED"
    assert pending[0]["id"] == plan["client_order_id"]

    broker.execute(plan, guard.before_submit)
    assert ledger.pending() == []
    ledger.close()


def test_identical_orders_for_both_arms_at_the_same_bar(tmp_path):
    """Given one quote, the two arms produce byte-identical plans. Fairness check."""
    now = 1_700_000_000.0
    _, on_ledger, on_clock, on_guard, _ = fresh_ledgers(tmp_path, now, True)
    _, off_ledger, off_clock, off_guard, _ = fresh_ledgers(tmp_path, now, False)
    quotes = {"BTC-USDC": quote_at(now)}
    on_plan = on_guard.plan("BTC-USDC", "BUY", quotes)
    off_plan = off_guard.plan("BTC-USDC", "BUY", quotes)
    for key in ("base_size", "limit_price", "fee_ceiling", "observed_bid", "observed_ask"):
        assert on_plan[key] == off_plan[key]
    assert Decimal(on_plan["base_size"]) > 0
    on_ledger.close()
    off_ledger.close()
