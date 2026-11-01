"""The frozen limits of the ingestion boundary.

This is the original Python implementation of ``ingestion.limits``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

Resource limits for every byte that crosses the ingestion boundary.

Ingestion is the one place where the process accepts data it did not author:
a URL body, a bibliography resolver's answer, an uploaded file. Each of those
was previously read whole, so a single request could make the process hold an
arbitrary amount of memory (and, for uploads, an arbitrary amount of disk).

The caps below are deliberately *hard* limits on a single payload rather than
a rate limit or a timeout: a timeout bounds how long a request takes, not how
much it may consume, and the resource that actually runs out here is memory
and disk, not patience.

The numbers are the default budget for the whole application and are exported
so the server layer can refuse an oversized upload *before* it is buffered
(:func:`validate_upload_size`) instead of after.
"""

from __future__ import annotations

from typing import Any, Final

from lmeditor.core.exceptions import ValidationError

#: Largest URL body the loader will read. A source document large enough to
#: exceed this is a misconfiguration or an attack, not a document: the writer
#: only ever sees chunks of a few hundred characters.
MAX_URL_RESPONSE_BYTES: Final[int] = 10 * 1024 * 1024

#: Largest bibliography-resolver response that will be read. The upstream
#: services answer with a single JSON record (or a short Atom feed); two
#: megabytes is already orders of magnitude more than any of them needs.
MAX_RESOLVER_RESPONSE_BYTES: Final[int] = 2 * 1024 * 1024

#: Largest uploaded file the server will accept, in bytes.
MAX_UPLOAD_BYTES: Final[int] = 50 * 1024 * 1024

#: Total number of bytes the persisted-upload store may hold at once. The
#: store evicts its least recently used files to stay under this budget.
MAX_SOURCE_STORE_BYTES: Final[int] = 512 * 1024 * 1024


class PayloadTooLargeError(Exception):
    """Raised when a payload exceeds the cap that applies to it.

    Deliberately *not* a :class:`~lmeditor.core.exceptions.SourceLoadError`:
    the callers translate it into the domain error that fits their layer
    (a load failure for a source, a resolution failure for a resolver), and
    the translation is where the surrounding context is added.
    """

    def __init__(self, limit: int, observed: int | None = None) -> None:
        self.limit = limit
        self.observed = observed
        if observed is None:
            message = f"payload exceeds the maximum of {limit} bytes"
        else:
            message = (
                f"payload of {observed} bytes exceeds the maximum of {limit} bytes"
            )
        super().__init__(message)


def validate_upload_size(size: Any) -> int:
    """Return ``size`` when it is an acceptable upload size, else raise.

    This is the helper the HTTP boundary calls *before* it buffers an upload:
    the server reads the whole file into memory to persist it, so the only
    moment a cheap refusal is possible is before that read.

    Raises:
        ValidationError: the size is not a plain non-negative integer, or it
            exceeds :data:`MAX_UPLOAD_BYTES`.
    """
    if isinstance(size, bool) or not isinstance(size, int):
        raise ValidationError(
            "upload size must be an integer number of bytes",
            field="size",
            value=size,
        )
    if size < 0:
        raise ValidationError(
            "upload size must not be negative",
            field="size",
            value=size,
        )
    if size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"upload of {size} bytes exceeds the maximum of {MAX_UPLOAD_BYTES} bytes",
            field="size",
            value=size,
        )
    return size


__all__ = [
    "MAX_RESOLVER_RESPONSE_BYTES",
    "MAX_SOURCE_STORE_BYTES",
    "MAX_UPLOAD_BYTES",
    "MAX_URL_RESPONSE_BYTES",
    "PayloadTooLargeError",
    "validate_upload_size",
]
