"""Fly vs. Fly: a two-arm paper-trading competition on the MaleCNS v1.0 connectome.

Both competitors run upstream Stonkfly's neural engine (vendored under `vendor/stonkfly`).
They differ in exactly one configured field: whether the experimental KC→MBON memory rule
is applied. See `UPSTREAM.md` for attribution and `docs/model.md` for what is and is not
claimed.
"""

__version__ = "0.1.0"

SCHEMA_VERSION = "flyvsly.recording/v1"
