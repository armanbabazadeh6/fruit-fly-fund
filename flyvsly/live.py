"""Live market: the same ``Season`` interface, with the bars arriving as the exchange closes them.

A recorded season is a fixed window: ``build_season`` fetches it once, every bar is already
in hand, and the arena walks it from bar 0 to the end. A live session is the same walk with
two differences: the window grows by one bar per minute, and the bar that is still forming on
the exchange must never be visible to a fly.

That second rule is the whole point of this module. The public candle endpoint returns the
bucket that is currently open, and its close price moves until the minute is up. A fly that
read it would be reading the future of its own bar, so :func:`completed` refuses any bucket
whose period has not ended (plus a small grace, because "has the exchange stopped editing this
bucket" and "is the clock past it" are not the same instant).

Holding a forming bar out costs the session a wait, and that wait comes out of the bar's own
minute — so it is budgeted rather than tolerated. See the constants below for the arithmetic,
and :attr:`CandleFeed.poll_seconds` for the half of it the feed controls: a poll that lands on
the boundary instead of on a grid.

Paper only, and no credentials of any kind: this reads the same public endpoint
``build_season`` already uses. Nothing here places an order or can place one.
"""

import dataclasses
import time
from decimal import Decimal

from .market import Season, _candles, _iso, kraken_candles

# What one bar costs, on the host these sessions run on. The bar interval is upstream's
# (`interval_seconds: 60`); the observation is this host's measured 500 ms neural observation
# (`flyvsly doctor --measure`), and the two arms decide concurrently, so a bar costs about one:
#
#     60 s  bar interval
#    - 8 s  one observation
#    ------
#     52 s  left for picking the bar up, recording it and drawing breath
#
# Waiting for the exchange to hand over a bar it has already closed is therefore pure loss: it
# comes straight off that 52 s. The wait is `COMPLETION_GRACE_SECONDS` plus however long the
# caller idles before the next poll plus the fetch, and at a 10 s grace on a uniform 15 s poll
# grid that came to 10-24 s depending on where the grid happened to fall (17 s on average) —
# most of the 21.1 s `lag_seconds` recorded at the end of runs/20260911-180303-live.
#
# A bucket is only trusted once the clock is past its end plus this much slack. The slack pays
# for the distance between our clock crossing the boundary and the exchange having finished
# with the bucket: host-versus-venue clock skew plus the endpoint's own write latency for the
# final tick. Read-only sampling of the live endpoint across minute boundaries times that
# directly: over six boundaries the venue's next bucket came back 0.87-1.65 s after our clock
# entered it, and the close of the bucket that just ended never moved once its successor was
# there. So the venue's handover, clock skew included, is under two seconds; 3 s leaves a margin
# on top of the worst observed minute while still spending a fraction of the budget on waiting.
COMPLETION_GRACE_SECONDS = 3

# How long to wait before looking again when a bar is already due but the endpoint has not
# published it. That is the one moment the caller is waiting on the venue rather than on the
# clock, and the venue's handover normally lands well inside the grace above, so a bar still
# missing at the due instant means a slow minute rather than an impatient caller. A short retry
# catches it; asking much faster is pointless, and Kraken answers a sub-second cadence with
# `EGeneral:Too many requests`.
OVERDUE_RETRY_SECONDS = 2.0

# How stale the newest closed bar may be when a session starts, counted in bars. A venue that is
# trading a product closes a bar every `bar_seconds`, so the newest completed one is normally
# under one bar old (plus the grace above). Five bars is a few minutes of silence: long enough
# that a slow minute or a paused market still starts, short enough that a venue which has really
# stopped is refused while someone is still watching — and nothing like the venue that caused
# this, where Coinbase Exchange's rows for every pair this project allows end 2022-07-13, so a
# session started there primes on four-year-old history, records `session_opened` in 2022, and
# then trades nothing, forever, with no error to show for it.
STALE_WINDOW_BARS = 5

# The public endpoint returns at most 300 buckets per request.
PAGE_ROWS = 300

# What a live session is taken to have run when its own checkpoint does not say. A checkpoint
# written by an older build recorded the window and nothing else, and both readers of one — a
# salvage, which finishes it, and a resume, which continues it — have to rebuild the session
# rather than refuse it. Read as a guess: both report that the metadata was rebuilt.
LIVE_DEFAULTS = {
    "engine": "neural",
    "kind": "competition",
    "product": "BTC-USDC",
    "venue": "kraken",
    "bar_seconds": 60,
    "warmup_bars": 120,
}


