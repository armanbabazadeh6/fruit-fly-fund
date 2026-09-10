"""The readout population must be picked deterministically and extracted in step."""

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import flyvsly.population as population

# Non-contiguous ids so that a position (index into the brain's arrays) can never be
# mistaken for a body id, and the reverse.
IDS = np.arange(1000, 1000 + 3 * 12, 3, dtype=np.int64)

TYPES = [
    "KCg-m",    # 0  Kenyon cell, only via the fill tier
    "DNp20",    # 1  descending and a decoder cell
    "MBON01",   # 2  memory output
    "PAM11",    # 3  dopaminergic
    "other",    # 4  no tier matches
    "DNpe017",  # 5  descending and a decoder cell
    "KCab",     # 6  Kenyon
    "KCg-s",    # 7  Kenyon
    "DNp09",    # 8  descending
    "PPL101",   # 9  dopaminergic
    "KCg-m",    # 10 Kenyon
    "MBON04",   # 11 memory output
]


def make_brain(types=TYPES, ids=IDS):
    return SimpleNamespace(n=len(types), ids=ids, circuit={"kc": np.arange(len(types))})


def install(monkeypatch, brain, types=TYPES, order=None):
    """Return the annotations frame keyed by body id, optionally row-shuffled."""
    index = list(brain.ids)
    frame = pd.DataFrame(
        {
            "type": list(types),
            "instance": ["i"] * len(types),
            "somaSide": ["L"] * len(types),
        },
        index=index,
    )
    if order is not None:
        frame = frame.iloc[order]
    monkeypatch.setattr(population, "annotations", lambda ids: frame)
    return frame


def test_selection_is_deterministic_sorted_and_duplicate_free(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain)
    first = population.select(brain, sample=8)
    second = population.select(brain, sample=8)

    assert np.array_equal(first["indices"], second["indices"])
    assert first["indices"].dtype == np.int32
    indices = [int(i) for i in first["indices"]]
    assert indices == sorted(indices)
    assert len(indices) == len(set(indices))

    # ids and types correspond exactly to the indices, in order.
    assert first["ids"] == [str(int(brain.ids[i])) for i in indices]
    assert first["types"] == [TYPES[i] for i in indices]
    assert first["truncated"] is False


def test_priority_tiers_choose_membership(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain)
    selection = population.select(brain, sample=8)
    # descending (1,5,8) then MBONs (2,11) then DANs (3,9) then one lowest-index KC (0).
    assert [int(i) for i in selection["indices"]] == [0, 1, 2, 3, 5, 8, 9, 11]
    assert "other" not in selection["types"]
    assert selection["groups"]["descending"] == 3
    assert selection["groups"]["mbon"] == 2
    assert selection["groups"]["dan"] == 2
    assert selection["groups"]["decoder"] == 2  # overlaps "descending", by design
    assert selection["groups"]["kc"] == 1


def test_cap_favours_earlier_tiers(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain)
    selection = population.select(brain, sample=4)
    # The four slots go to the three descending cells and the first MBON, not to DANs.
    assert [int(i) for i in selection["indices"]] == [1, 2, 5, 8]
    assert selection["truncated"] is False
    assert selection["groups"]["dan"] == 0


def test_annotation_lookup_is_matched_by_id_not_row_order(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain, order=list(reversed(range(len(TYPES)))))
    shuffled = population.select(brain, sample=8)
    assert shuffled["types"] == [TYPES[i] for i in (0, 1, 2, 3, 5, 8, 9, 11)]


def test_extract_returns_counts_in_selection_order(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain)
    selection = population.select(brain, sample=8)
    counts = np.asarray([7 * i + 3 for i in range(brain.n)], dtype=np.int32)
    values = population.extract(counts, selection)
    assert values == [int(7 * i + 3) for i in selection["indices"]]
    assert all(type(value) is int for value in values)


def test_extract_rejects_counts_shorter_than_the_selection(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain)
    selection = population.select(brain, sample=8)
    with pytest.raises(ValueError):
        population.extract(np.zeros(5, dtype=np.int32), selection)


def test_fewer_candidates_than_sample_returns_everything(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain)
    selection = population.select(brain, sample=20)
    assert selection["truncated"] is True
    assert selection["sample"] == 20
    # Every cell that matches any tier: 3 descending + 2 MBON + 2 DAN + 4 KC.
    assert [int(i) for i in selection["indices"]] == [0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11]
    assert int(selection["indices"].max()) + 1 == brain.n


def test_truncated_only_when_fewer_than_sample(monkeypatch):
    ids = np.arange(50, 50 + 4 * 8, 4, dtype=np.int64)
    brain = make_brain(types=["KCg-m"] * 8, ids=ids)
    install(monkeypatch, brain, types=["KCg-m"] * 8)

    exact = population.select(brain, sample=8)
    assert len(exact["indices"]) == 8
    assert exact["truncated"] is False

    one_short = population.select(brain, sample=9)
    assert len(one_short["indices"]) == 8
    assert one_short["truncated"] is True


def test_no_matching_cells_returns_empty(monkeypatch):
    types = ["LA-a", "LA-b", "OA-a", "OA-b"]
    ids = np.arange(10, 10 + 5 * 4, 5, dtype=np.int64)
    brain = make_brain(types=types, ids=ids)
    install(monkeypatch, brain, types=types)

    selection = population.select(brain, sample=4)
    assert len(selection["indices"]) == 0
    assert selection["ids"] == []
    assert selection["types"] == []
    assert selection["truncated"] is True
    assert set(selection["groups"].values()) == {0}
    assert population.extract(np.zeros(4, dtype=np.int32), selection) == []


def test_default_sample_is_recorded(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain)
    selection = population.select(brain)
    assert selection["sample"] == population.DEFAULT_SAMPLE


def test_describe_is_json_serialisable_and_names_the_priority_order(monkeypatch):
    brain = make_brain()
    install(monkeypatch, brain)
    selection = population.select(brain, sample=8)
    described = population.describe(selection)

    assert json.loads(json.dumps(described)) == described
    assert described["schema"] == population.SCHEMA_VERSION
    assert described["size"] == len(selection["indices"])
    assert described["sample"] == 8
    assert described["truncated"] is False
    assert described["ids"] == selection["ids"]
    assert set(described["groups"]) == {"descending", "mbon", "dan", "decoder", "kc"}
    assert "descending" in described["selection"]
