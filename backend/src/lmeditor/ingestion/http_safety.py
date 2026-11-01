"""Bounded HTTP reads and the vetted redirect policy shared by the loaders.

Two things live here because both the source loader and the bibliography
resolvers need them: a fetch that cannot be talked into reading an unbounded
body, and a redirect chain that is vetted hop by hop instead of handed to the
transport to follow.

This module is a *door*: the fetch and the redirect policy live in the Kotlin
tier, and the Python API answers with the tier's answers. The original
implementation is frozen in :mod:`lmeditor._reference.ingestion_http_safety`.

No parity check compares the two sides yet: the bounded fetch is reachable on
the tier only through a method that reaches the network. The gap is declared
rather than silent: the ledger ``REFERENCE_WITNESSES`` in
``backend/scripts/verify_kotlin_parity.py`` names this module against its
Kotlin counterpart
(``kotlin/ingest/src/main/kotlin/lmeditor/ingest/HttpSafety.kt``) and the
harness fails if that declaration stops matching the tree.

What stays here is what cannot cross the seam: the three helpers that read an
``httpx.Response``. They are bound to httpx, and the tier's equivalents work
on its own response type instead.
"""

from __future__ import annotations

import re
from typing import Final, Optional

import httpx

from lmeditor.bridge.errors import KotlinDomainError
from lmeditor.ingestion.limits import PayloadTooLargeError
from lmeditor.tier import ingest as tier_ingest

#: How many redirects a resolver call may follow. Matches the source loader's
#: budget so both paths refuse the same shape of redirect chain.
#:
#: The same number as the Kotlin tier's ``HttpSafety.MAX_RESOLVER_REDIRECT_HOPS``.
#: It is written out rather than read across the seam because a constant that
#: could not be read without a running JVM would be useless to a module that
#: is imported at startup. The two numbers are the same today, and the tier's
#: own test pins the tier's number; nothing compares the two numbers across
#: languages, so the duplication is a declared gap rather than a held one.
MAX_RESOLVER_REDIRECT_HOPS: Final[int] = 5

#: The tier reports an oversized body in its own words; this pulls the byte
#: count back out of them so the Python error keeps the number it always had.
_OBSERVED_BYTES: Final[re.Pattern[str]] = re.compile(r"payload of (\d+) bytes")


def connected_peer_ip(response: httpx.Response) -> Optional[str]:
    """Return the IP address the connection actually landed on, or ``None``.

    httpx keeps the underlying httpcore network stream on the response, and a
    *live* stream can report the socket's peer address. ``None`` means "this
    transport did not expose a peer address" -- on the URL fetch path that is
    a refusal (:meth:`UrlSourceLoader._assert_vetted_peer`), never a verdict in
    the loader's favour.

    The stream must still be open for this to answer. httpcore releases the
    socket as soon as the body has been read, and a released socket raises
    instead of reporting (EBADF / WinError 10038), so a fully-read response can
    never be checked. Callers that need the peer must therefore ask while the
    response is still streaming and the body is still unread.

    This helper is bound to httpx: it reads an ``httpx.Response``, which cannot
    cross the seam. The tier's equivalent asks its own response type.
    """
    stream = response.extensions.get("network_stream")
    get_extra_info = getattr(stream, "get_extra_info", None)
    if get_extra_info is None:
        return None
    try:
        peer = get_extra_info("server_addr")
    except (OSError, AttributeError, TypeError):
        # The descriptor is gone, so the peer is genuinely unknowable. The
        # caller treats that as unproven rather than as agreement.
        return None
    if not isinstance(peer, tuple) or not peer:
        return None
    address = peer[0]
    return address if isinstance(address, str) else None


def detached_response(response: httpx.Response, content: bytes) -> httpx.Response:
    """Copy a fully-read response so its caller never touches a closed stream.

    The body must be read while the connection is still open, which means the
    response handed back from the transport is already closed and must not be
    returned as-is. The copy carries the status, headers and body forward and
    deliberately drops ``extensions``: the live ``network_stream`` they
    describe is dead by now, and retaining a reference to it would keep a
    released connection object alive.

    This helper is bound to httpx: it reads an ``httpx.Response``, which cannot
    cross the seam. The tier's equivalent asks its own response type.
    """
    return httpx.Response(
        status_code=response.status_code,
        headers=response.headers,
        content=content,
        request=response.request,
    )


def read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """Return the response body, refusing it as soon as it passes ``max_bytes``.

    The body is consumed incrementally and the running total is checked after
    every chunk, so an oversized response is abandoned mid-flight instead of
    being buffered in full and measured afterwards.

    A response whose body the transport already buffered (a test double, or a
    response that was read before this call) is measured rather than streamed:
    there is nothing left to abort.

    This helper is bound to httpx: it reads an ``httpx.Response``, which cannot
    cross the seam. The tier's equivalent asks its own response type.

    Raises:
        PayloadTooLargeError: the body is larger than ``max_bytes``.
    """
    if max_bytes < 0:
        raise ValueError(f"max_bytes must be non-negative, got {max_bytes}")

    if response.is_stream_consumed:
        content = response.content
        if len(content) > max_bytes:
            raise PayloadTooLargeError(limit=max_bytes, observed=len(content))
        return content

    total = 0
    parts: list[bytes] = []
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(limit=max_bytes, observed=total)
        parts.append(chunk)
    return b"".join(parts)


def fetch_limited(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    max_redirects: int = MAX_RESOLVER_REDIRECT_HOPS,
) -> bytes:
    """Fetch ``url`` and return at most ``max_bytes`` of its body.

    The fetch is the Kotlin tier's: it follows the redirect chain by hand, so
    that every hop is re-validated against the private-IP policy before it is
    requested, and it abandons a body that passes ``max_bytes`` instead of
    buffering it whole.

    ``timeout`` and ``max_redirects`` are part of this function's published
    signature and are still accepted, but the seam does not carry them: the
    tier applies its own timeout and its own hop budget. They are kept so that
    the callers do not have to change.

    Raises:
        UnsafeSourceError: a hop failed the private-IP policy.
        httpx.HTTPError: the transport failed.
        PayloadTooLargeError: the answer exceeded ``max_bytes``.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    try:
        return tier_ingest.fetch_limited(url, max_bytes)
    except KotlinDomainError as exc:
        failure = exc.details.get("exception")
        if failure == "PayloadTooLargeError":
            raise _payload_too_large(exc, max_bytes) from exc
        if failure == "HttpSafetyError":
            raise httpx.HTTPError(str(exc)) from exc
        raise


def _payload_too_large(exc: KotlinDomainError, max_bytes: int) -> PayloadTooLargeError:
    """Rebuild the Python error the tier's own refusal stands for.

    The tier reports an oversized body in its own words and carries no
    structured details, so the byte count is read back out of the message. A
    message that does not carry one leaves the count unknown rather than
    guessed.
    """
    match = _OBSERVED_BYTES.search(str(exc))
    observed = int(match.group(1)) if match is not None else None
    return PayloadTooLargeError(limit=max_bytes, observed=observed)


__all__ = [
    "MAX_RESOLVER_REDIRECT_HOPS",
    "connected_peer_ip",
    "detached_response",
    "fetch_limited",
    "read_limited",
]
