"""Our extension of upstream's DNp20 readout: the same rule, with its gate optional.

Upstream's decoder is fixed: mean right DNp20 minus mean left DNp20 decides buy or sell, but
only if at least one DNpe017 spike also occurs on that bar. That gate is why the flies trade
so rarely — measured over three recorded seasons, the directional difference exceeded the
±2 Hz threshold on 90-98% of bars while the gate was open on only 27-60% of them.

Making the gate optional is **our** change, not upstream's rule, and it is applied to both
flies identically. It is also a change to the *interface*, not to the brain: the spike counts
are untouched.
"""

import numpy as np
from stonkfly.neural.controller import Decoder


class ConfigurableDecoder(Decoder):
    """Upstream's DNp20 readout with the DNpe017 gate and threshold made explicit options.

    With `require_gate=True` this is byte-identical to upstream's decoder, which
    `tests/test_decoder.py` checks against the upstream class for the same inputs.
    """

    def __init__(self, ids, annotation, threshold, require_gate=True):
        super().__init__(ids, annotation, threshold)
        self.require_gate = bool(require_gate)

    def decode(self, counts, seconds):
        # Same arithmetic as upstream: mean rates so side population size cannot bias it.
        left = float(np.mean(counts[self.left]) / seconds)
        right = float(np.mean(counts[self.right]) / seconds)
        difference = right - left
        gate = int(counts[self.gate].sum())
        directional = abs(difference) >= self.threshold
        decided = directional and (gate > 0 or not self.require_gate)
        side = "HOLD" if not decided else "BUY" if difference > 0 else "SELL"
        return {
            "side": side,
            "left_hz": left,
            "right_hz": right,
            "difference_hz": difference,
            "gate_spikes": gate,
            "gate_required": self.require_gate,
            "cell_ids": self.identities,
        }
