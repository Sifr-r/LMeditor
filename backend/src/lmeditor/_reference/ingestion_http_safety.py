"""The frozen bounded HTTP reads and the vetted redirect policy.

This is the original Python implementation of ``ingestion.http_safety``, kept
here so that the parity harness has a witness to compare the Kotlin tier
against. It is plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

Bounded HTTP reads and the vetted redirect policy shared by the loaders.

Two things live here because both the source loader and the bibliography
resolvers need them and neither may own the other's rules:

* :func:`read_limited` -- read a body **up to** a cap, aborting the read the
  moment the cap is passed. A cap that is only checked after the body has been
  buffered is not a cap; it is a post-mortem.
* :func:`fetch_limited` -- follow redirects by hand, re-validating every hop
  against the private-IP policy, and bound the body of the hop that answers.

The URL loader keeps its own stricter discipline (it pins the peer address of
every hop and fails closed when the peer is unobservable -- see
``lmeditor.ingestion.loaders``). :func:`fetch_limited` is the *resolver* path:
the resolver endpoints are fixed public hosts, so the only
attacker-controllable part of the request is the redirect target, and that is
what gets validated.
"""

from __future__ import annotations

from typing import Final, Optional
from urllib.parse import urljoin

import httpx

from lmeditor._reference.ingestion_limits import PayloadTooLargeError
from lmeditor._reference.ingestion_url_policy import (
    UnsafeSourceError,
    assert_public_url,
)

#: How many redirects a resolver call may follow. Matches the source loader's
#: budget so both paths refuse the same shape of redirect chain.
MAX_RESOLVER_REDIRECT_HOPS: Final[int] = 5


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

    Redirects are followed by hand rather than by the transport
    (``follow_redirects=False``), and every hop is re-validated against the
    private-IP policy before it is requested. A blind ``follow_redirects=True``
    would let an upstream answer with a ``Location`` pointing at
    ``http://169.254.169.254/`` and have the request followed there; the
    per-hop validation is what closes that.

    The *first* URL is not validated: it is built by the resolver from a
    literal host constant and a validated identifier, so it carries no
    user-controlled host. Every later hop is attacker-controlled and is
    validated.

    Raises:
        UnsafeSourceError: a redirect target failed the private-IP policy.
        httpx.HTTPError: the transport failed.
        PayloadTooLargeError: the answer exceeded ``max_bytes``.
    """
    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            request = client.build_request("GET", current)
            response = client.send(request, stream=True)
            try:
                if response.is_redirect:
                    location = response.headers.get("location", "").strip()
                    if not location:
                        raise UnsafeSourceError(
                            f"HTTP {response.status_code} redirect without a Location header"
                        )
                    current = _validated_redirect(current, location)
                    continue
                response.raise_for_status()
                return read_limited(response, max_bytes)
            finally:
                # Release the connection on every path -- including a refused
                # hop and an oversized body -- or a refused call leaks a socket.
                response.close()
        raise UnsafeSourceError(
            f"exceeded the maximum of {max_redirects} redirects"
        )


def _validated_redirect(current: str, location: str) -> str:
    """Return the next hop's URL, rejecting any target that is not public."""
    target = urljoin(current, location)
    assert_public_url(target)
    return target


__all__ = [
    "MAX_RESOLVER_REDIRECT_HOPS",
    "connected_peer_ip",
    "detached_response",
    "fetch_limited",
    "read_limited",
]
