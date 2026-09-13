"""Live feed: the bars a real session trades, and the one rule that matters — no future bars.

These tests use no network and no wall clock. The exchange response and the clock are both
injected, because the failure this module exists to prevent (a fly reading a bucket the
exchange is still filling) is exactly the case a live test would be least likely to catch.
"""

import json

import pytest

from flyvsly.config import MarketSpec
from flyvsly.live import (
    COMPLETION_GRACE_SECONDS,
    OVERDUE_RETRY_SECONDS,
    STALE_WINDOW_BARS,
    CandleFeed,
    LiveSeason,
    completed,
)

# 2026-09-11T14:40:00Z, an arbitrary but fixed instant.
EPOCH = 1789137600
SPEC = MarketSpec(kind="coinbase", product="BTC-USDC", bars=48, bar_seconds=60)


def row(opened: int, close: float):
    """One candle payload row, in the endpoint's own shape."""
    return [opened, close - 1, close + 1, close, close, 0.5]


def page(count: int, first_opened: int, start_close: float = 100.0, step: float = 1.0):
    """`count` consecutive completed minutes, newest first, as the endpoint returns them."""
    rows = [row(first_opened + i * 60, start_close + i * step) for i in range(count)]
    return list(reversed(rows))


def ascending(count: int, first_opened: int, start_close: float = 100.0, step: float = 1.0):
    """The same bars oldest first, which is the order LiveSeason keeps."""
    return list(reversed(page(count, first_opened, start_close, step)))


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Source:
    """A candle endpoint the test edits by hand."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return list(self.rows)


def test_forming_bucket_is_never_traded():
    """The bucket the exchange is still filling is dropped, and the grace period is real."""
    forming = EPOCH
    done = EPOCH - 60
    rows = [row(forming, 200.0), row(done, 100.0)]

    # The clock is inside the forming bucket: only the previous minute is usable.
    assert completed(rows, 60, forming + 30) == [(done, 100.0)]
    # The clock is past the bucket end but inside the grace window: still not trusted.
    assert completed(rows, 60, forming + 60 + COMPLETION_GRACE_SECONDS - 1) == [(done, 100.0)]
    # Past the grace window it is a closed bar.
    assert completed(rows, 60, forming + 60 + COMPLETION_GRACE_SECONDS) == [
        (done, 100.0),
        (forming, 200.0),
    ]


def test_a_bucket_one_second_past_its_end_is_still_refused():
    """One second past the boundary is inside the window: the venue may still be writing it.

    The window has to be wider than a second, or a venue that takes a moment to put the last
    tick of a bucket in place is handing over a close that is still moving.
    """
    opened = EPOCH
    assert completed([row(opened, 100.0)], 60, opened + 60 + 1) == []


def test_the_acceptance_boundary_is_the_end_of_the_grace_window():
    """The gate is `bucket end + grace`, asserted on both sides of that tick, not implied."""
    opened = EPOCH
    end = opened + 60
    just_inside = end + COMPLETION_GRACE_SECONDS - 0.5
    assert completed([row(opened, 100.0)], 60, just_inside) == []
    assert completed([row(opened, 100.0)], 60, end + COMPLETION_GRACE_SECONDS) == [
        (opened, 100.0)
    ]


def test_bars_come_back_oldest_first_and_once_each():
    rows = page(5, EPOCH - 300) + [row(EPOCH - 120, 999.0)]
    bars = completed(rows, 60, EPOCH + 600)
    assert [t for t, _ in bars] == sorted(t for t, _ in bars)
    assert len(bars) == 5
    assert len({t for t, _ in bars}) == 5


def test_missing_exchange_minutes_stay_missing():
    """A gap is the exchange's own record of time; it is not smoothed into a fake bar."""
    rows = [row(EPOCH + 120, 102.0), row(EPOCH, 100.0)]  # the EPOCH+60 minute never traded
    bars = completed(rows, 60, EPOCH + 600)
    assert [t for t, _ in bars] == [EPOCH, EPOCH + 120]


def test_season_starts_with_warmup_and_no_tradable_bars():
    warmup = ascending(5, EPOCH - 300)
    season = LiveSeason(SPEC, [r[4] for r in warmup], [r[0] for r in warmup], {"source": "test"}, 5)
    assert season.bars == 0
    assert season.seed_bars == 5
    # The chart the fly sees on its first decision is the warm-up, and nothing else.
    assert season.history(0) == [100.0, 101.0, 102.0, 103.0, 104.0]


