"""Which of the fly's own neurons the readout model gets to see.

The trade decision still comes from the two-cell DNp20 engine readout. This module picks a
few hundred of the fly's other cells and pulls their spike counts out of the same
observation, so a small readout model can later be fitted from the brain's own activity
rather than from the engineered DNp20 interface.

Selection is deterministic and depends only on the connectome annotations, never on RNG,
so a recording replays identically. It returns positions into the brain's own arrays; the
caller must not reorder them, because `extract` relies on that correspondence.
"""

import numpy as np
import pandas as pd
from stonkfly.neural.common import annotations

DEFAULT_SAMPLE = 256
SCHEMA_VERSION = "flyvsly.population/v1"

# Priority order. Each entry is (group, predicate on the annotated cell type), and the
# earlier a cell matches, the sooner it claims a slot: descending neurons are what the
# brain sends out, MBONs report what memory retrieved, DANs gate learning, and Kenyon
# cells are the only large population we can afford to fill the remainder with.
_TIERS = (
    ("descending", lambda text: text.startswith("DN")),
    ("mbon", lambda text: text.startswith("MBON")),
    (
        "dan",
        lambda text: text in ("PAM11", "PPL101")
        or text.startswith("PAM")
        or text.startswith("PPL"),
    ),
    ("decoder", lambda text: text in ("DNp20", "DNpe017")),
    ("kc", lambda text: text.startswith("KC")),
)

# Stored once per recording in `describe`, so the readout's provenance travels with the
# numbers instead of living only in this file.
SELECTION_SENTENCE = (
    "Priority order: every annotated descending neuron (DN*), then every MBON*, then the "
    "dopaminergic DANs PAM11 and PPL101 (and any PAM*/PPL* type), then the decoder cells "
    "DNp20 and DNpe017, then the lowest-index KC* Kenyon cells until the sample is full."
)


def _annotated_types(brain) -> list[str]:
    """Annotated cell type per position in the brain's own arrays.

    `annotations` is keyed by body id, and the frame it returns may be in any row order,
    so cells are matched by id rather than by row position. A cell with no annotation
    becomes "" and matches no tier.
    """
    ids = [int(value) for value in brain.ids]
    frame = annotations(np.asarray(brain.ids))
    by_id = {
        int(body): "" if pd.isna(text) else str(text)
        for body, text in frame["type"].items()
    }
    return [by_id.get(body, "") for body in ids]


def select(brain, sample: int = DEFAULT_SAMPLE) -> dict:
    """Choose the cells whose spike counts the readout model will be fitted from.

    `indices` are positions into the brain's arrays, sorted ascending and unique, and
    `ids`/`types` line up with them one for one. With fewer candidates than `sample`,
    everything is returned and `truncated` is True; cells are never invented to pad.

    `groups` counts the selected cells per category. The categories can overlap (a DNp20
    is both a descending neuron and a decoder cell), so the counts need not sum to `size`.
    """
    sample = int(sample)
    if sample < 0:
        raise ValueError("sample must be non-negative")

    types = _annotated_types(brain)
    claimed: set[int] = set()
    chosen: list[int] = []
    for _name, predicate in _TIERS:
        for position, text in enumerate(types):
            if len(chosen) >= sample:
                break
            # Ascending positions, first tier wins: that is the whole priority order.
            if position in claimed or not predicate(text):
                continue
            claimed.add(position)
            chosen.append(position)
        if len(chosen) >= sample:
            break
    chosen.sort()

    groups = {
        name: int(sum(1 for position in chosen if predicate(types[position])))
        for name, predicate in _TIERS
    }
    return {
        "indices": np.asarray(chosen, dtype=np.int32),
        "ids": [str(int(brain.ids[position])) for position in chosen],
        "types": [types[position] for position in chosen],
        "groups": groups,
        "sample": sample,
        "truncated": len(chosen) < sample,
    }


def extract(counts, selection: dict) -> list[int]:
    """The selected spike counts, in selection order, as plain Python ints.

    `counts` is the brain's full spike-count array for one observation. Reading past its
    end means the selection came from a different brain, which must fail loudly.
    """
    indices = np.asarray(selection["indices"])
    if len(indices) == 0:
        return []
    highest = int(indices.max())
    if len(counts) <= highest:
        raise ValueError(
            f"counts has length {len(counts)} but selection references index {highest}"
        )
    return [int(counts[position]) for position in indices]


def describe(selection: dict) -> dict:
    """JSON-serialisable summary of a selection, stored once in each recording."""
    return {
        "schema": SCHEMA_VERSION,
        "size": int(len(selection["indices"])),
        "sample": int(selection["sample"]),
        "truncated": bool(selection["truncated"]),
        "groups": {name: int(count) for name, count in selection["groups"].items()},
        "ids": [str(value) for value in selection["ids"]],
        "types": [str(value) for value in selection["types"]],
        "selection": SELECTION_SENTENCE,
    }
