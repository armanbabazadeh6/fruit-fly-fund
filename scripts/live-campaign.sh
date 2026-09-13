#!/usr/bin/env bash
# Repeat live sessions until there is more than one to pool: M sessions of K bars, unattended.
#
# `flyvsly live-report` pools live sessions, but one session is a single path through a single
# stretch of market. This runs M of them back to back under `scripts/live-supervisor.sh`,
# stopping each after K traded bars, then writes the same pooled report. Each session is a real
# session with `--brain-every`, so a crash becomes a restart rather than a lost run.
#
# What M sessions buy is honest and small: M paths through minutes of *one* market are still one
# market, and M is a handful. The point is to shake out the pipeline and give the pooled report
# more than one row, not to settle whether the memory arm is better. Read the report's own
# single-market caveat before reading anything into its leader.
#
# Usage: scripts/live-campaign.sh [sessions] [bars]
#
# Environment overrides:
#   ENGINE=neural|procedural   engine for every session (default neural)
#   PRODUCT=BTC-USDC           product every session trades (default BTC-USDC)
#   BRAIN_EVERY=BARS           --brain-every for every session (default 30; 0 disables)
#   RUNS=DIR                   where sessions are written (default runs)
#   OUT=FILE                   pooled table (default results/live-campaign.md)
#   SUPERVISOR=PATH            wrapper each session runs under (default scripts/live-supervisor.sh)
#   WATCH_SECONDS=SECONDS      how often the campaign reads a session's checkpoint (default 5)
#
# --dry-run prints the plan and the exact supervisor command for each session without starting
# one. -h, --help prints this block.
#
# Interruptible: Ctrl-C touches the session's stop file, lets the bar in flight finish, and
# exits 130. A lock in RUNS (`live-campaign.lock`) refuses a second campaign, and a session
# that is already running is refused before any new one starts: two sessions over the same
# minutes would make neither a clean experiment.
#
# Paper only. Nothing here can place an order.
set -euo pipefail

# Run from the repo root whatever the caller's directory was: `--runs`, the stop file and the
# report are all written relative to it, and `python -m flyvsly` finds this checkout.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [ -n "${FLYVSLY_PYTHON:-}" ]; then PY="$FLYVSLY_PYTHON"
elif [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then PY=".venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then PY="python3"
else PY="python"; fi

# The comment block at the top of this file is the help text, so there is one copy of it.
usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}"; }

SESSIONS=""
BARS=""
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    *) if [ -z "$SESSIONS" ]; then SESSIONS="$1"; elif [ -z "$BARS" ]; then BARS="$1"; else
         echo "too many arguments: $1 (try --help)" >&2; exit 2
       fi; shift ;;
  esac
done

SESSIONS="${SESSIONS:-3}"
BARS="${BARS:-120}"
ENGINE="${ENGINE:-neural}"
PRODUCT="${PRODUCT:-BTC-USDC}"
BRAIN_EVERY="${BRAIN_EVERY:-30}"
RUNS="${RUNS:-runs}"
OUT="${OUT:-results/live-campaign.md}"
SUPERVISOR="${SUPERVISOR:-scripts/live-supervisor.sh}"
WATCH_SECONDS="${WATCH_SECONDS:-5}"

case "$SESSIONS" in ''|*[!0-9]*) echo "sessions must be a positive integer, got '$SESSIONS'" >&2; exit 2 ;; esac
case "$BARS" in ''|*[!0-9]*) echo "bars must be a positive integer, got '$BARS'" >&2; exit 2 ;; esac
case "$WATCH_SECONDS" in ''|*[!0-9]*) echo "WATCH_SECONDS must be a positive integer, got '$WATCH_SECONDS'" >&2; exit 2 ;; esac
[ "$SESSIONS" -ge 1 ] || { echo "sessions must be at least 1" >&2; exit 2; }
[ "$BARS" -ge 1 ] || { echo "bars must be at least 1" >&2; exit 2; }
[ "$WATCH_SECONDS" -ge 1 ] || { echo "WATCH_SECONDS must be at least 1" >&2; exit 2; }

STAMP="$(date +%Y%m%d-%H%M%S)"
CAMPAIGN="live-campaign-$STAMP"
STOP_FILE="$RUNS/$CAMPAIGN.stop"
LOCK="$RUNS/live-campaign.lock"
LOG_DIR="$RUNS/$CAMPAIGN"

# The minutes of market the campaign will occupy, at a 60 s bar and one session at a time.
MINUTES=$(( SESSIONS * BARS ))
echo "live campaign: ${SESSIONS} session(s) x ${BARS} bars, engine ${ENGINE}, product ${PRODUCT}"
echo "real time: about ${MINUTES} minute(s) of uptime, one session at a time"
echo "this is a small sample: ${SESSIONS} path(s) through one market is a pipeline check, not evidence"
echo "sessions under $LOG_DIR; stop file $STOP_FILE"

if [ "$DRY" = 1 ]; then
  for (( i=1; i<=SESSIONS; i++ )); do
    echo "would run: $SUPERVISOR --runs $RUNS --stop-file $STOP_FILE" \
         "--log $LOG_DIR/session-$i.supervisor.jsonl --session-log $LOG_DIR/session-$i.session.log" \
         "--engine $ENGINE --product $PRODUCT --brain-every $BRAIN_EVERY --label $CAMPAIGN-$i"
    echo "  then watch $RUNS/*/live_state.json until bars_traded >= $BARS, touch the stop file, wait"
  done
  echo "would finish with: $PY -m flyvsly live-report --runs $RUNS --table --write $OUT"
  echo "dry run: nothing started"
  exit 0
fi

