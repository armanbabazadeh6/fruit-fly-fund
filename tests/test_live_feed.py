"""Live feed: the bars a real session trades, and the one rule that matters — no future bars.

These tests use no network and no wall clock. The exchange response and the clock are both
injected, because the failure this module exists to prevent (a fly reading a bucket the
exchange is still filling) is exactly the case a live test would be least likely to catch.
"""

import pytest

from flyvsly.config import MarketSpec
from flyvsly.live import COMPLETION_GRACE_SECONDS, CandleFeed, LiveSeason, completed

# 2026-09-11T14:00:00Z, an arbitrary but fixed instant.
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


def test_feed_refuses_to_start_without_enough_history():
    source = Source(page(2, EPOCH - 120))
    feed = CandleFeed(SPEC, warmup_bars=3, poll_seconds=0, fetch=source, clock=Clock(EPOCH + 600))
    with pytest.raises(RuntimeError, match="completed BTC-USDC bars"):
        feed.prime()