def observed_bars(observations) -> list[tuple[int, float]]:
    """The ``(timestamp, mid)`` of every bar a session traded, from its own log, oldest first.

    The one reading of an observation's price, shared by a rebuilt recording and by a
    continuation so the two cannot disagree about what the session saw. The mid is what the
    flies' quotes were built around; a log old enough to carry only the book falls back to the
    bid/ask it does have.
    """
    bars = []
    for observation in observations:
        market = observation.get("market") or {}
        mid = market.get("mid")
        if mid is None:
            bid, ask = Decimal(str(market.get("bid", 0))), Decimal(str(market.get("ask", 0)))
            mid = float((bid + ask) / 2)
        bars.append((int(observation["t"]), float(mid)))
    return bars


def completed(rows, bar_seconds: int, now: float) -> list[tuple[int, float]]:
    """Completed ``(timestamp, close)`` bars from a candle response, oldest first.

    ``rows`` is the endpoint's payload: ``[t, low, high, open, close, volume]``, newest
    first, timestamps at the bucket's open. The newest row is normally the bucket the
    exchange is still filling, and it is dropped rather than guessed at.
    """
    bars = []
    for row in rows:
        opened = int(row[0])
        if opened + bar_seconds + COMPLETION_GRACE_SECONDS > now:
            continue
        bars.append((opened, float(row[4])))
    bars.sort()
    # The endpoint can repeat a bucket across pages; one close per open time.
    deduped: list[tuple[int, float]] = []
    for bar in bars:
        if deduped and deduped[-1][0] == bar[0]:
            continue
        deduped.append(bar)
    return deduped


@dataclasses.dataclass
class LiveSeason:
    """A ``Season`` whose tradable bars are appended as the exchange completes them.

    ``closes``/``times`` begin with ``warmup_bars`` completed bars, which is the chart the
    fly sees on its first decision. ``bars`` is the number of tradable bars *so far*, so a
    caller that re-reads it each loop gets one more bar exactly when the feed appends one.
    """

    spec: object
    closes: list[float]
    times: list[int]
    provenance: dict
    warmup_bars: int

    def __post_init__(self) -> None:
        if self.warmup_bars < 1:
            raise ValueError("a live season needs at least one warm-up bar")
        if len(self.closes) != len(self.times):
            raise ValueError("closes and times must line up one for one")
        if any(later <= earlier for earlier, later in zip(self.times, self.times[1:])):
            # Out-of-order bars would put a later close inside an earlier bar's chart, which
            # is the one thing this module exists to prevent.
            raise ValueError("timestamps must be strictly ascending")
        self.closes = [float(c) for c in self.closes]
        self.times = [int(t) for t in self.times]
        self.provenance = dict(self.provenance)
        self._rebuild()

    def _rebuild(self) -> None:
        """Re-point the frozen Season view at the current window.

        ``Season`` is frozen and its ``bars`` comes from the spec, so the growing window is
        expressed as a spec with the tradable count filled in. Everything else — the quote
        arithmetic, the 120-bar history slice, the timestamp accessor — stays exactly the
        arithmetic a recorded season uses, which is what keeps live and replay comparable.

        ``bars`` may be 0, which is the state a session starts in: the warm-up is the chart,
        and no bar is tradable until the exchange closes one.
        """
        self._view = Season(
            dataclasses.replace(self.spec, bars=self.bars), self.closes, self.times, self.provenance
        )

    @property
    def bars(self) -> int:
        return len(self.closes) - self.warmup_bars

    @property
    def seed_bars(self) -> int:
        return self.warmup_bars

    @property
    def window(self) -> Season:
        """The chart so far as a fixed ``Season``: exactly the market a replay is run over.

        The growing season is already a ``Season`` underneath (see :meth:`_rebuild`), and
        handing that object out is what lets a replay read *this* market rather than rebuild
        something that merely resembles it — the same closes, the same timestamps, the same
        quote arithmetic. Nothing is copied, so the two cannot drift apart.
        """
        return self._view

    @property
    def next_bar_opened(self) -> int:
        """When the next tradable bar opens.

        This is the market clock a live session starts its accounts on: before the first
        close there is no bar 0 to read a timestamp from, but the next one is not a guess.
        """
        return self.times[-1] + int(self.spec.bar_seconds)

    def advance(self, bars) -> int:
        """Append completed bars this season has not seen. Returns how many were added.

        Only bars that opened at or after the session's own start are eligible. A live season
        begins from a warm-up window the exchange already had, so a response that still
        contains older bars — it always does, the endpoint returns a page — must not push the
        session backwards into minutes that had already closed before it opened.
        """
        start = self.next_bar_opened
        known = set(self.times)
        fresh = [
            (int(t), float(close))
            for t, close in sorted(bars)
            if int(t) >= start and int(t) not in known
        ]
        for opened, close in fresh:
            self.times.append(opened)
            self.closes.append(close)
        if fresh:
            self._rebuild()
        return len(fresh)

    def describe(self) -> str:
        return f"{self.spec.describe()} · live, {self.bars} completed bars"

    def mid(self, i: int) -> float:
        return self._view.mid(i)

    def timestamp(self, i: int) -> int:
        return self._view.timestamp(i)

    def quote(self, i: int, product: str | None = None):
        return self._view.quote(i, product)

    def history(self, i: int) -> list[float]:
        return self._view.history(i)


