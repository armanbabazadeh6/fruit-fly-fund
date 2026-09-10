"""Replay-mode execution: upstream's guard and paper broker on a virtual clock.

Upstream's `Guard` reads `time.time()` for its cooldown, daily-order and quote-freshness
rules because it is built for a live worker that waits in real time. A replay advances
market time explicitly, so the arena injects a virtual clock instead. This is the only
difference: the sizing math, fee accounting, spread and staleness limits all come from
upstream's own `Guard.plan` and `PaperBroker._fill`, and both arms use the same instance
type with the same settings.

`tests/test_execution_parity.py` checks that, for the same quotes and clock, this guard
plans byte-identical orders to upstream's guard.
"""

import time

from stonkfly.risk import Guard, Veto


class VirtualClock:
    """Market time for a replay. Wall time never enters an execution decision."""

    def __init__(self, start: float):
        self.seconds = float(start)

    def set(self, seconds: float):
        self.seconds = float(seconds)

    def advance(self, seconds: float):
        self.seconds += float(seconds)

    def now(self) -> float:
        return self.seconds


class ReplayGuard(Guard):
    def __init__(self, settings, ledger, clock: VirtualClock, stop_file):
        super().__init__(settings, ledger, stop_file)
        self.clock = clock

    def check(self, quotes, now=None):
        return super().check(quotes, self.clock.now())

    def plan(self, product, side, quotes, now=None):
        return super().plan(product, side, quotes, self.clock.now())

    def before_submit(self, plan):
        if self.stop_file.exists() or self.l.get("halted"):
            raise Veto("Execution stopped")
        if not -0.5 <= self.clock.now() - plan["quote_timestamp"] <= self.s.max_quote_age:
            raise Veto("Quote expired before submission")
        pending = self.l.pending()
        if (
            len(pending) != 1
            or pending[0]["id"] != plan["client_order_id"]
            or pending[0]["status"] != "PREPARED"
        ):
            raise Veto("Intent ownership mismatch")


def wall_seconds() -> float:
    """Only used for telemetry, never for a rule."""
    return time.time()
