"""Watch a live paper session, keep it running, and notice when it stops trading.

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
* **A crash continues the session it killed, when the disk says it can.** A restart used to
  mint a new run id, so every crash silently began a new experiment with fresh $100 accounts.
  `flyvsly live --resume <run id>` continues the killed session instead, and that is what the
  supervisor now launches: same run directory, same accounts at the equity they had reached,
  the bars already traded read back from the session's own log rather than traded again. What
  makes a run resumable is not decided here — `salvage.load_resume` is asked, and its answer is
  the policy (it refuses a session with a recording, a checkpoint that is no longer `running`,
  a hole in the log, an order intent for a bar the log never recorded, or a checkpoint with no
  session start). If it cannot be asked or refuses, the next launch is a fresh session and the
  log says so and why. A continuation that dies without trading one bar is recorded as a failed
  resume and the next launch is fresh, so a checkpoint that exists but cannot actually be
  continued does not burn the whole ladder first.
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
* **A session that is alive but silent is a failure too.** Dying is not the only way to stop
  trading: the loop can wedge, or the venue can stop publishing, and the process stays healthy
  and quiet. The session writes a checkpoint every bar and it names the newest bar it traded,
  so the supervisor samples that while the session runs and treats `DEFAULT_STALL_BARS` bar
  lengths without a new bar as a failure — it ends the process and lets the crash policy above
  restart it (as a continuation, if the disk still allows one). This is deliberately blunt: it
  cannot tell a wedged loop from an exchange outage, because from outside there is no
  difference — both are bars that stopped arriving. It restarts in both cases, and a venue that
  really has stopped is refused by the restarted session's own prime, which ends in a give-up
  that names the venue. Nor can it see a session that is trading badly, or slowly: any bar
  inside the window is progress, however bad the bar.
* **Nothing is decided quietly.** Every start, exit, restart, resume, fallback, give-up and
  expected stop is one JSON Lines record, appended with a UTC timestamp, the attempt number and
  the exit code. A supervisor that silently retries is worse than no supervisor, because it
  hides the pattern that a human needs to see.

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

# How long a running session may go without a new bar before it is treated as failed, counted in
# bar lengths. Five is not a round number chosen for its looks: it is the same window the feed
# itself uses to refuse a venue whose bars stopped closing (`live.STALE_WINDOW_BARS`), so the
# supervisor and the session agree on what "this market has gone quiet" means. The arithmetic
# against the recorded session, which is the only real one: 60 s bars, so 5 bars is 300 s of
# silence; it polled 1914 times over 429 bars — one poll every ~13 s, at most the 15 s
# `--poll` ceiling — and a bar cost 7.05 s of compute on average, 29.9 s at worst, with 21.1 s
# of lag at the end. A healthy session writes a bar well inside 300 s — ten times the worst bar
# it ever took — and a wedged process is caught within five minutes, long before anyone is awake
# to notice the equity curve has gone flat.
DEFAULT_STALL_BARS = 5

# The bar length assumed until the session's checkpoint says otherwise: the same default
# `flyvsly live` starts with, or the `--bar-seconds` on its own command line.
DEFAULT_BAR_SECONDS = 60

# How often a running session's checkpoint is sampled. Five seconds is inside every bar length
# this project trades (60 s and up), so the watchdog sees each bar land, and far enough apart
# that a six-hour session costs fewer than 5000 stats of a small JSON file.
DEFAULT_WATCH_INTERVAL_SECONDS = 5.0

# How long a session that will not stop is given between the ask and the kill.
STOP_GRACE_SECONDS = 10.0

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


class Session:
    """A running session, watched rather than merely waited for.

    A live session is started rather than blocked on, because the supervisor has to sample what
    it writes while it runs: ``poll`` is the exit code or None, and ``end`` stops a session that
    will not stop on its own — which is the only way a watch that reads files can act on what it
    reads.
    """

    def __init__(self, argv, session_log=None):
        self._log = None
        stdout = stderr = None
        if session_log is not None:
            path = Path(session_log)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._log = path.open("a", encoding="utf-8")
            stdout, stderr = self._log, subprocess.STDOUT
        try:
            self.process = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
        except BaseException:
            self.close()
            raise

    def poll(self):
        """The exit code once the session has ended, or None while it is still running."""
        return self.process.poll()

    def wait(self):
        """Wait for the session and return its exit code."""
        try:
            return self.process.wait()
        finally:
            self.close()

    def end(self, grace: float = STOP_GRACE_SECONDS) -> int:
        """Stop a session that is not going to stop, and return what it exited with.

        Ask first, kill second: a process wedged badly enough to be caught by the watchdog will
        usually ignore the ask, and the kill is what keeps it from holding the run directory
        against the session that is about to continue it.
        """
        self.process.terminate()
        try:
            return self.process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait()
        finally:
            self.close()

    def close(self) -> None:
        """Close the session's log, if this session opened one."""
        if self._log is not None:
            self._log.close()
            self._log = None


