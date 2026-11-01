"""Extraction of a PDF's own document metadata.

A PDF carries an ``/Info`` dictionary with the title, the author, the subject
and the keywords its producer wrote into it, and -- often -- the paper's DOI.
That metadata is the only bibliographic evidence available for a PDF whose
title the operator never typed, so it is extracted here rather than being
discarded inside the loader.

The identifiers are *found*, not *known*: the DOI or the arXiv id is looked
for in the metadata values and in the first page. A first page is scanned only
up to :data:`FIRST_PAGE_SCAN_LIMIT` characters because a DOI that appears
further down is more likely to be one the paper *cites* than the paper's own,
and a cited DOI must not be mistaken for the document's identity.

This module is a *door*: the patterns, the scan limit and the search order are
the Kotlin tier's, and the Python API answers with the tier's answers. The
original implementation is frozen in
:mod:`lmeditor._reference.ingestion_pdf_metadata`.

No parity check compares the two sides yet: the two tiers read a PDF with
different parsers -- the JDK's in Kotlin, ``pypdf`` in the witness -- so a
comparison would need a corpus of PDFs and would measure the parsers as much
as the metadata rule. The gap is declared rather than silent: the ledger
``REFERENCE_WITNESSES`` in ``backend/scripts/verify_kotlin_parity.py`` names
this module against its Kotlin counterpart
(``kotlin/pdf/src/main/kotlin/lmeditor/pdf/PdfMetadata.kt``) and the harness
fails if that declaration stops matching the tree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.tier import pdf as tier_pdf

#: How much of the first page is scanned for an identifier. The DOI of a paper
#: sits with its title, at the top of the first page; a DOI further down the
#: page is far more likely to be a reference.
#:
#: The same number as the Kotlin tier's ``PdfMetadata.FIRST_PAGE_SCAN_LIMIT``.
#: It is written out rather than read across the seam because a constant that
#: could not be read without a running JVM would be useless to a module that
#: is imported at startup. The two numbers are the same today, and the tier's
#: own test pins the tier's number; nothing compares the two numbers across
#: languages, so the duplication is a declared gap rather than a held one.
FIRST_PAGE_SCAN_LIMIT: Final[int] = 3000

#: The keys the Python API promises. The tier's answer carries the same nine
#: keys, plus a page count and the raw metadata mapping, which this module
#: does not promise and therefore does not return.
_METADATA_KEYS: Final[tuple[str, ...]] = (
    "title",
    "author",
    "subject",
    "keywords",
    "creator",
    "producer",
    "creation_date",
    "doi",
    "arxiv_id",
)


def _pdf_load_error(exc: SourceLoadError, origin: str) -> SourceLoadError:
    """Rebuild the tier's refusal as the Python error the callers catch.

    The tier's ``SourceLoadError`` carries its kind, its origin and its reason
    in its *message*; the seam reads them from the structured details, which
    the tier does not fill, so what arrives here has an empty kind, an empty
    origin and an empty reason. The origin is known here -- it is the one
    this call asked for -- so it is put back, and the reason is the tier's
    when it survived the seam.
    """
    reason = exc.reason or "the Kotlin tier refused the document"
    return SourceLoadError(kind="pdf", origin=origin, reason=reason)


def extract_pdf_metadata(path: str | Path) -> dict[str, str]:
    """Return the document metadata of the PDF at ``path``.

    The returned mapping uses plain lower-case keys (``title``, ``author``,
    ``subject``, ``keywords``, ``creator``, ``producer``, ``creation_date``)
    plus ``doi`` and ``arxiv_id`` when an identifier was found. It is empty
    when the PDF carries no metadata, which is a normal state for a
    generated file.

    Raises:
        SourceLoadError: the PDF could not be parsed. A PDF that cannot be
            read is a failure to load, and the tier says so rather than
            returning an empty mapping that would read as "this document
            carries no metadata".
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    try:
        metadata = tier_pdf.extract_metadata(path=str(path))
    except SourceLoadError as exc:
        raise _pdf_load_error(exc, str(path)) from exc
    return {
        key: str(metadata[key])
        for key in _METADATA_KEYS
        if metadata.get(key) is not None
    }


__all__ = [
    "FIRST_PAGE_SCAN_LIMIT",
    "extract_pdf_metadata",
]
