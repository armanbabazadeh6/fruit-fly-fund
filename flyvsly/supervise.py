"""Watch a live paper session and restart it when it dies.

A live session is the one thing here that is meant to keep running, and the one thing nothing
watches. On 2026-09-11 a session traded 429 bars over seven hours and then Docker Desktop died
under it; the record survived (that is what `salvage` is for) but the session did not come
back. The gap between "can trade" and "trades for weeks" is not in the trading loop, it is in
what stands over it, so this module is that.

The policy, and why each part is what it is:

* **An expected end is not a failure.** The session exits 0 when it stops cleanly, and the stop
  file is the operator saying "finish". Either one ends the supervisor with exit 0 and no
  restart. The stop file outranks the exit code: whoever asked for the end gets it even if the
  process died on its way out.
* **Anything else is a crash, and is retried.** The failures that actually happen are transient
  — a slow minute at the venue, a prime refused because the newest bar was stale, a container
  killed by the host — not the engine deciding badly. A retry is the cheap response.
* **Backoff doubles from 5 s and stops at 300 s.** A crash loop must not hammer a public candle
  endpoint, and the ceiling is what keeps a session that has been down for an hour from waiting
  an hour before its next try.
* **Five consecutive crashes is a give-up.** That many in a row, without a healthy run between
  them, is a fault a retry cannot fix (a venue that stopped trading, a missing graph, a port
  already bound), and a human should look rather than have the supervisor retry forever. Giving
  up exits non-zero and says so in the log.
* **A healthy run resets the ladder.** A session that traded for ten minutes and then died has
  proved the market, the venue and the graph work; its death is a new event, not the fifth rung
  of a crash loop. Without this, a session that had survived hours would still be abandoned
  after five crashes spread over a week.
* **Nothing is decided quietly.** Every start, exit, restart, give-up and expected stop is one
  JSON Lines record, appended with a UTC timestamp, the attempt number and the exit code. A
  supervisor that silently retries is worse than no supervisor, because it hides the pattern
  that a human needs to see.

What a restart is, and is not. It restarts the *process*, not the position: `flyvsly live`
mints a run id from the clock, so a restarted session is a new run directory with fresh paper
accounts and its own recording. Nothing is lost — the killed session's observations and
checkpoint stay on disk for `flyvsly salvage`, and both are listed in the browser — but the
restart's equity curve is a new $100 account, not a continuation of the one that died.

Paper only, like everything else here: the supervisor launches a session and cannot place an
order.
"""

import argparse
import dataclasses
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

# The retry ladder. The first retry is short because the common crash is a transient one worth
# five seconds; doubling keeps a venue that is having a bad hour from being polled in a tight
# loop; the ceiling bounds how long a recovered venue waits to be noticed.
DEFAULT_BACKOFF_SECONDS = 5.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_BACKOFF_CEILING_SECONDS = 300.0

# Consecutive crashes before the supervisor stops trying and hands the problem to a human.
DEFAULT_MAX_FAILURES = 5

# A run this long is not part of a crash loop, so it clears the ladder. Ten minutes is far past
# the point where a prime, a venue choice and the graph have proved themselves: the 2026-09-11
# session traded 429 bars in seven hours, so a run that reaches this has traded dozens of bars.
DEFAULT_HEALTHY_SECONDS = 600.0

# The supervisor's own exit codes. 1 is the session's "failed" code, so the supervisor's are
# kept apart from it: 2 names the one outcome an operator must be paged about.
EXIT_CLEAN = 0
EXIT_GAVE_UP = 2
EXIT_INTERRUPTED = 130