def launch(argv, session_log=None) -> Session:
    """Start the session as a child process and return it, running.

    With a ``session_log`` the session's own output is appended there, next to the supervisor's
    record, so an unattended run leaves both; without one the child inherits this process's
    stdout and stderr, which is what a foreground operator wants.
    """
    return Session(argv, session_log)


@dataclasses.dataclass
class Supervisor:
    """One live session, restarted on unexpected death until it stops or gives up.

    The launcher, the clock and the sleep are constructor arguments rather than module globals,
    so a test can drive a hundred restarts in no time and pin the policy without a wall clock
    anywhere near it. The launcher may return the exit code directly, for a session that has
    already been waited for (which is all the tests that do not exercise the watchdog need), or
    a :class:`Session` to poll, which is what lets silence be noticed.

    ``runs`` is where sessions are written. It is what makes a crash a continuation and a
    silence visible: without it the supervisor can only restart, so the module's own CLI leaves
    it unset unless told, while the script always passes the same directory the session writes.
    """

    session: list = dataclasses.field(default_factory=list)
    log: Path = Path("runs/live-supervisor.jsonl")
    runs: Path | None = None
    stop_file: Path | None = None
    session_log: Path | None = None
    max_failures: int = DEFAULT_MAX_FAILURES
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR
    backoff_ceiling: float = DEFAULT_BACKOFF_CEILING_SECONDS
    healthy_seconds: float = DEFAULT_HEALTHY_SECONDS
    stall_bars: int = DEFAULT_STALL_BARS
    watch_interval: float = DEFAULT_WATCH_INTERVAL_SECONDS
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
        if self.stall_bars < 1:
            raise ValueError(
                "stall_bars below 1 would fail every session the moment it was sampled"
            )

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

    # -- reading what a session writes -----------------------------------------------------

    def checkpoint(self, run_dir) -> dict | None:
        """A run's checkpoint as JSON, or None when there is nothing readable there.

        The session rewrites the file atomically, so a sample that lands mid-write reads as
        nothing rather than as an error, and the next sample gets the whole file: a supervisor
        that died on a half-read would be less reliable than the thing it watches.
        """
        if run_dir is None:
            return None
        try:
            return json.loads((Path(run_dir) / "live_state.json").read_text())
        except (OSError, ValueError):
            return None

    def newest_run(self, since: float):
        """The newest run directory whose checkpoint was written at or after ``since``.

        ``since`` is the attempt's own start, so this names the run *that attempt* wrote rather
        than a killed session that was already on disk when the operator restarted the
        supervisor.
        """
        if self.runs is None:
            return None
        newest = None
        for path in Path(self.runs).glob("*/live_state.json"):
            try:
                stamp = path.stat().st_mtime
            except OSError:
                continue
            if stamp >= since and (newest is None or stamp > newest[0]):
                newest = (stamp, path.parent)
        return newest[1] if newest else None

    def bar_seconds_of(self, run_dir, argv) -> int:
        """How long one bar lasts: what the checkpoint recorded, else the session's own flag.

        The checkpoint is authoritative because it is the session's own statement; the flag is
        the fallback for the window before a session has written one at all, which is exactly
        the window in which a session that hangs during its prime would otherwise be invisible.
        """
        state = self.checkpoint(run_dir) or {}
        try:
            recorded = int(state.get("bar_seconds") or 0)
        except (TypeError, ValueError):
            recorded = 0
        if recorded > 0:
            return recorded
        for index, token in enumerate(argv):
            value = None
            if token == "--bar-seconds" and index + 1 < len(argv):
                value = argv[index + 1]
            elif token.startswith("--bar-seconds="):
                value = token.split("=", 1)[1]
            if value is not None:
                try:
                    return int(value)
                except ValueError:
                    break
        return DEFAULT_BAR_SECONDS

    def last_progress(self, run_dir, started: float, bar_seconds: int) -> float:
        """When the session's own writing last showed a new bar, never before its own start.

        The checkpoint's ``last_bar`` is the *open* of the last bar traded, so it is complete —
        and the bar is on the record — at ``last_bar + bar_seconds``. A session that has not
        traded yet says nothing with ``last_bar``, so its checkpoint's mtime stands in: the file
        is written before the loop starts, so silence is then measured from that write. Both are
        floored at the attempt's own start, because a resumed session opens with a ``last_bar``
        from the process that died — days old, if the host was down — and must not be failed for
        the downtime it was started to recover from.
        """
        state = self.checkpoint(run_dir) or {}
        try:
            last = int(state["last_bar"]) if state.get("last_bar") is not None else None
        except (TypeError, ValueError):
            last = None
        if last is not None and bar_seconds > 0:
            return max(started, float(last) + bar_seconds)
        if run_dir is not None:
            try:
                return max(started, (Path(run_dir) / "live_state.json").stat().st_mtime)
            except OSError:
                pass
        return started

    def bars_traded(self, run_id):
        """The bars a run's checkpoint says it has traded, or None without a checkpoint."""
        if self.runs is None or not run_id:
            return None
        state = self.checkpoint(Path(self.runs) / run_id)
        if state is None:
            return None
        try:
            return int(state.get("bars_traded") or 0)
        except (TypeError, ValueError):
            return None

    # -- deciding the next launch ----------------------------------------------------------

    def command_for(self, run_id) -> list:
        """The session's argv for the next launch, with the run to continue when there is one.

        Any ``--resume`` already on the session command is dropped first: the supervisor owns
        which session is being continued, and a command line carrying two of them would make the
        launch say one thing and do another.
        """
        argv = list(self.session)
        if not run_id:
            return argv
        cleaned = []
        skip = False
        for token in argv:
            if skip:
                skip = False
                continue
            if token == "--resume":
                skip = True
                continue
            if token.startswith("--resume="):
                continue
            cleaned.append(token)
        return cleaned + ["--resume", str(run_id)]

    def continuation(self, run_id, resumed_attempt: bool, advanced: bool):
        """The run the next launch should continue, and why it cannot, if it cannot.

        Resumability is not re-derived here: ``salvage.load_resume`` is asked, right now, from
        the artifacts, so this agrees with ``flyvsly live --resume`` and with a supervisor that
        never saw the crashed process. It is imported here rather than at the top of the module
        because it is only needed on a crash, and the arena it pulls in is not (the supervisor's
        own import graph stays standard library).

        Three answers, three records an operator can tell apart: the run id to continue; nothing
        because there was no checkpoint to ask about; nothing because the checkpoint refused,
        with the refusal as the reason. A fourth, ``resume_failed``, covers the checkpoint that
        *was* resumable and whose continuation died without trading a bar: the next launch is
        fresh rather than asking the same run again, which is what keeps one uncontinuable
        checkpoint from spending the whole crash ladder.
        """
        if self.runs is None:
            return None, "no_runs_dir", None
        if run_id is None:
            return None, "no_checkpoint", None
        from .salvage import load_resume

        run_dir = Path(self.runs) / str(run_id)
        try:
            state = load_resume(run_dir)
        except (OSError, ValueError) as error:
            return None, "resume_refused", str(error)
        if resumed_attempt and not advanced:
            return (
                None,
                "resume_failed",
                f"{run_id} was continued but traded no bar before it died; the next launch is "
                "a new session",
            )
        return state.run_id, None, None

    # -- running ---------------------------------------------------------------------------

    def run_once(self, argv, started: float, run_id):
        """Run one attempt, watching a running session for silence.

        Returns ``(exit_code, start_error, stall)``. ``stall`` is None for an ordinary exit, or
        the fields describing why a live session was ended: how long it had been silent, the
        threshold it crossed, the bar length that made that threshold, and the run it was
        writing. A launcher that returns an exit code has already waited for the session, so
        there is nothing to watch and the code is returned as it stands.
        """
        try:
            running = self.launch(list(argv), self.session_log)
        except OSError as error:
            return 127, str(error), None
        if isinstance(running, int):
            return running, None, None
        run_dir = Path(self.runs) / run_id if (self.runs is not None and run_id) else None
        bar_seconds = self.bar_seconds_of(run_dir, argv)
        while True:
            code = running.poll()
            if code is not None:
                return code, None, None
            if self.runs is not None:
                if run_dir is None:
                    run_dir = self.newest_run(started)
                    if run_dir is not None:
                        bar_seconds = self.bar_seconds_of(run_dir, argv)
                threshold = self.stall_bars * bar_seconds
                silent = round(
                    self.clock() - self.last_progress(run_dir, started, bar_seconds), 3
                )
                if silent > threshold:
                    # A session that will not end itself has to be ended here: the next attempt
                    # opens the same run directory, and two processes trading one account is
                    # worse than the bars this kills.
                    code = running.end()
                    return code, None, {
                        "silent_seconds": silent,
                        "stall_seconds": float(threshold),
                        "bar_seconds": bar_seconds,
                        "run_id": run_dir.name if run_dir is not None else None,
                    }
            self.sleep(self.watch_interval)

    def run_that_ended(self, started: float, run_id, stall):
        """Which run the attempt that just ended was writing, if the disk can say."""
        if run_id:
            return str(run_id)
        if stall and stall.get("run_id"):
            return stall["run_id"]
        found = self.newest_run(started)
        return found.name if found is not None else None

    def run(self) -> int:
        """Supervise the session until it ends expectedly or the retries run out."""
        if not self.session:
            raise ValueError("no session command to run")
        attempts = 0
        failures = 0
        resume_id = None
        while True:
            # Asked before every launch as well as after every exit: a stop file that appears
            # while the session is down means the answer is already no.
            if self.stop_requested():
                self.record("stop", reason="stop_file", attempts=attempts)
                return EXIT_CLEAN
            attempts += 1
            command = self.command_for(resume_id)
            started = self.clock()
            self.record(
                "start",
                attempt=attempts,
                command=command,
                mode="resume" if resume_id else "fresh",
                run_id=resume_id,
            )
            baseline = self.bars_traded(resume_id)
            try:
                code, start_error, stall = self.run_once(command, started, resume_id)
            except KeyboardInterrupt:
                # The console delivers Ctrl-C to the session as well, so it is finishing the
                # bar it is on; the supervisor only records that the operator asked for the end.
                self.record(
                    "stop",
                    reason="interrupted",
                    attempts=attempts,
                    runtime_seconds=round(self.clock() - started, 3),
                )
                return EXIT_INTERRUPTED
            runtime = round(self.clock() - started, 3)
            stopped = self.stop_requested()
            reason = (
                "stop_file"
                if stopped
                else "stalled"
                if stall is not None
                else "exit_zero"
                if code == 0
                else "crash"
            )
            expected = code == 0 or stopped
            fields = {
                "attempt": attempts,
                "exit_code": code,
                "runtime_seconds": runtime,
                "expected": expected,
                "reason": reason,
            }
            if start_error:
                fields["error"] = start_error
            if stall is not None:
                fields.update(stall)
            if resume_id:
                after = self.bars_traded(resume_id)
                fields["resumed"] = resume_id
                fields["bars_traded"] = after
            self.record("exit", **fields)
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
            advanced = baseline is not None and (fields.get("bars_traded") or 0) > baseline
            next_run, why, detail = self.continuation(
                self.run_that_ended(started, resume_id, stall),
                resumed_attempt=resume_id is not None,
                advanced=advanced,
            )
            wait = self.backoff_for(failures)
            restart = {
                "attempt": attempts + 1,
                "failures": failures,
                "backoff_seconds": wait,
                "last_exit_code": code,
                "mode": "resume" if next_run else "fresh",
            }
            if next_run:
                restart["run_id"] = next_run
            else:
                restart["reason"] = why
            if detail:
                restart["error"] = detail
            self.record("restart", **restart)
            resume_id = next_run
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
        "--runs",
        default=None,
        help=(
            "where the session writes its run directories: needed to continue a killed "
            "session and to notice one that has stopped trading (default: neither)"
        ),
    )
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
    parser.add_argument(
        "--stall-bars",
        type=int,
        default=DEFAULT_STALL_BARS,
        help="bar lengths without a new bar before a running session is treated as failed",
    )
    head, session = parse_args(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(head)
    if not session:
        parser.error("no session command: put the session's argv after --")
    supervisor = Supervisor(
        session=session,
        log=Path(args.log),
        runs=Path(args.runs) if args.runs else None,
        stop_file=Path(args.stop_file) if args.stop_file else None,
        session_log=Path(args.session_log) if args.session_log else None,
        max_failures=args.max_failures,
        backoff_seconds=args.backoff,
        backoff_factor=args.backoff_factor,
        backoff_ceiling=args.backoff_ceiling,
        healthy_seconds=args.healthy_seconds,
        stall_bars=args.stall_bars,
    )
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
