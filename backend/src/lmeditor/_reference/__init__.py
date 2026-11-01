"""Frozen Python reference implementations — the drift detector's only witness.

The Kotlin tier is a second implementation of the deterministic half of the
backend. Two implementations of the same rule are worth nothing unless they are
*shown* to agree, so ``backend/scripts/verify_kotlin_parity.py`` asks both of
them the same questions and compares the answers byte for byte.

That comparison is only worth anything if the Python side is a **witness**, not
a mirror. If the Python reference asked the Kotlin tier what the answer is,
the two sides would agree by construction and the harness would report a pass
that proves nothing — a drift detector that cannot fail.

So the rules in this package are **frozen**:

* they are plain Python, with no call across the NDJSON seam -- the witness
  has to be able to answer differently from the tier, or the comparison
  proves nothing;
* they are deliberately brought into agreement with the tier's *current*
  semantics, in plain Python, so that the harness compares two independent
  answers rather than one answer twice. This round did exactly that for the
  Word merge semantics and for the source-id policy; and
* they are the yardstick the Kotlin tier is measured against, not a second
  implementation that is free to drift.

The live modules under ``lmeditor.core`` and ``lmeditor.verification`` are
*doors* to the Kotlin tier. They may change freely. These change only to
follow the tier.

``backend/tests/test_parity_reference.py`` enforces the first rule
mechanically: nothing in this package may import ``lmeditor.tier`` or
``lmeditor.bridge``.
"""

from __future__ import annotations

__all__: list[str] = []
