"""Supervision: a live session that dies comes back, and every decision is written down.

These tests pin the policy rather than the plumbing. An expected end is not a failure and must
not be retried; a crash must be, with a wait that grows and then stops growing; the ladder must
end in a give-up that says so and exits non-zero; and a run that lasted long enough to prove the
venue and the graph work must clear the ladder. A crash must also come back as the *same*
session when the checkpoint on disk allows it, fall back to a new one when it does not, and say
which happened; and a session that is alive but has stopped writing bars must be treated as
failed, without a healthy session ever being mistaken for one. Nothing here touches a clock, a
market or the network: the launcher, the clock and the sleep are the supervisor's own arguments,
and the session's checkpoint is a file the test writes.

The last tests drive scripts/live-supervisor.sh, because the script is what an operator runs.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from flyvsly.supervise import EXIT_CLEAN, EXIT_GAVE_UP, EXIT_INTERRUPTED, Supervisor

RUN_LOG = "supervisor.jsonl"


class Fake:
    """A launcher with scripted exit codes, and a clock that only moves when it does.

    ``runtimes`` says how long each attempt lasted; the clock jumps by that much, so a session
    that ran for an hour costs a test nothing. The last code and runtime repeat, so a test that
    only cares how the runs end can leave the script short.
    """

    def __init__(self, codes, runtimes=None):
        self.codes = list(codes)
        self.runtimes = list(runtimes if runtimes is not None else [0.0] * len(self.codes))
        self.calls = []
        self.slept = []
        self.now = 1_700_000_000.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)

    def launch(self, argv, session_log):
        self.calls.append(list(argv))
        attempt = len(self.calls)
        self.now += self.runtimes[min(attempt, len(self.runtimes)) - 1]
        return self.codes[min(attempt, len(self.codes)) - 1]


def supervised(tmp_path, fake, **kwargs):
    return Supervisor(
        session=["fake", "session"],
        log=tmp_path / RUN_LOG,
        clock=fake.clock,
        sleep=fake.sleep,
        launch=fake.launch,
        **kwargs,
    )


def records(tmp_path):
    path = tmp_path / RUN_LOG
    return [json.loads(line) for line in path.read_text().splitlines()]


def events(rows):
    return [row["event"] for row in rows]


def write_checkpoint(runs, run_id, **overrides):
    """The live_state.json a killed session leaves, in the shape `load_resume` reads.

    The defaults are a session that traded three bars and was killed between two of them:
    ``running``, no recording, a continuous log of observations that does not exist (which
    reads as a session killed before its first bar would record one — an empty log is still
    continuous), and a ``session_opened`` to date it from.
    """
    run = Path(runs) / run_id
    run.mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": run_id,
        "status": "running",
        "bar_seconds": 60,
        "warmup_bars": 120,
        "session_opened": 1_699_999_800,
        "bars_traded": 3,
        "last_bar": 1_699_999_940,
    }
    state.update(overrides)
    (run / "live_state.json").write_text(json.dumps(state))
    return run


class Running:
    """A session process that stays alive until it is killed or reaches a scripted exit.

    ``poll`` returns None while it runs and the exit code once it is over; ``killed`` says
    whether the watchdog ended it, which is the difference between a crash and a session that
    was alive but silent.
    """

    def __init__(self, code, stop_after=None):
        self.code = code
        self.stop_after = stop_after
        self.samples = 0
        self.over = False
        self.killed = False

    def poll(self):
        if self.over:
            return self.code
        self.samples += 1
        if self.stop_after is not None and self.samples > self.stop_after:
            self.over = True
            return self.code
        return None

    def end(self, grace=None):
        self.over = True
        self.killed = True
        return self.code


class Watch:
    """A clock and a sleep that only move when the supervisor sleeps."""

    def __init__(self, now):
        self.now = now
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_a_clean_exit_ends_the_supervisor_without_retrying(tmp_path):
    fake = Fake(codes=[0])
    assert supervised(tmp_path, fake).run() == EXIT_CLEAN
    assert events(records(tmp_path)) == ["start", "exit", "stop"]
    start, exit_, stop = records(tmp_path)
    assert start["attempt"] == 1
    assert exit_["exit_code"] == 0 and exit_["expected"] is True and exit_["reason"] == "exit_zero"
    assert stop["reason"] == "exit_zero"
    assert len(fake.calls) == 1 and fake.slept == []


def test_a_stop_file_outranks_a_bad_exit_code(tmp_path):
    """The operator asked for the end; the supervisor does not second-guess the exit code."""
    stop = tmp_path / "live.stop"
    calls = []

    def launch(argv, session_log):
        calls.append(argv)
        stop.write_text("")
        return 137

    supervisor = Supervisor(
        session=["fake"],
        log=tmp_path / RUN_LOG,
        stop_file=stop,
        clock=lambda: 1_700_000_000.0,
        sleep=lambda seconds: calls.append(("sleep", seconds)),
        launch=launch,
    )
    assert supervisor.run() == EXIT_CLEAN
    assert events(records(tmp_path)) == ["start", "exit", "stop"]
    exit_ = records(tmp_path)[1]
    assert exit_["exit_code"] == 137 and exit_["reason"] == "stop_file" and exit_["expected"] is True
    assert len(calls) == 1  # no restart, and no wait before deciding not to


def test_a_stop_file_already_there_never_starts_a_session(tmp_path):
    stop = tmp_path / "live.stop"
    stop.write_text("")
    fake = Fake(codes=[0])
    supervisor = supervised(tmp_path, fake, stop_file=stop)
    assert supervisor.run() == EXIT_CLEAN
    assert fake.calls == []
    assert [(row["event"], row["reason"], row["attempts"]) for row in records(tmp_path)] == [
        ("stop", "stop_file", 0)
    ]


def test_a_crash_is_retried_with_a_wait_that_grows(tmp_path):
    fake = Fake(codes=[1, 1, 1, 0])
    assert supervised(tmp_path, fake, max_failures=5).run() == EXIT_CLEAN
    rows = records(tmp_path)
    restarts = [row for row in rows if row["event"] == "restart"]
    assert [row["attempt"] for row in restarts] == [2, 3, 4]
    assert [row["failures"] for row in restarts] == [1, 2, 3]
    assert [row["backoff_seconds"] for row in restarts] == [5.0, 10.0, 20.0]
    assert fake.slept == [5.0, 10.0, 20.0]
    assert [row["exit_code"] for row in rows if row["event"] == "exit"] == [1, 1, 1, 0]


def test_the_failure_ceiling_gives_up_and_is_recorded(tmp_path):
    fake = Fake(codes=[1])
    assert supervised(tmp_path, fake, max_failures=3).run() == EXIT_GAVE_UP
    rows = records(tmp_path)
    assert len(fake.calls) == 3
    assert events(rows).count("start") == 3
    assert events(rows).count("restart") == 2
    last = rows[-1]
    assert last["event"] == "give_up"
    assert last["failures"] == 3 and last["attempts"] == 3 and last["last_exit_code"] == 1
    assert fake.slept == [5.0, 10.0]


def test_a_healthy_run_clears_the_crash_ladder(tmp_path):
    """Two crashes, an hour of trading, then two more: the hour is not part of a crash loop."""
    fake = Fake(codes=[1, 1, 1, 1], runtimes=[0.0, 3600.0, 0.0, 0.0])
    supervisor = supervised(tmp_path, fake, max_failures=3, healthy_seconds=600.0)
    assert supervisor.run() == EXIT_GAVE_UP
    rows = records(tmp_path)
    restarts = [row for row in rows if row["event"] == "restart"]
    assert [row["failures"] for row in restarts] == [1, 1, 2]
    assert [row["backoff_seconds"] for row in restarts] == [5.0, 5.0, 10.0]
    assert len(fake.calls) == 4  # the healthy run earned another attempt, not immunity
    assert rows[-1]["attempts"] == 4


def test_the_backoff_ladder_stops_at_its_ceiling(tmp_path):
    supervisor = Supervisor(session=["fake"], log=tmp_path / RUN_LOG)
    assert [supervisor.backoff_for(n) for n in range(1, 9)] == [
        5.0,
        10.0,
        20.0,
        40.0,
        80.0,
        160.0,
        300.0,
        300.0,
    ]


@pytest.mark.parametrize("interrupted", ["during the run", "during the wait"])
def test_an_operator_interrupt_stops_the_supervisor(tmp_path, interrupted):
    def launch(argv, session_log):
        if interrupted == "during the run":
            raise KeyboardInterrupt
        return 1

    def sleep(seconds):
        raise KeyboardInterrupt

    supervisor = Supervisor(
        session=["fake"],
        log=tmp_path / RUN_LOG,
        clock=lambda: 1_700_000_000.0,
        sleep=sleep,
        launch=launch,
    )
    assert supervisor.run() == EXIT_INTERRUPTED
    rows = records(tmp_path)
    assert rows[-1]["event"] == "stop" and rows[-1]["reason"] == "interrupted"
    assert "give_up" not in events(rows)
    if interrupted == "during the run":
        # An interrupt during the wait comes after the restart it was about to make: the
        # decision is on the record, the wait it asked for is not.
        assert "restart" not in events(rows)


def test_a_session_that_cannot_be_started_follows_the_crash_policy(tmp_path):
    """A mistyped command is not a reason for the supervisor to die on a traceback."""

    def launch(argv, session_log):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    supervisor = Supervisor(
        session=["typo"],
        log=tmp_path / RUN_LOG,
        clock=lambda: 1.0,
        sleep=lambda seconds: None,
        launch=launch,
        max_failures=2,
    )
    assert supervisor.run() == EXIT_GAVE_UP
    rows = records(tmp_path)
    assert [row["exit_code"] for row in rows if row["event"] == "exit"] == [127, 127]
    assert "cannot find the file" in rows[1]["error"]
    assert rows[-1]["event"] == "give_up" and rows[-1]["last_exit_code"] == 127


def test_settings_that_would_make_the_policy_meaningless_are_refused(tmp_path):
    with pytest.raises(ValueError):
        Supervisor(session=["fake"], log=tmp_path / RUN_LOG, max_failures=0)
    with pytest.raises(ValueError):
        Supervisor(session=["fake"], log=tmp_path / RUN_LOG, backoff_factor=0.5)
    with pytest.raises(ValueError):
        Supervisor(session=["fake"], log=tmp_path / RUN_LOG, stall_bars=0)
    with pytest.raises(ValueError):
        Supervisor(session=[], log=tmp_path / RUN_LOG).run()


def test_the_log_is_append_only_and_every_record_is_timestamped(tmp_path):
    fake = Fake(codes=[0])
    supervised(tmp_path, fake).run()
    supervised(tmp_path, fake).run()
    rows = records(tmp_path)
    assert events(rows) == ["start", "exit", "stop"] * 2
    assert [row["attempt"] for row in rows if row["event"] == "start"] == [1, 1]
    assert all(row["ts"].endswith("Z") for row in rows)


def test_without_a_runs_directory_a_crash_just_restarts(tmp_path):
    """No run directory to read means no continuation and no watchdog: a plain restart."""
    fake = Fake(codes=[1, 0])
    assert supervised(tmp_path, fake).run() == EXIT_CLEAN
    restart = [row for row in records(tmp_path) if row["event"] == "restart"][0]
    assert restart["mode"] == "fresh" and restart["reason"] == "no_runs_dir"
    assert "run_id" not in restart


def test_a_crash_with_a_resumable_checkpoint_continues_the_same_run(tmp_path):
    runs = tmp_path / "runs"
    write_checkpoint(runs, "killed-run", bars_traded=7)
    fake = Fake(codes=[1, 0])
    assert supervised(tmp_path, fake, runs=runs).run() == EXIT_CLEAN
    assert fake.calls[0] == ["fake", "session"]
    assert fake.calls[1] == ["fake", "session", "--resume", "killed-run"]
    rows = records(tmp_path)
    restart = [row for row in rows if row["event"] == "restart"][0]
    assert restart["mode"] == "resume" and restart["run_id"] == "killed-run"
    assert restart["last_exit_code"] == 1
    start = [row for row in rows if row["event"] == "start"][1]
    assert start["mode"] == "resume" and start["command"][-2:] == ["--resume", "killed-run"]
    assert start["run_id"] == "killed-run"


def test_a_crash_with_no_checkpoint_starts_a_new_session_and_says_so(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    fake = Fake(codes=[1, 0])
    assert supervised(tmp_path, fake, runs=runs).run() == EXIT_CLEAN
    assert fake.calls[1] == ["fake", "session"]  # nothing to continue
    restart = [row for row in records(tmp_path) if row["event"] == "restart"][0]
    assert restart["mode"] == "fresh" and restart["reason"] == "no_checkpoint"


def test_a_resume_that_dies_without_trading_falls_back_to_a_new_session(tmp_path):
    """One uncontinuable checkpoint must not spend the whole crash ladder on resumes."""
    runs = tmp_path / "runs"
    write_checkpoint(runs, "stuck-run", bars_traded=7)
    fake = Fake(codes=[1, 1, 0])
    assert supervised(tmp_path, fake, runs=runs).run() == EXIT_CLEAN
    assert fake.calls[1] == ["fake", "session", "--resume", "stuck-run"]
    assert fake.calls[2] == ["fake", "session"]  # the failed continuation is not retried
    restarts = [row for row in records(tmp_path) if row["event"] == "restart"]
    assert [(row["mode"], row.get("reason")) for row in restarts] == [
        ("resume", None),
        ("fresh", "resume_failed"),
    ]
    assert "traded no bar" in restarts[1]["error"]
    exits = [row for row in records(tmp_path) if row["event"] == "exit"]
    assert exits[1]["resumed"] == "stuck-run" and exits[1]["bars_traded"] == 7


def test_a_failed_resume_counts_on_the_same_crash_ladder(tmp_path):
    """A resume is a launch like any other: its death is a rung, not a fresh start."""
    runs = tmp_path / "runs"
    write_checkpoint(runs, "stuck-run", bars_traded=7)
    fake = Fake(codes=[1, 1, 1])
    assert supervised(tmp_path, fake, runs=runs, max_failures=2).run() == EXIT_GAVE_UP
    assert len(fake.calls) == 2  # the give-up is the second rung, not a third attempt
    rows = records(tmp_path)
    assert rows[-1]["event"] == "give_up" and rows[-1]["failures"] == 2
    assert rows[-1]["last_exit_code"] == 1


def test_a_continued_session_that_traded_before_dying_is_continued_again(tmp_path):
    """A continuation that did trade is the session; its next death continues it again."""
    runs = tmp_path / "runs"
    run = write_checkpoint(runs, "busy-run", bars_traded=7)
    inner = Fake(codes=[1, 1, 0])

    def launch(argv, session_log):
        if "--resume" in argv:
            state = json.loads((run / "live_state.json").read_text())
            if state["bars_traded"] == 7:
                # The resumed session traded two bars before it died.
                state["bars_traded"] = 9
                (run / "live_state.json").write_text(json.dumps(state))
        return inner.launch(argv, session_log)

    supervisor = Supervisor(
        session=["fake"],
        log=tmp_path / RUN_LOG,
        runs=runs,
        clock=inner.clock,
        sleep=inner.sleep,
        launch=launch,
    )
    assert supervisor.run() == EXIT_CLEAN
    assert inner.calls[0] == ["fake"]
    assert inner.calls[1] == ["fake", "--resume", "busy-run"]
    assert inner.calls[2] == ["fake", "--resume", "busy-run"]
    restarts = [row for row in records(tmp_path) if row["event"] == "restart"]
    assert [row["mode"] for row in restarts] == ["resume", "resume"]
    assert [row["run_id"] for row in restarts] == ["busy-run", "busy-run"]


@pytest.mark.parametrize(
    "case,phrase",
    [
        ("a recording", "already has a recording.json"),
        ("a checkpoint that stopped running", "not running"),
        ("an order for a bar the log never recorded", "killed inside a bar"),
    ],
)
def test_a_checkpoint_the_loader_refuses_falls_back_and_names_the_refusal(
    tmp_path, case, phrase
):
    runs = tmp_path / "runs"
    run = write_checkpoint(runs, "closed-run", bars_traded=1)
    if case == "a recording":
        (run / "recording.json").write_text("{}")
    elif case == "a checkpoint that stopped running":
        state = json.loads((run / "live_state.json").read_text())
        state["status"] = "salvaged"
        (run / "live_state.json").write_text(json.dumps(state))
    else:
        (run / "observations.jsonl").write_text(json.dumps({"i": 0, "t": 1_699_999_940}) + "\n")
        ledger = sqlite3.connect(run / "gordon.sqlite")
        ledger.execute("CREATE TABLE orders (plan TEXT)")
        ledger.execute(
            "INSERT INTO orders VALUES (?)",
            (json.dumps({"neural_observation": {"t": 1_700_000_000}}),),
        )
        ledger.commit()
        ledger.close()
    fake = Fake(codes=[1, 0])
    assert supervised(tmp_path, fake, runs=runs).run() == EXIT_CLEAN
    assert fake.calls[1] == ["fake", "session"]
    restart = [row for row in records(tmp_path) if row["event"] == "restart"][0]
    assert restart["mode"] == "fresh" and restart["reason"] == "resume_refused"
    assert phrase in restart["error"]


def test_the_supervisor_owns_the_resume_flag(tmp_path):
    supervisor = Supervisor(session=["flyvsly", "live", "--resume", "old"], log=tmp_path / RUN_LOG)
    assert supervisor.command_for(None) == ["flyvsly", "live", "--resume", "old"]
    assert supervisor.command_for("new") == ["flyvsly", "live", "--resume", "new"]
    equals = Supervisor(session=["live", "--resume=old"], log=tmp_path / RUN_LOG)
    assert equals.command_for("new") == ["live", "--resume", "new"]


def watched(tmp_path, runs, watch, launch, **kwargs):
    """A supervisor with a runs directory, a moving clock and a pollable launcher."""
    return Supervisor(
        session=["fake", "session"],
        log=tmp_path / RUN_LOG,
        runs=runs,
        clock=watch.clock,
        sleep=watch.sleep,
        launch=launch,
        watch_interval=1.0,
        **kwargs,
    )


def test_a_session_alive_but_silent_past_the_window_is_killed_and_restarted(tmp_path):
    """Alive is not trading: the checkpoint's last bar is 301 s old, so the session is ended.

    The run continues afterwards, because a checkpoint that was only silent is still a
    resumable session.
    """
    runs = tmp_path / "runs"
    write_checkpoint(runs, "quiet-run", last_bar=1_700_000_000, bars_traded=3)
    watch = Watch(1_700_000_010.0)
    sessions = []

    def launch(argv, session_log):
        session = Running(137)
        sessions.append((list(argv), session))
        return session

    supervisor = watched(tmp_path, runs, watch, launch, stall_bars=5, max_failures=2)
    assert supervisor.run() == EXIT_GAVE_UP
    rows = records(tmp_path)
    stalled = rows[1]
    assert stalled["event"] == "exit" and stalled["reason"] == "stalled"
    assert stalled["expected"] is False and stalled["exit_code"] == 137
    assert stalled["silent_seconds"] == 301.0
    assert stalled["stall_seconds"] == 300.0 and stalled["bar_seconds"] == 60
    assert stalled["run_id"] == "quiet-run"
    assert sessions[0][1].killed
    restart = rows[2]
    assert restart["event"] == "restart" and restart["mode"] == "resume"
    assert restart["run_id"] == "quiet-run"
    assert sessions[1][0][-2:] == ["--resume", "quiet-run"]


def test_a_session_still_inside_the_window_is_not_flagged(tmp_path):
    """The near side of the boundary: 300 s of silence at a 60 s bar is not yet a failure.

    The session stays alive through the sample that lands exactly on 300 s and exits on the
    next one, so the strict comparison is what is being pinned: a watchdog that failed on
    ``>=`` would kill this session instead of letting it exit cleanly.
    """
    runs = tmp_path / "runs"
    write_checkpoint(runs, "slow-run", last_bar=1_700_000_000, bars_traded=3)
    watch = Watch(1_700_000_010.0)
    session = Running(0, stop_after=351)  # the sample at exactly 300 s is not the last
    supervisor = watched(tmp_path, runs, watch, lambda argv, log: session, stall_bars=5)
    assert supervisor.run() == EXIT_CLEAN
    rows = records(tmp_path)
    assert events(rows) == ["start", "exit", "stop"]
    assert rows[1]["reason"] == "exit_zero" and "silent_seconds" not in rows[1]
    assert session.killed is False


def test_a_resumed_session_is_not_failed_for_the_downtime_it_recovers(tmp_path):
    """A checkpoint's last bar can be days old; the continuation's clock starts at its own."""
    runs = tmp_path / "runs"
    write_checkpoint(runs, "cold-run", last_bar=1_700_000_000 - 3 * 86400, bars_traded=3)
    watch = Watch(1_700_000_000.0)
    session = Running(0, stop_after=100)  # 100 s of catching up, then a clean exit
    calls = []

    def launch(argv, session_log):
        calls.append(list(argv))
        return session if "--resume" in argv else 1

    supervisor = watched(tmp_path, runs, watch, launch, stall_bars=5)
    assert supervisor.run() == EXIT_CLEAN
    assert calls[1][-2:] == ["--resume", "cold-run"]
    assert session.killed is False
    assert events(records(tmp_path)) == ["start", "exit", "restart", "start", "exit", "stop"]


def test_a_session_that_has_not_traded_yet_is_measured_from_its_checkpoint(tmp_path):
    """Before the first bar there is no `last_bar`; the checkpoint's write time is the clock."""
    runs = tmp_path / "runs"
    run = write_checkpoint(runs, "warming-run", last_bar=None)
    os.utime(run / "live_state.json", (1_700_000_000, 1_700_000_000))
    watch = Watch(1_700_000_005.0)
    session = Running(137)
    supervisor = watched(
        tmp_path, runs, watch, lambda argv, log: session, max_failures=1
    )
    assert supervisor.run() == EXIT_GAVE_UP
    stalled = records(tmp_path)[1]
    assert stalled["reason"] == "stalled" and stalled["silent_seconds"] == 301.0
    assert session.killed


