"""Supervision: a live session that dies comes back, and every decision is written down.

These tests pin the policy rather than the plumbing. An expected end is not a failure and must
not be retried; a crash must be, with a wait that grows and then stops growing; the ladder must
end in a give-up that says so and exits non-zero; and a run that lasted long enough to prove the
venue and the graph work must clear the ladder. Nothing here touches a clock, a market or the
network: the launcher, the clock and the sleep are the supervisor's own arguments.

The last test drives scripts/live-supervisor.sh, because the script is what an operator runs.
"""

import json
import os
import shutil
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
        Supervisor(session=[], log=tmp_path / RUN_LOG).run()


def test_the_log_is_append_only_and_every_record_is_timestamped(tmp_path):
    fake = Fake(codes=[0])
    supervised(tmp_path, fake).run()
    supervised(tmp_path, fake).run()
    rows = records(tmp_path)
    assert events(rows) == ["start", "exit", "stop"] * 2
    assert [row["attempt"] for row in rows if row["event"] == "start"] == [1, 1]
    assert all(row["ts"].endswith("Z") for row in rows)


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
