"""The NDJSON seam between the Python tier and the Kotlin tier.

The Kotlin tier owns the deterministic domain rules that cross the seam; the
Python tier calls into it through the client in this package. See
``kotlin/bridge/README.md`` for the wire contract.

The rules the seam does not carry are named in ARCHITECTURE.md §18.2.1, not
implied: the drift-pinned Python twins, the two rules that have no Kotlin
counterpart at all, and the model-facing half, which is Python by design.
"""

from __future__ import annotations

from lmeditor.bridge.client import (
    BRIDGE_MAIN_CLASS,
    TIER_MODULES,
    KotlinBridge,
    decode_bytes,
    encode_bytes,
)
from lmeditor.bridge.errors import (
    BridgeProtocolError,
    BridgeUnavailableError,
    KotlinBridgeError,
    KotlinDomainError,
)

__all__ = [
    "KotlinBridge",
    "KotlinBridgeError",
    "BridgeUnavailableError",
    "BridgeProtocolError",
    "KotlinDomainError",
    "TIER_MODULES",
    "BRIDGE_MAIN_CLASS",
    "encode_bytes",
    "decode_bytes",
]
