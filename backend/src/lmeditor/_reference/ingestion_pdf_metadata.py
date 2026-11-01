"""The frozen extraction of a PDF's own document metadata.

This is the original Python implementation of ``ingestion.pdf_metadata``, kept
here so that the parity harness has a witness to compare the Kotlin tier
against. It is plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

Extraction of a PDF's own document metadata.

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
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Final, Optional

logger = logging.getLogger(__name__)

#: A DOI: the ``10.`` prefix, a registrant code of four to nine digits, and a
#: suffix. The suffix charset is deliberately wide -- a legacy SICI suffix
#: uses parentheses, semicolons and angle brackets -- and the trailing
#: punctuation of a sentence is trimmed afterwards.
_DOI_PATTERN: Final[re.Pattern[str]] = re.compile(r"10\.\d{4,9}/[^\s\"'<>\]]+")

#: An arXiv id in either its modern (``2401.01234``, ``2401.01234v2``) or its
#: legacy (``hep-th/9901001``, ``math.GT/0309136``) form.
_ARXIV_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"arXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?|[a-zA-Z-]+(?:\.[A-Za-z]{2})?/\d{7}(?:v\d+)?)"
)

#: How much of the first page is scanned for an identifier. The DOI of a paper
#: sits with its title, at the top of the first page; a DOI further down the
#: page is far more likely to be a reference.
FIRST_PAGE_SCAN_LIMIT: Final[int] = 3000

#: Characters stripped from the end of a matched DOI, because a DOI written
#: inside a sentence is followed by the sentence's punctuation.
_DOI_TRAILING_PUNCTUATION: Final[str] = ".,;:"

#: The ``/Info`` entries that are copied into the extracted metadata, mapped
#: to the plain key they are stored under.
_INFO_KEYS: Final[dict[str, str]] = {
    "/Title": "title",
    "/Author": "author",
    "/Subject": "subject",
    "/Keywords": "keywords",
    "/Creator": "creator",
    "/Producer": "producer",
    "/CreationDate": "creation_date",
}


def _clean_doi(raw: str) -> str:
    """Return ``raw`` without the sentence punctuation that follows a DOI."""
    return raw.rstrip(_DOI_TRAILING_PUNCTUATION)


def _find_doi(text: str) -> Optional[str]:
    """Return the first DOI in ``text``, or ``None``."""
    match = _DOI_PATTERN.search(text)
    if match is None:
        return None
    doi = _clean_doi(match.group(0))
    return doi or None


def _find_arxiv_id(text: str) -> Optional[str]:
    """Return the first arXiv id in ``text``, or ``None``."""
    match = _ARXIV_PATTERN.search(text)
    if match is None:
        return None
    return match.group(1)


def _first_page_text(reader: object) -> str:
    """Return the text of a PDF's first page, or ``''`` when it has none."""
    pages = getattr(reader, "pages", None)
    if not pages:
        return ""
    try:
        extracted = pages[0].extract_text() or ""
    except Exception as exc:  # pragma: no cover - defensive; pypdf edge cases
        logger.warning("Could not extract the first page of a PDF: %s", exc)
        return ""
    return extracted


def extract_pdf_metadata(path: str | Path) -> dict[str, str]:
    """Return the document metadata of the PDF at ``path``.

    The returned mapping uses plain lower-case keys (``title``, ``author``,
    ``subject``, ``keywords``, ``creator``, ``producer``, ``creation_date``)
    plus ``doi`` and ``arxiv_id`` when an identifier was found. It is empty
    when the PDF carries no metadata, which is a normal state for a
    generated file.
    """
    metadata: dict[str, str] = {}
    try:
        from pypdf import PdfReader  # local import; soft dep
    except ImportError:
        logger.warning("pypdf is not installed; PDF metadata cannot be extracted")
        return metadata

    try:
        reader = PdfReader(str(path))
        info = reader.metadata
        if info:
            for info_key, plain_key in _INFO_KEYS.items():
                value = info.get(info_key)
                if isinstance(value, str) and value.strip():
                    metadata[plain_key] = value.strip()
        first_page = _first_page_text(reader)
    except Exception as exc:
        # A PDF whose metadata cannot be read is still a PDF whose *text* can
        # be read: the loader has already opened it successfully. The failure
        # is reported rather than swallowed, and the metadata stays empty.
        logger.warning("Could not extract metadata from PDF %s: %s", path, exc)
        return {}

    metadata.update(_identifiers(metadata, first_page))
    return metadata


def _identifiers(metadata: dict[str, str], first_page: str) -> dict[str, str]:
    """Return the DOI / arXiv id found in the metadata or the first page."""
    found: dict[str, str] = {}

    haystacks = list(metadata.values())
    if first_page:
        haystacks.append(first_page[:FIRST_PAGE_SCAN_LIMIT])

    for haystack in haystacks:
        if "doi" not in found:
            doi = _find_doi(haystack)
            if doi:
                found["doi"] = doi
        if "arxiv_id" not in found:
            arxiv_id = _find_arxiv_id(haystack)
            if arxiv_id:
                found["arxiv_id"] = arxiv_id
        if "doi" in found and "arxiv_id" in found:
            break
    return found


__all__ = [
    "FIRST_PAGE_SCAN_LIMIT",
    "extract_pdf_metadata",
]
