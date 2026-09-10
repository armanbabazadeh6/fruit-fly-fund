#!/usr/bin/env bash
# The full experiment: train, exam, reset, and a readout fitted from the fly's own brain.
#
# The question each stage answers:
#   train    - let the memory rule learn on two real market seasons.
#   exam     - freeze both flies and run them on a season neither has seen. One carries the
#              trained weights, one carries the reconstructed baseline. If the trained fly
#              does not beat the fresh one, the memory did not generalise. Nothing is learned
#              during an exam, so there is no answer key in the room.
#   reset    - the same exam, but the trained fly's learned efficacies are wiped back to
#              baseline first. This is the control for "the difference lives in the weights".
#   readout  - fit a small model on the fly's own recorded population vectors and let it
#              decide instead of the fixed DNp20 threshold. The model is shared by both
#              flies; each supplies its own activity. Reported with its base rate, because a
#              model that cannot beat guessing must say so.
#
# Usage: scripts/experiment.sh [bars] [train_seasons] [preset]
set -euo pipefail

if [ -n "${FLYVSLY_PYTHON:-}" ]; then PY="$FLYVSLY_PYTHON"
elif [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then PY="python3"
else PY="python"; fi

BARS="${1:-48}"
TRAIN_SEASONS="${2:-2}"
PRESET="${3:-scalper}"
BRAINS="runs/brains/train"
MODEL="models/readout.json"
# Seasons are windows stepping back one at a time; training takes the newest ones.
EXAM_OFFSET=$(( BARS * TRAIN_SEASONS ))

echo "== train (${TRAIN_SEASONS} seasons, preset ${PRESET})"
"$PY" -m flyvsly run --engine neural --market coinbase --bars "$BARS" --repeats "$TRAIN_SEASONS" \
  --preset "$PRESET" --reinforcement pnl --window-offset 0 \
  --label train --save-brains "$BRAINS" --out runs

echo "== fit a readout from the control arm's own activity"
"$PY" -m flyvsly fitreadout --runs runs --label train --arm warren --out "$MODEL" --horizon 1

echo "== exam on a season neither fly has seen (both frozen, fixed DNp20 rule)"
"$PY" -m flyvsly run --engine neural --market coinbase --bars "$BARS" --repeats 1 \
  --preset "$PRESET" --kind exam --window-offset "$EXAM_OFFSET" \
  --starting "gordon=trained:${BRAINS}/gordon.npz" --starting "warren=baseline" \
  --label exam-fixed --out runs

echo "== reset control: same exam with the learned efficacies wiped"
"$PY" -m flyvsly run --engine neural --market coinbase --bars "$BARS" --repeats 1 \
  --preset "$PRESET" --kind reset --window-offset "$EXAM_OFFSET" \
  --starting "gordon=reset:${BRAINS}/gordon.npz" --starting "warren=baseline" \
  --label exam-reset --out runs

echo "== exam again, this time decided by the fitted readout"
"$PY" -m flyvsly run --engine neural --market coinbase --bars "$BARS" --repeats 1 \
  --preset "$PRESET" --kind exam --window-offset "$EXAM_OFFSET" --readout "$MODEL" \
  --starting "gordon=trained:${BRAINS}/gordon.npz" --starting "warren=baseline" \
  --label exam-readout --out runs

"$PY" -m flyvsly report --runs runs --table --write results/report.md
echo "done; see results/report.md"
