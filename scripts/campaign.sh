#!/usr/bin/env bash
# A full campaign: every reinforcement mode over the same disjoint seasons, with the rules
# identical for both flies inside each run.
#
# The modes answer different questions:
#   pnl       - upstream's rule. The pulse follows the fly's own profit and loss.
#   decoy     - the same pulse statistics, driven by the benchmark instead of the fly, so
#               nothing about the fly's actions is contingent on the reward.
#   shuffled  - a seeded permutation of the pnl arm's own pulse schedule: identical pulses,
#               delivered at unrelated times.
#   none      - no pulse at all: does the memory rule move weights without any feedback?
#
# Usage: scripts/campaign.sh [seasons] [bars] [preset]
set -euo pipefail

# Use the project virtualenv when there is one: `python` is not on PATH everywhere, and this
# script must work when called from a scheduler or an agent as well as from a terminal.
if [ -n "${FLYVSLY_PYTHON:-}" ]; then
  PY="$FLYVSLY_PYTHON"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

SEASONS="${1:-3}"
BARS="${2:-48}"
PRESET="${3:-active}"
PNL_LABEL="campaign-${PRESET}-pnl"

echo "campaign: ${SEASONS} seasons x 4 modes, ${BARS} bars, preset ${PRESET}"

"$PY" -m flyvsly run --engine neural --market coinbase --bars "$BARS" --repeats "$SEASONS" \
  --preset "$PRESET" --reinforcement pnl --label "$PNL_LABEL" --out runs

for MODE in decoy none; do
  "$PY" -m flyvsly run --engine neural --market coinbase --bars "$BARS" --repeats "$SEASONS" \
    --preset "$PRESET" --reinforcement "$MODE" --label "campaign-${PRESET}-${MODE}" --out runs
done

# The shuffled arm needs the pnl schedule to permute, and it refers to it by label because a
# campaign cannot know its own run ids in advance.
"$PY" -m flyvsly run --engine neural --market coinbase --bars "$BARS" --repeats "$SEASONS" \
  --preset "$PRESET" --reinforcement shuffled --shuffle-reference "$PNL_LABEL" \
  --shuffle-seed 11 --label "campaign-${PRESET}-shuffled" --out runs

"$PY" -m flyvsly report --runs runs --table --write results/report.md
echo "campaign complete; see results/report.md"