def test_a_session_that_keeps_writing_bars_is_never_flagged(tmp_path):
    """A moving checkpoint is progress: this session runs 1000 s, far past the 300 s window."""
    runs = tmp_path / "runs"
    run = write_checkpoint(runs, "trading-run", last_bar=1_700_000_000, bars_traded=3)
    path = run / "live_state.json"
    watch = Watch(1_700_000_000.0)

    def sleep(seconds):
        watch.sleep(seconds)
        state = json.loads(path.read_text())
        state["last_bar"] = int(watch.now) - 60  # the bar that closed while we slept
        state["bars_traded"] += 1
        path.write_text(json.dumps(state))

    session = Running(0, stop_after=1000)
    supervisor = Supervisor(
        session=["fake", "session"],
        log=tmp_path / RUN_LOG,
        runs=runs,
        clock=watch.clock,
        sleep=sleep,
        launch=lambda argv, log: session,
        watch_interval=1.0,
    )
    assert supervisor.run() == EXIT_CLEAN
    assert events(records(tmp_path)) == ["start", "exit", "stop"]
    assert session.killed is False


def bash_to_run_the_script():
    """A bash that can actually run this script, or None.

    ``bash`` is not one program. On Windows it is often the WSL launcher, which fails when no
    distribution is installed, so a candidate is only accepted once it has run a command with
    the arrays the script needs.
    """
    candidates = [
        os.environ.get("FLYVSLY_BASH"),
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
        shutil.which("sh"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "a=(ok); printf %s ${a[0]}"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except OSError:
            continue
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return candidate
    return None


def test_the_script_supervises_a_real_child_process(tmp_path):
    """The operator runs the script, not the module: a crash once, then a clean exit."""
    bash = bash_to_run_the_script()
    if bash is None:
        pytest.skip("no usable bash on this machine to run scripts/live-supervisor.sh")
    root = Path(__file__).resolve().parents[1]
    mark = tmp_path / "crashed-once"
    session = (
        'fake_session() { echo "fake session argv: $*"; '
        'if [ -e "$FLYVSLY_FAKE_MARK" ]; then echo "fake session: clean exit"; exit 0; '
        'else echo "fake session: crash"; : > "$FLYVSLY_FAKE_MARK"; exit 7; fi; }; '
        "fake_session"
    )
    # The script resolves the shell for the override itself, so nothing here has to fix PATH:
    # whatever `bash` this test found is the one the script's own bash is.
    env = {
        **os.environ,
        "FLYVSLY_PYTHON": sys.executable,
        "FLYVSLY_SESSION": session,
        "FLYVSLY_FAKE_MARK": str(mark),
    }
    log = tmp_path / "supervisor.jsonl"
    session_log = tmp_path / "session.log"
    proc = subprocess.run(
        [
            bash,
            "scripts/live-supervisor.sh",
            "--runs",
            str(tmp_path / "runs"),
            "--log",
            str(log),
            "--session-log",
            str(session_log),
            "--backoff",
            "0",
            "--engine",
            "procedural",
            "--poll",
            "15",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == EXIT_CLEAN, proc.stderr
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert events(rows) == ["start", "exit", "restart", "start", "exit", "stop"]
    assert [row["exit_code"] for row in rows if row["event"] == "exit"] == [7, 0]
    restart = rows[2]
    assert restart["attempt"] == 2 and restart["failures"] == 1 and restart["backoff_seconds"] == 0.0
    assert rows[-1]["reason"] == "exit_zero"
    assert mark.exists()  # the fake really did crash once and then run again
    session_text = session_log.read_text()
    assert "fake session: crash" in session_text and "fake session: clean exit" in session_text
    # The session's own flags reached the child, which is the pass-through the script promises.
    assert "fake session argv: --engine procedural --poll 15" in session_text


def test_the_script_builds_the_default_session_and_honours_a_stop_file(tmp_path):
    """The default command is `flyvsly live`, with the runs dir and stop file wired in."""
    bash = bash_to_run_the_script()
    if bash is None:
        pytest.skip("no usable bash on this machine to run scripts/live-supervisor.sh")
    root = Path(__file__).resolve().parents[1]
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "live.stop").write_text("")
    log = tmp_path / "supervisor.jsonl"
    proc = subprocess.run(
        [
            bash,
            "scripts/live-supervisor.sh",
            "--runs",
            str(runs),
            "--log",
            str(log),
            "--engine",
            "procedural",
            "--source",
            "kraken",
            "--poll",
            "15",
        ],
        cwd=root,
        env={**os.environ, "FLYVSLY_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == EXIT_CLEAN, proc.stderr
    assert (
        f"-m flyvsly live --runs {runs} --stop-file {runs}/live.stop "
        "--engine procedural --source kraken --poll 15"
    ) in proc.stdout
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert events(rows) == ["stop"] and rows[0]["reason"] == "stop_file"


def test_the_script_refuses_a_flag_without_a_value():
    bash = bash_to_run_the_script()
    if bash is None:
        pytest.skip("no usable bash on this machine to run scripts/live-supervisor.sh")
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [bash, "scripts/live-supervisor.sh", "--log"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "--log needs a value" in proc.stderr


def test_the_script_continues_the_session_its_child_wrote(tmp_path):
    """The resume path through the script, not only through the module.

    The fake session is a whole `flyvsly live` here: its first run leaves a running checkpoint
    the way a killed session does, and its second run only succeeds if the supervisor hands it
    `--resume <that run>`.
    """
    bash = bash_to_run_the_script()
    if bash is None:
        pytest.skip("no usable bash on this machine to run scripts/live-supervisor.sh")
    root = Path(__file__).resolve().parents[1]
    runs = tmp_path / "runs"
    session = (
        "fake_session() { "
        'echo "fake session argv: $*"; '
        'case " $* " in '
        '*" --resume fake-run "*) echo "fake session: continued fake-run"; exit 0 ;; '
        "esac; "
        'mkdir -p "$FLYVSLY_FAKE_RUNS/fake-run"; '
        "printf %s '{\"run_id\":\"fake-run\",\"status\":\"running\",\"bar_seconds\":60,"
        "\"warmup_bars\":120,\"session_opened\":1700000000,\"bars_traded\":1,"
        "\"last_bar\":1700000000}' > \"$FLYVSLY_FAKE_RUNS/fake-run/live_state.json\"; "
        'echo "fake session: crash after one bar"; exit 7; }; '
        "fake_session"
    )
    env = {
        **os.environ,
        "FLYVSLY_PYTHON": sys.executable,
        "FLYVSLY_SESSION": session,
        "FLYVSLY_FAKE_RUNS": str(runs),
    }
    log = tmp_path / "supervisor.jsonl"
    session_log = tmp_path / "session.log"
    proc = subprocess.run(
        [
            bash,
            "scripts/live-supervisor.sh",
            "--runs",
            str(runs),
            "--log",
            str(log),
            "--session-log",
            str(session_log),
            "--backoff",
            "0",
            "--engine",
            "procedural",
            "--poll",
            "15",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == EXIT_CLEAN, proc.stderr
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert events(rows) == ["start", "exit", "restart", "start", "exit", "stop"]
    restart = rows[2]
    assert restart["mode"] == "resume" and restart["run_id"] == "fake-run"
    started_again = rows[3]
    assert started_again["mode"] == "resume"
    assert started_again["command"][-2:] == ["--resume", "fake-run"]
    assert (runs / "fake-run" / "live_state.json").exists()
    session_text = session_log.read_text()
    assert "fake session: crash after one bar" in session_text
    assert "fake session: continued fake-run" in session_text
