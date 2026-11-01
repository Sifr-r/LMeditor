"""The Python face of the Kotlin tier.

The deterministic domain rules the backend shares across the seam are
implemented in Kotlin. The functions in this package are the door to those
rules: they call across the NDJSON seam and validate the answer into the
Python tier's own models.

They are deliberately thin. A rule that lives here is a rule that exists in
two places, which is the one thing this package must never become.

The seam does not carry every rule, and this package does not pretend it
does. ARCHITECTURE.md §18.2.1 is the catalogue of what the tier does not own:
the drift-pinned Python twins (the Word whitespace and format rules, the
compiled citation grammar, the imported-document section split, the chunking
algorithms and the byte-offset helpers), the two rules that have no Kotlin
counterpart at all (the document-level grounding aggregation and the async
session and human-in-the-loop broker), and the model-facing half, which is
Python by design.
"""

from __future__ import annotations

from lmeditor.tier import (
    core,
    discovery,
    editing,
    factcheck,
    ingest,
    library,
    pdf,
    server,
    text,
    word,
    workflow,
)
from lmeditor.tier.bridge import tier_bridge

__all__ = [
    "tier_bridge",
    "core",
    "text",
    "word",
    "pdf",
    "ingest",
    "library",
    "workflow",
    "editing",
    "factcheck",
    "discovery",
    "server",
]
