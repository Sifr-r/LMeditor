"""Errors raised by the NDJSON seam to the Kotlin tier."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lmeditor.core.exceptions import LMeditorError


class KotlinBridgeError(LMeditorError):
    """Base error for the NDJSON seam between the Python and Kotlin tiers."""


class BridgeUnavailableError(KotlinBridgeError):
    """The Kotlin bridge process could not be started, or it died mid-call.

    This error is never swallowed: a caller that cannot reach the Kotlin tier
    must fail loudly, because silently falling back to the Python
    implementation would hide exactly the drift the tier exists to remove.
    """

    def __init__(self, reason: str = "", detail: str = "") -> None:
        message = f"Kotlin bridge unavailable: {reason}"
        if detail:
            message = f"{message} | {detail}"
        super().__init__(message, details={"reason": reason, "detail": detail})


class BridgeProtocolError(KotlinBridgeError):
    """The bridge answered with something that is not a valid response."""

    def __init__(self, reason: str = "", detail: str = "") -> None:
        message = f"Malformed Kotlin bridge response: {reason}"
        if detail:
            message = f"{message} | {detail}"
        super().__init__(message, details={"reason": reason, "detail": detail})


class KotlinDomainError(KotlinBridgeError):
    """The Kotlin tier refused the operation with a typed domain error.

    ``error_type`` is the simple name of the Kotlin exception class, so a
    caller can tell a ``WordParseError`` from a ``ChunkNotFoundError``
    without parsing the message.
    """

    def __init__(
        self,
        error_type: str,
        message: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=dict(details or {}))
        self.error_type = error_type


__all__ = [
    "KotlinBridgeError",
    "BridgeUnavailableError",
    "BridgeProtocolError",
    "KotlinDomainError",
]
