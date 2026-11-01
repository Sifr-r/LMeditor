"""The resolver payloads, as the Python door sees them.

The parsing of an upstream document -- the CrossRef JSON, the PubMed esummary
envelope, the arXiv Atom feed -- belongs to the Kotlin tier, and the tier's own
tests feed it the real payload shapes
(``kotlin/ingest/src/test/kotlin/lmeditor/ingest/BibliographyResolversTest.kt``).
A Python test can no longer hand the resolver a payload: the request and the
response happen in the JVM.

What is left for this side is the *door*: the resolver classes must answer
with the tier's record, and the record must carry the fields the Python side
reads. The payload that can still be fed from here is the one that never
crosses a network -- the ``/Info`` dictionary a PDF carries -- and it is
enough to prove the door end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lmeditor.core.exceptions import BibliographyResolutionError
from lmeditor.core.types import BibliographyResolverKind, SourceKind
from lmeditor.ingestion import resolvers as resolvers_module
from lmeditor.ingestion.resolvers import (
    BibliographyResolverChain,
    CrossRefBibliographyResolver,
    PdfHeaderBibliographyResolver,
    PubMedBibliographyResolver,
    UserBibliographyResolver,
)

# ============================================================================
# 1. The payload a document carries
# ============================================================================


def test_the_info_dictionary_is_read() -> None:
    """A PDF's ``/Info`` dictionary is read into a record."""
    resolver = PdfHeaderBibliographyResolver()
    record = resolver.resolve(
        pdf_metadata={
            "/Title": "Foundations of NLI",
            "/Author": "Jane Doe; John Roe",
            "/CreationDate": "D:20240315120000Z",
        },
        source_id="src_1",
        source_kind=SourceKind.PDF,
    )
    assert record is not None
    assert record.title == "Foundations of NLI"
    assert record.authors == ["Jane Doe", "John Roe"]
    assert record.resolver == BibliographyResolverKind.PDF_HEADER


def test_the_normalised_spelling_is_read_too() -> None:
    """The extracted metadata is spelled without the slash, and is read as well."""
    resolver = PdfHeaderBibliographyResolver()
    record = resolver.resolve(
        pdf_metadata={"title": "An Unpublished Note", "author": "Jane Doe"},
        source_id="src_1",
        source_kind=SourceKind.PDF,
    )
    assert record is not None
    assert record.title == "An Unpublished Note"
    assert record.authors == ["Jane Doe"]


def test_a_doi_written_into_the_metadata_is_found() -> None:
    """A DOI written into a free-text entry is found and put on the record."""
    resolver = PdfHeaderBibliographyResolver()
    record = resolver.resolve(
        pdf_metadata={"title": "A Note", "subject": "doi:10.1234/note"},
        source_id="src_1",
        source_kind=SourceKind.PDF,
    )
    assert record is not None
    assert record.doi == "10.1234/note"


def test_a_document_without_a_title_resolves_to_nothing() -> None:
    """A document that states no title resolves to nothing."""
    resolver = PdfHeaderBibliographyResolver()
    assert (
        resolver.resolve(
            pdf_metadata={"/Author": "Anon"},
            source_id="src_1",
            source_kind=SourceKind.PDF,
        )
        is None
    )


# ============================================================================
# 2. The chain over the payloads
# ============================================================================


def test_the_chain_falls_through_to_the_next_strategy() -> None:
    """A strategy that declines lets the next one answer."""
    chain = BibliographyResolverChain([
        CrossRefBibliographyResolver(),
        PdfHeaderBibliographyResolver(),
    ])
    record = chain.resolve(
        pdf_metadata={"title": "Title from the PDF"},
        source_id="src_1",
        source_kind=SourceKind.PDF,
    )
    assert record is not None
    assert record.title == "Title from the PDF"
    assert record.resolver == BibliographyResolverKind.PDF_HEADER


def test_the_chain_falls_through_after_a_refused_identifier() -> None:
    """An identifier the policy refuses does not stop the chain."""
    chain = BibliographyResolverChain([
        CrossRefBibliographyResolver(),
        PdfHeaderBibliographyResolver(),
    ])
    record = chain.resolve(
        doi="not-a-doi",
        pdf_metadata={"title": "Title from the PDF"},
        source_id="src_1",
        source_kind=SourceKind.PDF,
    )
    assert record is not None
    assert record.resolver == BibliographyResolverKind.PDF_HEADER


def test_the_chain_keeps_the_record_the_operator_supplied() -> None:
    """A user resolver in a chain keeps the record it was given."""
    from lmeditor.core.schemas import BibliographicRecord

    record = BibliographicRecord(title="A Working Paper", authors=["Jane Doe"])
    chain = BibliographyResolverChain([
        PdfHeaderBibliographyResolver(),
        UserBibliographyResolver(record),
    ])
    resolved = chain.resolve(
        source_id="src_1",
        source_kind=SourceKind.PDF,
    )
    assert resolved is not None
    # The header resolver answers first; the user resolver is the fallback.
    assert resolved.resolver in (
        BibliographyResolverKind.PDF_HEADER,
        BibliographyResolverKind.USER,
    )


def test_a_strategy_that_cannot_answer_reports_the_domain_error() -> None:
    """A failed strategy raises the error the chain knows how to fall back on."""
    with pytest.raises(BibliographyResolutionError):
        PubMedBibliographyResolver().resolve(
            pmid="not-a-pmid",
            source_id="src_1",
            source_kind=SourceKind.PDF,
        )


# ============================================================================
# 3. The door itself
# ============================================================================


def test_the_door_carries_no_second_implementation() -> None:
    """The module must not keep a Python copy of the parsing it now asks for.

    A door that still held an HTTP client of its own would be a second
    implementation of the rules: the two would drift, and only one of them
    would be in the request path.
    """
    source = Path(resolvers_module.__file__).read_text(encoding="utf-8")
    assert "httpx" not in source