def utc(stamp: float) -> str:
    """A UTC timestamp for a record, in the form the recordings already use."""
    return (
        datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def launch(argv, session_log=None) -> int:
    """Run the session as a child process and return its exit code.

    With a ``session_log`` the session's own output is appended there, next to the supervisor's
    record, so an unattended run leaves both; without one the child inherits this process's
    stdout and stderr, which is what a foreground operator wants. The child is waited for
    rather than polled: the supervisor's whole job is to act on the moment it exits.
    """
    handle = None
    stdout = stderr = None
    if session_log is not None:
        path = Path(session_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8")
        stdout, stderr = handle, subprocess.STDOUT
    try:
        return subprocess.Popen(argv, stdout=stdout, stderr=stderr).wait()
    finally:
        if handle is not None:
            handle.close()


@dataclasses.dataclass
class Supervisor:
    """One live session, restarted on unexpected death until it stops or gives up.

    The launcher, the clock and the sleep are constructor arguments rather than module globals,
    so a test can drive a hundred restarts in no time and pin the policy without a wall clock
    anywhere near it.
    """

    session: list = dataclasses.field(default_factory=list)
    log: Path = Path("runs/live-supervisor.jsonl")
    stop_file: Path | None = None
    session_log: Path | None = None
    max_failures: int = DEFAULT_MAX_FAILURES
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR
    backoff_ceiling: float = DEFAULT_BACKOFF_CEILING_SECONDS
    healthy_seconds: float = DEFAULT_HEALTHY_SECONDS
    clock: object = time.time
    sleep: object = time.sleep
    launch: object = launch

    def __post_init__(self):
        if self.max_failures < 1:
            raise ValueError(
                "max_failures must be at least 1: a supervisor that never retries a crash is "
                "a launcher with extra steps"
            )
        if self.backoff_factor < 1:
            raise ValueError("backoff_factor below 1 would shorten the wait after every crash")

    def stop_requested(self) -> bool:
        """Whether the operator has asked for the session to end."""
        return self.stop_file is not None and Path(self.stop_file).exists()

    def backoff_for(self, failures: int) -> float:
        """Seconds to wait after ``failures`` consecutive crashes.

        The first crash waits ``backoff_seconds``, and each further one doubles it up to the
        ceiling.
        """
        wait = self.backoff_seconds * (self.backoff_factor ** max(0, failures - 1))
        return round(min(wait, self.backoff_ceiling), 3)

    def record(self, event: str, **fields) -> dict:
        """Append one JSON Lines record and return it.

        Append-only, one write per record: the log is the supervisor's only durable statement
        about what it did, so it never rewrites a line and never holds one in memory.
        """
        payload = {"ts": utc(self.clock()), "event": event, **fields}
        path = Path(self.log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
        return payload

    def run(self) -> int:
        """Supervise the session until it ends expectedly or the retries run out."""
        if not self.session:
            raise ValueError("no session command to run")
        attempts = 0
        failures = 0
        while True:
            # Asked before every launch as well as after every exit: a stop file that appears
            # while the session is down means the answer is already no.
            if self.stop_requested():
                self.record("stop", reason="stop_file", attempts=attempts)
                return EXIT_CLEAN
            attempts += 1
            started = self.clock()
            self.record("start", attempt=attempts, command=list(self.session))
            start_error = None
            try:
                code = self.launch(list(self.session), self.session_log)
            except KeyboardInterrupt:
                self.record(
                    "stop",
                    reason="interrupted",
                    attempts=attempts,
                    runtime_seconds=round(self.clock() - started, 3),
                )
                return EXIT_INTERRUPTED
            except OSError as error:
                # The session could not be started at all — a mistyped command, or a container
                # runtime this host does not have. Recorded with the shell's 127 for "command
                # not found" and put through the same policy as a crash, so an unattended
                # supervisor says what happened instead of dying on a traceback nobody reads.
                code = 127
                start_error = str(error)
            runtime = round(self.clock() - started, 3)
            stopped = self.stop_requested()
            reason = "stop_file" if stopped else ("exit_zero" if code == 0 else "crash")
            expected = code == 0 or stopped
            self.record(
                "exit",
                attempt=attempts,
                exit_code=code,
                runtime_seconds=runtime,
                expected=expected,
                reason=reason,
                **({"error": start_error} if start_error else {}),
            )
            if expected:
                self.record(
                    "stop",
                    reason=reason,
                    attempts=attempts,
                    exit_code=code,
                    runtime_seconds=runtime,
                )
                return EXIT_CLEAN
            if runtime >= self.healthy_seconds:
                failures = 0
            failures += 1
            if failures >= self.max_failures:
                self.record(
                    "give_up",
                    reason="consecutive_crashes",
                    failures=failures,
                    attempts=attempts,
                    last_exit_code=code,
                )
                return EXIT_GAVE_UP
            wait = self.backoff_for(failures)
            self.record(
                "restart",
                attempt=attempts + 1,
                failures=failures,
                backoff_seconds=wait,
                last_exit_code=code,
            )
            try:
                self.sleep(wait)
            except KeyboardInterrupt:
                self.record("stop", reason="interrupted", attempts=attempts)
                return EXIT_INTERRUPTED


def parse_args(argv):
    """Split ``--`` into the supervisor's own arguments and the session's argv."""
    argv = list(argv)
    if "--" in argv:
        cut = argv.index("--")
        head, session = argv[:cut], argv[cut + 1 :]
    else:
        head, session = argv, []
    return head, session


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m flyvsly.supervise",
        description="Run a live paper session, restarting it if it dies.",
    )
    parser.add_argument("--log", required=True, help="append-only JSON Lines supervisor log")
    parser.add_argument(
        "--stop-file", default=None, help="stop for good when this file appears"
    )
    parser.add_argument(
        "--session-log",
        default=None,
        help="append the session's own output here; default is to inherit stdout/stderr",
    )
    parser.add_argument("--max-failures", type=int, default=DEFAULT_MAX_FAILURES)
    parser.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF_SECONDS)
    parser.add_argument("--backoff-factor", type=float, default=DEFAULT_BACKOFF_FACTOR)
    parser.add_argument("--backoff-ceiling", type=float, default=DEFAULT_BACKOFF_CEILING_SECONDS)
    parser.add_argument("--healthy-seconds", type=float, default=DEFAULT_HEALTHY_SECONDS)
    head, session = parse_args(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(head)
    if not session:
        parser.error("no session command: put the session's argv after --")
    supervisor = Supervisor(
        session=session,
        log=Path(args.log),
        stop_file=Path(args.stop_file) if args.stop_file else None,
        session_log=Path(args.session_log) if args.session_log else None,
        max_failures=args.max_failures,
        backoff_seconds=args.backoff,
        backoff_factor=args.backoff_factor,
        backoff_ceiling=args.backoff_ceiling,
        healthy_seconds=args.healthy_seconds,
    )
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
