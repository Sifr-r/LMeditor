"""Resource limits at the ingestion boundary (P1).

Three kinds of payload cross into the process from outside: a URL body, a
bibliography resolver's answer and an uploaded file. Each is bounded here, and
each bound is checked *while* the payload is being read -- a cap that is only
applied after the body has been buffered is not a cap.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
import pytest

from lmeditor.core.exceptions import ValidationError
from lmeditor.ingestion.http_safety import read_limited
from lmeditor.ingestion.limits import (
    MAX_RESOLVER_RESPONSE_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_URL_RESPONSE_BYTES,
    PayloadTooLargeError,
    validate_upload_size,
)


class _CountingBody(httpx.SyncByteStream):
    """A body that records how many bytes were actually handed over.

    The point of the cap is that the rest of the body is never read, so the
    evidence is the number of bytes the transport was asked for -- not the size
    of the answer the caller ended up with.
    """

    def __init__(self, total_bytes: int, chunk_size: int = 64) -> None:
        self._remaining = total_bytes
        self._chunk_size = chunk_size
        self.yielded = 0

    def __iter__(self):
        while self._remaining > 0:
            size = min(self._chunk_size, self._remaining)
            self._remaining -= size
            self.yielded += size
            yield b"x" * size

    def close(self) -> None:
        return None


# ============================================================================
# 1. The upload cap
# ============================================================================


def test_validate_upload_size_accepts_a_size_inside_the_cap() -> None:
    """A size under the cap is returned unchanged."""
    assert validate_upload_size(0) == 0
    assert validate_upload_size(MAX_UPLOAD_BYTES) == MAX_UPLOAD_BYTES


def test_validate_upload_size_refuses_an_oversized_upload() -> None:
    """An upload over the cap is refused with the typed validation error."""
    with pytest.raises(ValidationError) as excinfo:
        validate_upload_size(MAX_UPLOAD_BYTES + 1)
    assert "exceeds the maximum" in str(excinfo.value)


def test_validate_upload_size_refuses_a_negative_or_non_integer_size() -> None:
    """A size that is not a plain byte count is refused, not coerced."""
    with pytest.raises(ValidationError):
        validate_upload_size(-1)
    with pytest.raises(ValidationError):
        validate_upload_size("1024")  # type: ignore[arg-type]


# ============================================================================
# 2. The bounded read
# ============================================================================


def test_read_limited_returns_a_body_inside_the_cap() -> None:
    """A body under the cap is returned whole."""
    response = httpx.Response(200, content=b"a short body")
    assert read_limited(response, 1024) == b"a short body"


def test_read_limited_aborts_the_read_when_the_cap_is_passed() -> None:
    """An oversized body is abandoned mid-read, not measured after the fact."""
    body = _CountingBody(total_bytes=100_000, chunk_size=64)
    response = httpx.Response(200, stream=body)

    with pytest.raises(PayloadTooLargeError) as excinfo:
        read_limited(response, 1024)

    assert excinfo.value.limit == 1024
    # The read stopped early: the transport never had to hand over the rest.
    assert body.yielded <= 1024 + 64


def test_read_limited_measures_a_body_the_transport_already_buffered() -> None:
    """A body that was already read is measured rather than streamed."""
    response = httpx.Response(200, content=b"0123456789")
    with pytest.raises(PayloadTooLargeError) as excinfo:
        read_limited(response, 4)
    assert excinfo.value.observed == 10


# ============================================================================
# 3. The resolver cap
# ============================================================================


def test_the_resolver_cap_has_a_ceiling() -> None:
    """The resolver's default cap is a real, finite number."""
    assert 0 < MAX_RESOLVER_RESPONSE_BYTES < MAX_UPLOAD_BYTES
    assert 0 < MAX_URL_RESPONSE_BYTES