class CandleFeed:
    """Polls the public candle endpoint and appends newly completed bars to a LiveSeason.

    Polling rather than a websocket, deliberately: a one-minute decision cadence needs one
    authoritative close per minute, the REST endpoint already carries it, and a poll has no
    connection state to get wrong. ``fetch``/``clock`` are injectable so the feed can be
    tested without a network or a wall clock.

    ``poll_seconds`` is a ceiling on the wait between polls, not a fixed cadence. A caller
    idles for whatever :attr:`poll_seconds` says, which is the interval while the next close is
    further away than that and exactly the time left until the close once it is nearer. A fixed
    grid would leave a bar that just closed sitting untraded for up to a whole interval, which
    is most of what the session's wait used to be; leaning in means the next look lands on the
    boundary instead of after it.
    """

    def __init__(
        self,
        spec,
        warmup_bars: int = 120,
        poll_seconds: float = 15.0,
        fetch=None,
        clock=time.time,
        venue: str = "kraken",
    ):
        self.spec = spec
        self.warmup_bars = int(warmup_bars)
        self.poll_seconds = float(poll_seconds)
        # Kraken by default: every pair upstream allows is delisted on Coinbase Exchange, where
        # a live session would wait forever for a bar that never closes.
        self.venue = venue
        if fetch is not None:
            self.fetch = fetch
        elif venue == "kraken":
            self.fetch = lambda: kraken_candles(spec.product, spec.bar_seconds)
        else:
            self.fetch = lambda: _candles(spec.product, spec.bar_seconds)
        self.clock = clock
        self.season: LiveSeason | None = None
        self.last_poll = 0.0
        self.polls = 0

    @property
    def poll_seconds(self) -> float:
        """How long the caller should idle before polling again, given the clock now.

        The feed knows exactly when the season's next bar will be tradable — its open is the
        season's own clock, its end is a bar interval later, and the grace above is the rest —
        so it never has to ask the endpoint whether the bar is ready yet. Until that instant is
        nearer than the ceiling this is just the ceiling; in the last stretch it is the time
        left, so the waiter wakes on the boundary rather than up to an interval after it. Once
        the instant has passed and the bar still is not here, the endpoint is late rather than
        the caller impatient, and the answer is the short retry.
        """
        ceiling = self._poll_seconds
        if self.season is None:
            return ceiling
        due = self.season.next_bar_opened + int(self.spec.bar_seconds) + COMPLETION_GRACE_SECONDS
        wait = due - self.clock()
        return min(ceiling, wait if wait > 0 else OVERDUE_RETRY_SECONDS)

    @poll_seconds.setter
    def poll_seconds(self, seconds: float) -> None:
        self._poll_seconds = float(seconds)

    def prime(self) -> LiveSeason:
        """Start the session: the newest ``warmup_bars`` completed bars, nothing tradable yet.

        Refuses a window whose bars have stopped closing instead of starting on one: a venue
        that is silent and a market that is quiet look identical from here, and a session that
        primes on old history and then trades nothing, forever, is the one outcome nobody can
        audit. A short gap is still allowed — the session catches up on the bars that closed
        while it was not looking, in market order, like any other backlog.
        """
        rows = self.fetch()
        now = self.clock()
        bars = completed(rows, self.spec.bar_seconds, now)
        if len(bars) < self.warmup_bars:
            raise RuntimeError(
                f"the exchange returned {len(bars)} completed {self.spec.product} bars, "
                f"but this session needs {self.warmup_bars} of warm-up"
            )
        self._refuse_a_venue_that_stopped(bars, now)
        window = bars[-self.warmup_bars :]
        self.season = LiveSeason(
            self.spec,
            [close for _, close in window],
            [opened for opened, _ in window],
            {
                "source": f"{self.venue}-public-candles",
                "venue": self.venue,
                "mode": "live",
                "product": self.spec.product,
                "bar_seconds": self.spec.bar_seconds,
                "warmup_bars": self.warmup_bars,
                "session_started": int(self.clock()),
                # Stated on the recording, not inferred by a reader: the session began now,
                # so the warm-up is history and everything traded after it was unknown then.
                "disclaimer": "Live public candles. Paper execution only; no order is placed.",
            },
            self.warmup_bars,
        )
        self.last_poll = self.clock()
        self.polls = 1
        return self.season

    def resume(self, observations) -> LiveSeason:
        """Continue a session from the bars it already traded, rather than starting over.

        A killed session did not write down the chart it was shown, so the window is rebuilt
        from the two things that did survive: the bars in its own log, and the bars the venue
        has closed — the newest ``warmup_bars`` of them that ended before the session's first
        traded bar, which is the warm-up it was primed with. Where the two could disagree the
        log wins, because those mids are the ones the flies actually decided on: substituting
        the venue's close for an observed mid would make a resumed decision incomparable with
        the one recorded for the same bar.

        The window ends at the last bar in the log, so nothing already traded can be traded
        again: ``advance`` refuses a timestamp the season holds, and the caller counts its bars
        from the log rather than from zero. Every bar the venue has closed since is new, and is
        traded in market order like any other backlog — including bars that closed while the
        session was down and the venue still has. A gap the venue can no longer reach (Kraken
        keeps a page of history, not a memory) stays a gap: the recording's bar list is the
        truth about which bars this session traded, and it is not backfilled with bars nobody
        could have seen.
        """
        if self.season is not None:
            raise RuntimeError("this feed already has a season; resume() needs a fresh feed")
        rows = self.fetch()
        now = self.clock()
        closed = completed(rows, self.spec.bar_seconds, now)
        self._refuse_a_venue_that_stopped(closed, now)
        observed = observed_bars(observations)
        first = observed[0][0] if observed else None
        # `first` is None only for a session killed before its first bar, and then the whole
        # page is the warm-up it never got to trade behind.
        earlier = [bar for bar in closed if first is None or bar[0] < first]
        prefix = earlier[-self.warmup_bars :]
        if prefix:
            warmup = (
                f"the {len(prefix)} bars {self.venue} closed before this session's first "
                "traded bar, rebuilt from the venue's own history"
            )
        else:
            # Older than the venue's page of history. The mid is real and the timestamp is one
            # bar before the session's first traded bar; the provenance says so rather than
            # leaving a reader to work out where the fly's chart came from.
            prefix = [(first - self.spec.bar_seconds, observed[0][1])]
            warmup = (
                f"one placeholder bar stands in for the warm-up: {self.venue}'s history no "
                "longer reaches back to before this session's first traded bar"
            )
        self.season = LiveSeason(
            self.spec,
            [close for _, close in prefix] + [close for _, close in observed],
            [opened for opened, _ in prefix] + [opened for opened, _ in observed],
            {
                "source": f"{self.venue}-public-candles",
                "venue": self.venue,
                "mode": "live",
                "product": self.spec.product,
                "bar_seconds": self.spec.bar_seconds,
                "warmup_bars": len(prefix),
                "resumed_at": int(now),
                "resumed_bars": len(observed),
                "warmup": warmup,
                "disclaimer": "Live public candles. Paper execution only; no order is placed.",
            },
            len(prefix),
        )
        self.last_poll = now
        self.polls += 1
        return self.season

    def _refuse_a_venue_that_stopped(self, bars, now) -> None:
        """Refuse a venue that has stopped closing bars, rather than trade a dead market.

        Shared by a session that is starting and one that is continuing: both are about to
        decide on the next bar this venue closes, and a venue that has stopped closing them
        looks exactly like a quiet market until the recording is read months later.
        """
        if not bars:
            raise RuntimeError(
                f"{self.venue} returned no completed {self.spec.product} bars; there is no "
                "market to decide on"
            )
        newest, _ = bars[-1]
        stale = now - (newest + self.spec.bar_seconds)
        if stale > STALE_WINDOW_BARS * self.spec.bar_seconds:
            # The message carries the whole diagnosis — the venue, the product, the newest bar
            # it did close and how stale that is — because the operator's next step is there.
            raise RuntimeError(
                f"{self.venue} is not trading {self.spec.product}: the newest completed bar is "
                f"{_iso(newest)} and it ended {stale:.0f} s ago, more than the "
                f"{STALE_WINDOW_BARS} bars ({STALE_WINDOW_BARS * self.spec.bar_seconds} s) this "
                f"session will accept. Check that {self.venue} still lists "
                f"{self.spec.product}, or point the session at a venue that trades it."
            )

    def poll(self) -> int:
        """Append every bar completed since the last poll. Returns the number appended."""
        if self.season is None:
            raise RuntimeError("prime() the feed before polling it")
        now = self.clock()
        self.last_poll = now
        self.polls += 1
        return self.season.advance(completed(self.fetch(), self.spec.bar_seconds, now))
