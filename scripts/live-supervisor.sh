#!/usr/bin/env bash
# Run a live paper session under supervision, so a crash becomes a restart instead of an outage.
#
# The session itself is `flyvsly live`; this wraps it in `flyvsly.supervise`, which restarts it
# with a bounded backoff when it dies unexpectedly — continuing the run it was writing when the
# checkpoint allows that, and starting a new one when it does not — ends and restarts a session
# that is alive but has written no new bar for `--stall-bars` bar lengths, stops for good when
# the session stops cleanly or the stop file appears, gives up after a run of crashes, and
# appends one JSON Lines record for every decision. The policy is in flyvsly/supervise.py.
#
# Usage:
#   scripts/live-supervisor.sh [supervisor options] [session flags...]
#
# Supervisor options (everything else goes to the session):
#   --runs DIR                 where sessions are written and read back (default: runs)
#   --log FILE                 append-only JSON Lines supervisor log
#                              (default: <runs>/live-supervisor.jsonl)
#   --stop-file FILE           stop when this file appears (default: <runs>/live.stop)
#   --session-log FILE         append the session's own output here
#                              (default: <runs>/live-supervisor.session.log)
#   --max-failures N           give up after N consecutive crashes (default: 5)
#   --backoff SECONDS          wait before the first retry, doubling each time (default: 5)
#   --backoff-ceiling SECONDS  longest wait between retries (default: 300)
#   --healthy-seconds SECONDS  a run this long clears the crash ladder (default: 600)
#   --stall-bars N             bar lengths without a new bar before a running session is
#                              treated as failed and restarted (default: 5)
#   -h, --help
#
# Any other argument is passed through to the session verbatim, so the session's own flags work
# exactly as they do on its command line:
#   scripts/live-supervisor.sh --engine procedural --source kraken --product BTC-USDC --poll 15
# `--` stops the supervisor's parsing; everything after it is passed through as well, which is
# the escape hatch for a flag this script's parser could ever mistake for one of its own.
#
# The default session is `<python> -m flyvsly live`. Set FLYVSLY_SESSION to a shell command to
# supervise something else — the same session in Docker, for instance:
#   FLYVSLY_SESSION="docker run --rm -v $PWD:/app flyvsly live --engine neural" \
#     scripts/live-supervisor.sh
# The supervisor reads the session's checkpoints under `--runs`, so an override has to write its
# run directories there too: continuing a killed session and noticing a silent one both read
# what the session wrote.
#
# Paper only. Nothing here can place an order.
set -euo pipefail

# Run from the repo root whatever the caller's directory was: the session's `--runs`, the stop
# file and the log are all written relative to it, and `python -m flyvsly` finds this checkout.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [ -n "${FLYVSLY_PYTHON:-}" ]; then PY="$FLYVSLY_PYTHON"
elif [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then PY=".venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then PY="python3"
else PY="python"; fi

# The comment block at the top of this file is the help text, so there is one copy of it.
usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}"; }

need() {
  if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
    echo "$1 needs a value" >&2
    exit 2
  fi
}

RUNS="runs"
LOG=""
STOP_FILE=""
SESSION_LOG=""
MAX_FAILURES=""
BACKOFF=""
BACKOFF_CEILING=""
HEALTHY=""
STALL_BARS=""
SESSION=()

while [ $# -gt 0 ]; do
  case "$1" in
    --runs) need "$@"; RUNS="$2"; shift 2 ;;
    --log) need "$@"; LOG="$2"; shift 2 ;;
    --stop-file) need "$@"; STOP_FILE="$2"; shift 2 ;;
    --session-log) need "$@"; SESSION_LOG="$2"; shift 2 ;;
    --max-failures) need "$@"; MAX_FAILURES="$2"; shift 2 ;;
    --backoff) need "$@"; BACKOFF="$2"; shift 2 ;;
    --backoff-ceiling) need "$@"; BACKOFF_CEILING="$2"; shift 2 ;;
    --healthy-seconds) need "$@"; HEALTHY="$2"; shift 2 ;;
    --stall-bars) need "$@"; STALL_BARS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; SESSION+=("$@"); break ;;
    *) SESSION+=("$1"); shift ;;
  esac
done

LOG="${LOG:-$RUNS/live-supervisor.jsonl}"
STOP_FILE="${STOP_FILE:-$RUNS/live.stop}"
SESSION_LOG="${SESSION_LOG:-$RUNS/live-supervisor.session.log}"

SUPERVISOR=(--log "$LOG" --stop-file "$STOP_FILE" --session-log "$SESSION_LOG" --runs "$RUNS")
if [ -n "$MAX_FAILURES" ]; then SUPERVISOR+=(--max-failures "$MAX_FAILURES"); fi
if [ -n "$BACKOFF" ]; then SUPERVISOR+=(--backoff "$BACKOFF"); fi
if [ -n "$BACKOFF_CEILING" ]; then SUPERVISOR+=(--backoff-ceiling "$BACKOFF_CEILING"); fi
if [ -n "$HEALTHY" ]; then SUPERVISOR+=(--healthy-seconds "$HEALTHY"); fi
if [ -n "$STALL_BARS" ]; then SUPERVISOR+=(--stall-bars "$STALL_BARS"); fi

if [ -n "${FLYVSLY_SESSION:-}" ]; then
  # The override is a whole command line, run by the shell. `bash` on PATH is not good enough
  # to spawn: the supervisor starts it as a native process, where Windows' PATH order can pick
  # the WSL launcher. This shell knows its own bash, and cygpath (Git for Windows) turns that
  # into the native path a spawned process needs. Session flags given here are appended to the
  # override, so the Docker form takes the same flags as the default form.
  if [ -n "${BASH:-}" ]; then
    if command -v cygpath >/dev/null 2>&1; then SESSION_SHELL="$(cygpath -w "$BASH")"; else SESSION_SHELL="$BASH"; fi
  else
    SESSION_SHELL="bash"
  fi
  LAUNCH=("$SESSION_SHELL" -c "$FLYVSLY_SESSION \"\$@\"" _)
  if [ ${#SESSION[@]} -gt 0 ]; then LAUNCH+=("${SESSION[@]}"); fi
else
  LAUNCH=("$PY" -m flyvsly live --runs "$RUNS" --stop-file "$STOP_FILE")
  if [ ${#SESSION[@]} -gt 0 ]; then LAUNCH+=("${SESSION[@]}"); fi
fi

echo "supervising: ${LAUNCH[*]}"
echo "log:         $LOG"
echo "session log: $SESSION_LOG"
echo "stop with:   touch $STOP_FILE"

# `exec` keeps Ctrl-C and the exit code direct: 0 when the session ended expectedly, 2 when the
# supervisor gave up, 130 when the interrupt reached the supervisor rather than the session.
exec "$PY" -m flyvsly.supervise "${SUPERVISOR[@]}" -- "${LAUNCH[@]}"