def test_advance_appends_once_and_the_chart_never_runs_ahead():
    warmup = ascending(5, EPOCH - 300)
    season = LiveSeason(
        SPEC,
        [r[4] for r in warmup],
        [r[0] for r in warmup],
        {"source": "test"},
        5,
    )
    assert season.advance([(EPOCH, 500.0)]) == 1
    assert season.bars == 1
    assert season.mid(0) == 500.0
    # Replaying the same completed bar is a no-op, not a second bar.
    assert season.advance([(EPOCH, 500.0)]) == 0
    assert season.bars == 1

    assert season.advance([(EPOCH + 60, 600.0), (EPOCH + 120, 700.0)]) == 2
    assert season.bars == 3
    # Bar i sees bars up to and including itself, never a later close.
    assert season.history(1)[-1] == 600.0
    assert season.history(2)[-1] == 700.0
    assert 700.0 not in season.history(1)
    # The window is the 120-bar chart the replay path uses, filled from the warm-up onward.
    assert len(season.history(0)) == 6


def test_feed_primes_on_warmup_then_hands_over_completed_bars():
    source = Source(page(4, EPOCH - 180))
    # Past the grace window of the EPOCH-60 bucket, so three of the four rows are closed.
    clock = Clock(EPOCH + 15)
    feed = CandleFeed(SPEC, warmup_bars=3, poll_seconds=0, fetch=source, clock=clock)

    season = feed.prime()
    # The newest completed bar (still inside its grace window) is not counted yet.
    assert season.bars == 0
    assert source.calls == 1

    # A minute closes on the exchange; the feed turns it into exactly one tradable bar.
    clock.now = EPOCH + 60 + COMPLETION_GRACE_SECONDS + 1
    source.rows = page(5, EPOCH - 180)
    assert feed.poll() == 1
    assert season.bars == 1
    assert season.timestamp(0) == EPOCH
    # The warm-up is still the fly's chart, and it now ends one bar back from the close.
    assert season.history(0)[-1] == season.mid(0)


def test_the_feed_hands_the_bar_over_on_the_first_look_after_its_window():
    """A closed bar waits for the grace and the fetch, not for the next poll interval."""
    source = Source(page(4, EPOCH - 180))
    clock = Clock(EPOCH + 15)
    feed = CandleFeed(SPEC, warmup_bars=3, poll_seconds=15, fetch=source, clock=clock)
    season = feed.prime()
    assert season.bars == 0

    end = EPOCH + 60
    clock.now = end + COMPLETION_GRACE_SECONDS - 0.5
    assert feed.poll() == 0
    clock.now = end + COMPLETION_GRACE_SECONDS
    assert feed.poll() == 1
    assert season.timestamp(0) == EPOCH


def test_the_wait_before_the_next_poll_leans_in_to_the_boundary():
    """The feed answers how long the next look may wait, so it lands on the close itself."""
    source = Source(page(4, EPOCH - 180))
    clock = Clock(EPOCH + 15)
    feed = CandleFeed(SPEC, warmup_bars=3, poll_seconds=15, fetch=source, clock=clock)
    feed.prime()

    # The bar that opens at EPOCH is trusted at EPOCH + 60 + grace: 48 s away, so the full
    # interval still fits inside that and the ceiling is the answer.
    due = EPOCH + 60 + COMPLETION_GRACE_SECONDS
    assert feed.poll_seconds == 15
    clock.now = due - 8  # 8 s left: wait exactly that, not a whole interval
    assert feed.poll_seconds == pytest.approx(8)
    clock.now = due + 0.5  # due and still not published: now the venue is late, not us
    assert feed.poll_seconds == OVERDUE_RETRY_SECONDS


def test_the_configured_interval_is_a_ceiling_not_a_fixed_cadence():
    source = Source(page(4, EPOCH - 180))
    clock = Clock(EPOCH + 55)  # 8 s short of the next bar being due
    capped = CandleFeed(SPEC, warmup_bars=3, poll_seconds=3, fetch=source, clock=clock)
    capped.prime()
    assert capped.poll_seconds == 3

    # A caller that asked for no idling gets none, whatever the clock says.
    idle_free = CandleFeed(SPEC, warmup_bars=3, poll_seconds=0, fetch=source, clock=clock)
    idle_free.prime()
    assert idle_free.poll_seconds == 0

    # Before there is a session there is no next bar to aim at, so the ceiling stands alone.
    assert CandleFeed(SPEC, poll_seconds=15, fetch=source, clock=clock).poll_seconds == 15


def test_a_minute_of_waiting_costs_the_grace_and_not_a_poll_interval():
    """Walk the loop's own cadence past a boundary and measure what the fly waited for.

    On the fixed 15 s grid this bar would have been handed over at EPOCH + 75 — the close at
    EPOCH + 60, plus a full interval spent not looking. The wait is the whole of the delay
    after the bucket's end, so it is the number this module hands the arena's budget.
    """
    source = Source(page(4, EPOCH - 180))
    clock = Clock(EPOCH + 15)
    feed = CandleFeed(SPEC, warmup_bars=3, poll_seconds=15, fetch=source, clock=clock)
    feed.prime()

    while clock.now < EPOCH + 300:
        if feed.poll():
            break
        clock.now += feed.poll_seconds

    assert feed.season.timestamp(0) == EPOCH
    assert clock.now - (EPOCH + 60) == pytest.approx(COMPLETION_GRACE_SECONDS)


