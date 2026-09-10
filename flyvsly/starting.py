"""Where each fly's brain starts, and the checkpoints that let it start elsewhere.

Every run so far has begun from the reconstructed MaleCNS graph, so the two arms differ
only in whether the memory rule is applied. The exam question is different: does a fly
that *already* learned something trade differently from one that has not? Answering it
needs three starting conditions and a control:

- ``baseline``: the reconstructed graph, untouched (what every earlier run used).
- ``trained``: weights checkpointed by an earlier season, restored exactly. Upstream's
  ``restore`` verifies the graph identity, rule parameters and array shapes, so a
  checkpoint from a different graph or engine build is rejected rather than silently
  loaded.
- ``reset``: the same trained weights, minus the learning. This is the control that
  separates "the fly learned" from "the fly happens to sit at unusual effiracies": it
  restores the checkpoint and then puts only the learned KC→MBON efficacies back to the
  values they had in the reconstructed graph.

A checkpoint is a whole-brain snapshot, so it is also the only honest way to compare two
seasons: the fly that sits an exam starts from the same state the training run ended in.
``changed_before_reset`` is reported because it is the evidence that a checkpoint actually
carried learning rather than a byte-identical copy of the baseline.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

KINDS = ("baseline", "trained", "reset")

CHECKPOINT_SUFFIX = ".npz"


def _sha256(path: Path) -> str:
    """Digest a checkpoint without holding the whole (possibly tens of MB) file in RAM."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class StartingWeights:
    """A parsed starting condition: the kind, plus the checkpoint it names if any."""

    kind: str
    path: Path | None = None

    def __post_init__(self):
        # Validate here as well as in `parse` so a directly constructed instance cannot
        # be invalid; `parse` only adds the filesystem check that a string spec needs.
        if self.kind not in KINDS:
            raise ValueError(
                f"Unknown starting kind {self.kind!r}; expected one of {', '.join(KINDS)}"
            )
        if self.kind == "baseline":
            if self.path is not None:
                raise ValueError("baseline takes no checkpoint path")
        else:
            if self.path is None:
                raise ValueError(f"{self.kind} weights need a checkpoint path")
            object.__setattr__(self, "path", Path(self.path))

    def label(self) -> str:
        """A short, human-readable name for tables and recordings."""
        if self.path is None:
            return "baseline"
        return f"{self.kind}:{self.path.name}"

    def describe(self) -> dict:
        """The machine-checkable form recorded next to a run.

        The digest is of the checkpoint file itself, so a later reader can tell whether
        the weights it is looking at are the ones this run advertised.
        """
        return {
            "kind": self.kind,
            "label": self.label(),
            "file": None if self.path is None else str(self.path),
            "sha256": None if self.path is None else _sha256(self.path),
        }


def parse(spec: str) -> StartingWeights:
    """Turn a CLI-style spec into ``StartingWeights``.

    Accepted forms are ``baseline``, ``trained:<path>`` and ``reset:<path>``. A missing
    or unknown kind, a missing path, or a path that is not an existing file all raise
    ``ValueError`` with the offending text, because a campaign that starts from the wrong
    weights cannot be told apart from a bug after the fact.
    """
    text = spec.strip()
    kind, sep, raw = text.partition(":")
    kind = kind.strip()
    if kind not in KINDS:
        raise ValueError(
            f"Unknown starting kind {kind!r}; expected one of {', '.join(KINDS)}"
        )
    if kind == "baseline":
        if sep:
            raise ValueError("baseline takes no checkpoint path")
        return StartingWeights("baseline")
    raw = raw.strip()
    if not raw:
        raise ValueError(
            f"{kind} weights need a checkpoint path, e.g. {kind}:/runs/<id>/weights.npz"
        )
    path = Path(raw)
    if not path.exists():
        raise ValueError(f"checkpoint does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"checkpoint is not a file: {path}")
    return StartingWeights(kind, path)


def save_brains(directory, backends: dict) -> dict:
    """Checkpoint every arm into ``<directory>/<arm_id>.npz``.

    The directory is created if missing. Existing checkpoints are never overwritten: a
    training season takes long enough that losing one to a mistyped run id is not
    acceptable, so the collision is detected for every arm *before* anything is written
    (a half-written set is worse than no set). Returns the file name, byte size and
    digest of each checkpoint for the run metadata.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    targets = {arm: directory / f"{arm}{CHECKPOINT_SUFFIX}" for arm in backends}
    collisions = sorted(str(p) for p in targets.values() if p.exists())
    if collisions:
        raise FileExistsError(
            "Refusing to overwrite existing checkpoint(s): " + ", ".join(collisions)
        )
    saved = {}
    for arm, backend in backends.items():
        path = targets[arm]
        backend.save(path)
        saved[arm] = {
            "file": path.name,
            "sha256": _sha256(path),
            "bytes": int(path.stat().st_size),
        }
    return saved


def apply(weights: StartingWeights, backend) -> dict:
    """Put a backend into the state ``weights`` names, and report what was done.

    ``reset`` mirrors upstream's own ``MemoryBrain.reset`` line for wiping learned
    efficacies (``weight[edges] = baseline_plastic``), so a reset brain is element-wise
    the brain the reconstructed graph would have produced; the non-plastic remainders of
    the weight array are whatever the checkpoint held, which is what makes the reset a
    control for the checkpoint rather than a fresh baseline.
    """
    if weights.kind == "baseline":
        return {"applied": False, "reason": "baseline weights"}
    backend.controller.restore(weights.path)
    brain = backend.controller.brain
    edges = brain.circuit["edges"]
    # Counted after the restore and before any wipe: this is the evidence that the
    # checkpoint carried learning, and not merely a relabelled copy of the baseline.
    changed = int(np.count_nonzero(brain.weight[edges] != brain.baseline_plastic))
    if weights.kind == "reset":
        brain.weight[edges] = brain.baseline_plastic
    return {
        "applied": True,
        "kind": weights.kind,
        "label": weights.label(),
        "file": str(weights.path),
        "plastic_edges": int(len(edges)),
        "changed_before_reset": changed,
    }
