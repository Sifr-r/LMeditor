"""Format detection for the Word subsystem.

The reader only speaks OOXML (``.docx``).  A legacy Word 97-2003 document
(``.doc``) is a *different* format entirely -- an OLE2 compound-file binary --
and feeding one to the OOXML reader produces a confusing "not a valid zip
container" error that tells the operator nothing about what to do next.

This module recognises the legacy format by its magic bytes and states, in
one place, what the operator has to do about it.  The reader and the HTTP
route both use it, so the two can never disagree about what a file is.

This module is **not** a door.  The Kotlin tier carries the same rules in
``lmeditor.word.WordFormats``, and the two are held together by a shared
corpus: one file, read by both sides, whose expected values are Python's
answers.

What stays here, and why:

* **The rule, in Python.**  A magic number is compared a handful of times
  per file, and a door would spend a round trip across the NDJSON seam to do
  it.  The corpus is what keeps the two copies from drifting apart, and it
  costs one file read.
* **The refusal, in the operator's words.**  The reason strings are what the
  operator reads, and the corpus pins them on both sides -- so the two tiers
  cannot say different things about the same file.
"""

from __future__ import annotations

from typing import Final

#: The OLE2 / Compound File Binary magic number every legacy ``.doc`` starts
#: with (``D0 CF 11 E0 A1 B1 1A E1``).
LEGACY_DOC_MAGIC: Final[bytes] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: The ZIP local-file-header magic number every OOXML package starts with.
ZIP_MAGIC: Final[bytes] = b"PK\x03\x04"

#: The reason a legacy document is refused, in the words the operator needs.
LEGACY_DOC_REASON: Final[str] = (
    "legacy .doc (OLE2) is not supported; convert to .docx"
)

#: The reason a file that is neither format at all is refused.
UNKNOWN_FORMAT_REASON: Final[str] = (
    "the file is neither an OOXML (.docx) package nor a legacy .doc document"
)


def is_legacy_doc(data: bytes) -> bool:
    """Report whether ``data`` is a legacy binary Word document.

    The check is on the magic number only: an OLE2 compound file is what a
    ``.doc`` document is, whatever its extension claims.

    Args:
        data: The first bytes of the file.

    Returns:
        True when the data carries the OLE2 magic number.
    """
    return data[: len(LEGACY_DOC_MAGIC)] == LEGACY_DOC_MAGIC


def is_zip_package(data: bytes) -> bool:
    """Report whether ``data`` starts like an OOXML (zip) package."""
    return data[: len(ZIP_MAGIC)] == ZIP_MAGIC


def describe_unsupported_format(data: bytes) -> str:
    """Return the reason a file cannot be parsed as OOXML.

    Args:
        data: The first bytes of the file.

    Returns:
        A reason string suitable for
        :class:`~lmeditor.core.exceptions.WordParseError`.
    """
    if is_legacy_doc(data):
        return LEGACY_DOC_REASON
    if is_zip_package(data):
        return "the file is not a valid .docx package (bad zip container)"
    return UNKNOWN_FORMAT_REASON


__all__ = [
    "LEGACY_DOC_MAGIC",
    "LEGACY_DOC_REASON",
    "UNKNOWN_FORMAT_REASON",
    "ZIP_MAGIC",
    "describe_unsupported_format",
    "is_legacy_doc",
    "is_zip_package",
]