def test_feed_refuses_to_start_without_enough_history():
    source = Source(page(2, EPOCH - 120))
    feed = CandleFeed(SPEC, warmup_bars=3, poll_seconds=0, fetch=source, clock=Clock(EPOCH + 600))
    with pytest.raises(RuntimeError, match="completed BTC-USDC bars"):
        feed.prime()


def test_feed_refuses_to_start_on_a_venue_that_stopped_closing_bars():
    """A silent venue is named and refused, not idled on until someone notices.

    This is the shape of `--source coinbase`: that venue's rows for every pair upstream allows
    end in 2022, so a session there primes on that history, records `session_opened` in 2022 and
    then adds nothing, forever, without an error to show for it.
    """
    source = Source(page(4, EPOCH - 180))
    clock = Clock(EPOCH + 4 * 365 * 86400)
    feed = CandleFeed(SPEC, warmup_bars=3, poll_seconds=0, fetch=source, clock=clock)
    with pytest.raises(RuntimeError) as caught:
        feed.prime()
    message = str(caught.value)
    assert "kraken" in message  # the venue that went quiet
    assert "BTC-USDC" in message  # and the product it stopped closing
    assert "2026-09-11T14:40:00Z" in message  # the newest bar it did close
    assert "not trading BTC-USDC" in message  # what the operator has to act on
    assert feed.season is None  # a refused session has no season to trade


def test_feed_primes_on_the_stalest_window_it_will_trade_and_no_more():
    """The bound is `STALE_WINDOW_BARS` bars of silence, asserted on both sides of it."""
    newest_ended = EPOCH + 60
    limit = STALE_WINDOW_BARS * 60
    at_the_limit = CandleFeed(
        SPEC, warmup_bars=3, poll_seconds=0, fetch=Source(page(4, EPOCH - 180)),
        clock=Clock(newest_ended + limit),
    )
    assert at_the_limit.prime().bars == 0
    assert at_the_limit.season.timestamp(-1) == EPOCH

    a_second_later = CandleFeed(
        SPEC, warmup_bars=3, poll_seconds=0, fetch=Source(page(4, EPOCH - 180)),
        clock=Clock(newest_ended + limit + 1),
    )
    with pytest.raises(RuntimeError, match="not trading BTC-USDC"):
        a_second_later.prime()


# -- the venue ---------------------------------------------------------------------------
#
# Coinbase Exchange delisted every pair upstream allows (BTC-USDC, ETH-USDC, SOL-USDC all stop
# at 2022-07-13 and answer "Not allowed for delisted products"), so the live feed reads Kraken,
# which still trades them. Same rule applies there: no forming bar is ever traded.


class _Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_kraken_rows_are_normalised_to_the_shared_shape(monkeypatch):
    from flyvsly import market

    payload = {
        "error": [],
        # Kraken: [time, open, high, low, close, vwap, volume, count]
        "result": {"XXBTUSDC": [[EPOCH, "77000.0", "77200.0", "76900.0", "77173.79", "77050.0", "1.5", 42]]},
    }
    monkeypatch.setattr(market.urllib.request, "urlopen", lambda *a, **k: _Response(payload))
    # …and out in the order the rest of the project reads: [t, low, high, open, close, volume]
    assert market.kraken_candles("BTC-USDC", 60) == [
        [EPOCH, 76900.0, 77200.0, 77000.0, 77173.79, 1.5]
    ]


def test_kraken_refuses_a_pair_or_interval_it_cannot_serve():
    from flyvsly import market

    with pytest.raises(ValueError, match="does not list"):
        market.kraken_candles("DOGE-USDC", 60)
    with pytest.raises(ValueError, match="interval"):
        market.kraken_candles("BTC-USDC", 90)


def test_kraken_surfaces_a_venue_error_rather_than_trading_blind(monkeypatch):
    from flyvsly import market

    monkeypatch.setattr(
        market.urllib.request, "urlopen", lambda *a, **k: _Response({"error": ["EQuery:Unknown asset pair"]})
    )
    with pytest.raises(RuntimeError, match="Unknown asset pair"):
        market.kraken_candles("BTC-USDC", 60)


def test_feed_records_which_venue_the_bars_came_from():
    source = Source(page(4, EPOCH - 180))
    clock = Clock(EPOCH + 15)
    feed = CandleFeed(SPEC, warmup_bars=3, poll_seconds=0, fetch=source, clock=clock, venue="kraken")
    season = feed.prime()
    assert season.provenance["source"] == "kraken-public-candles"
    assert season.provenance["venue"] == "kraken"
    assert season.provenance["mode"] == "live"