# Refuse a second campaign: its lock is the campaign that is running, not a leftover file.
if [ -e "$LOCK" ]; then
  OTHER="$(cat "$LOCK" 2>/dev/null || true)"
  if [ -n "$OTHER" ] && kill -0 "$OTHER" 2>/dev/null; then
    echo "another live campaign (pid $OTHER) is running; refusing to start a second session" >&2
    exit 1
  fi
  echo "removing stale campaign lock $LOCK"
  rm -f "$LOCK"
fi

# A session started outside this campaign is just as disqualifying as a second campaign: the
# two would trade the same minutes and neither record would be clean.
if command -v pgrep >/dev/null 2>&1 && pgrep -f -- '-m flyvsly live($| )' >/dev/null 2>&1; then
  echo "a live session is already running; refusing to start another" >&2
  exit 1
fi

mkdir -p "$LOG_DIR" "$(dirname -- "$OUT")"
printf '%s\n' "$$" > "$LOCK"

INTERRUPTED=0
on_interrupt() {
  INTERRUPTED=1
  echo ""
  echo "interrupted: asking the running session to stop after this bar"
  touch "$STOP_FILE"
}
cleanup() {
  rm -f "$LOCK" "$STOP_FILE"
}
trap on_interrupt INT TERM
trap cleanup EXIT

# The newest checkpoint written since the session started (the marker argument), or nothing.
# A crashed session that the supervisor restarts writes a newer one, so the watch follows the
# live session rather than a run id the campaign cannot know in advance.
newest_checkpoint() {
  local marker="$1" path newest=""
  for path in "$RUNS"/*/live_state.json; do
    [ -e "$path" ] || continue
    [ "$path" -nt "$marker" ] || continue
    if [ -z "$newest" ] || [ "$path" -nt "$newest" ]; then newest="$path"; fi
  done
  [ -n "$newest" ] && printf '%s\n' "$newest"
}

bars_traded() {
  "$PY" -c 'import json,sys
try:
    print(int(json.load(open(sys.argv[1], encoding="utf-8")).get("bars_traded") or 0))
except Exception:
    print(0)' "$1" 2>/dev/null || echo 0
}

# Stop the session once its own checkpoint records `target` traded bars. Runs in the
# background so the campaign's main loop can be interrupted at any moment rather than only
# when the session happens to exit; the one-second sleeps are what make Ctrl-C land promptly.
watch_for_target() {
  local target="$1" marker="$2" supervisor="$3" tick=0 asked=0 state bars
  while kill -0 "$supervisor" 2>/dev/null; do
    if [ "$tick" -le 0 ]; then
      tick="$WATCH_SECONDS"
      if [ "$asked" = 0 ]; then
        state="$(newest_checkpoint "$marker" || true)"
        if [ -n "$state" ]; then
          bars="$(bars_traded "$state")"
          if [ "$bars" -ge "$target" ]; then
            echo "  target reached: $bars bars; asking the session to stop"
            touch "$STOP_FILE"
            asked=1
          fi
        fi
      fi
    fi
    tick=$((tick - 1))
    sleep 1
  done
}

FINISHED=0
for (( i=1; i<=SESSIONS; i++ )); do
  [ "$INTERRUPTED" = 0 ] || break
  rm -f "$STOP_FILE"
  MARKER="$LOG_DIR/.session-$i.started"
  : > "$MARKER"
  LOG="$LOG_DIR/session-$i.supervisor.jsonl"
  SESSION_LOG="$LOG_DIR/session-$i.session.log"
  LABEL="$CAMPAIGN-$i"

  echo "session $i/$SESSIONS: starting (label $LABEL)"
  "$SUPERVISOR" --runs "$RUNS" --stop-file "$STOP_FILE" --log "$LOG" --session-log "$SESSION_LOG" \
    --engine "$ENGINE" --product "$PRODUCT" --brain-every "$BRAIN_EVERY" --label "$LABEL" &
  SUPERVISOR_PID=$!
  watch_for_target "$BARS" "$MARKER" "$SUPERVISOR_PID" &
  WATCHER_PID=$!

  CODE=0
  wait "$SUPERVISOR_PID" || CODE=$?
  wait "$WATCHER_PID" 2>/dev/null || true

  if [ "$INTERRUPTED" = 1 ]; then
    # A signal can land between bars. Make sure the session is ending before leaving it.
    touch "$STOP_FILE"
    wait "$SUPERVISOR_PID" 2>/dev/null || true
    break
  fi

  if [ "$CODE" -eq 2 ]; then
    echo "session $i: the supervisor gave up after consecutive crashes; see $LOG" >&2
    exit 1
  elif [ "$CODE" -ne 0 ] && [ "$CODE" -ne 130 ]; then
    echo "session $i ended with supervisor exit $CODE; see $LOG" >&2
    exit 1
  fi
  STATE="$(newest_checkpoint "$MARKER" || true)"
  BARS_SEEN=0
  [ -n "$STATE" ] && BARS_SEEN="$(bars_traded "$STATE")"
  if [ "$BARS_SEEN" -lt "$BARS" ]; then
    echo "session $i ended at $BARS_SEEN of $BARS bars; the supervisor log is the record" >&2
  else
    FINISHED=$((FINISHED + 1))
  fi
done

if [ "$INTERRUPTED" = 1 ]; then
  echo "campaign interrupted after $FINISHED complete session(s); nothing is pooled" >&2
  exit 130
fi

echo "campaign complete: $FINISHED session(s) traded $BARS bars each"
"$PY" -m flyvsly live-report --runs "$RUNS" --table --write "$OUT"
echo "pooled report: $OUT"
echo "remember: $FINISHED session(s) through one market is a small sample; the report's own"
echo "single-market caveat stands."
