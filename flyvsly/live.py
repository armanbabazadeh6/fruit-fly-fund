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

Paper only, and no credentials of any kind: this reads the same public endpoint
``build_season`` already uses. Nothing here places an order or can place one.
"""

import dataclasses
import time

from .market import Season, _candles

# A bucket is only trusted once the clock is past its end plus this much slack.
COMPLETION_GRACE_SECONDS = 10

# The public endpoint returns at most 300 buckets per request.
PAGE_ROWS = 300


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
    """

    def __init__(
        self,
        spec,
        warmup_bars: int = 120,
        poll_seconds: float = 15.0,
        fetch=None,
        clock=time.time,
    ):
        self.spec = spec
        self.warmup_bars = int(warmup_bars)
        self.poll_seconds = float(poll_seconds)
        self.fetch = fetch or (lambda: _candles(spec.product, spec.bar_seconds))
        self.clock = clock
        self.season: LiveSeason | None = None
        self.last_poll = 0.0
        self.polls = 0

    def prime(self) -> LiveSeason:
        """Start the session: the newest ``warmup_bars`` completed bars, nothing tradable yet."""
        bars = completed(self.fetch(), self.spec.bar_seconds, self.clock())
        if len(bars) < self.warmup_bars:
            raise RuntimeError(
                f"the exchange returned {len(bars)} completed {self.spec.product} bars, "
                f"but this session needs {self.warmup_bars} of warm-up"
            )
        window = bars[-self.warmup_bars :]
        self.season = LiveSeason(
            self.spec,
            [close for _, close in window],
            [opened for opened, _ in window],
            {
                "source": "coinbase-public-candles",
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

    def poll(self) -> int:
        """Append every bar completed since the last poll. Returns the number appended."""
        if self.season is None:
            raise RuntimeError("prime() the feed before polling it")
        now = self.clock()
        self.last_poll = now
        self.polls += 1
        return self.season.advance(completed(self.fetch(), self.spec.bar_seconds, now))
