"""The process-wide bridge to the Kotlin tier.

One JVM process serves the whole Python process. The bridge is started on the
first call and closed when the interpreter exits.

There is deliberately no fallback to the Python implementations here. A
silent fallback would hide the exact drift the Kotlin tier exists to remove:
two implementations that disagree, with only one of them in the request path.
"""

from __future__ import annotations

import atexit
import threading

from lmeditor.bridge.client import KotlinBridge

_lock = threading.Lock()
_bridge: KotlinBridge | None = None


def tier_bridge() -> KotlinBridge:
    """The shared bridge to the Kotlin tier, started on first use."""
    global _bridge
    with _lock:
        if _bridge is None:
            instance = KotlinBridge()
            atexit.register(_close)
            _bridge = instance
        return _bridge


def _close() -> None:
    """Stop the shared bridge."""
    global _bridge
    with _lock:
        if _bridge is not None:
            _bridge.close()
            _bridge = None


__all__ = ["tier_bridge"]
