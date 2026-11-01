"""The bibliography resolver chain, as the Python door sees it.

The rules -- which upstream is asked, how a payload is read, which identifier
is safe to put in a request line, and in what order the strategies are tried
-- belong to the Kotlin tier, and the tier's own tests prove them there
(``kotlin/ingest/src/test/kotlin/lmeditor/ingest/BibliographyResolversTest.kt``).
A Python test can no longer stub the transport: the request is issued in the
JVM, where ``httpx`` has no say.

What is left for this side is the *door*: the resolver classes must keep their
names, their signatures and their exceptions, they must ask the tier, and they
must answer with the tier's record. That is what this file asserts.

The tests that need no network are the ones that stay: the PDF-header
resolver reads the metadata the document carries, and the user resolver
returns the record the operator typed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lmeditor.core.exceptions import BibliographyResolutionError
from lmeditor.core.schemas import BibliographicRecord
from lmeditor.core.types import BibliographyResolverKind, SourceKind
from lmeditor.ingestion import resolvers as resolvers_module
from lmeditor.ingestion.resolvers import (
    ArxivBibliographyResolver,
    BibliographyResolverChain,
    CrossRefBibliographyResolver,
    OpenAlexBibliographyResolver,
    PdfHeaderBibliographyResolver,
    PubMedBibliographyResolver,
    UserBibliographyResolver,
)

# ============================================================================
# 1. PdfHeaderBibliographyResolver (offline: it never makes a request)
# ============================================================================


def test_pdf_header_resolver_extracts_title_and_author() -> None:
    """The header resolver returns the title and the authors the document carries."""
    pdf_metadata = {
        "/Title": "Foundations of NLI",
        "/Author": "Jane Doe; John Roe",
        "/CreationDate": "D:20240315120000Z",
    }
    resolver = PdfHeaderBibliographyResolver()
    rec = resolver.resolve(pdf_metadata=pdf_metadata, source_id="src_1", source_kind=SourceKind.PDF)
    assert rec is not None
    assert rec.title == "Foundations of NLI"
    assert rec.authors == ["Jane Doe", "John Roe"]
    assert rec.resolver == BibliographyResolverKind.PDF_HEADER


def test_pdf_header_resolver_returns_none_when_no_title() -> None:
    """The header resolver returns None when no title is present."""
    resolver = PdfHeaderBibliographyResolver()
    rec = resolver.resolve(pdf_metadata={"/Author": "Anon"}, source_id="src_1", source_kind=SourceKind.PDF)
    assert rec is None


def test_the_pdf_header_resolver_reports_the_doi_the_metadata_states() -> None:
    """The header resolver puts the document's own DOI on the record."""
    resolver = PdfHeaderBibliographyResolver()
    rec = resolver.resolve(
        pdf_metadata={"title": "A Note", "doi": "10.1234/note"},
        source_id="src_1",
        source_kind=SourceKind.PDF,
    )
    assert rec is not None
    assert rec.doi == "10.1234/note"


# ============================================================================
# 2. The strategies that ask an upstream
# ============================================================================


def test_a_strategy_without_an_identifier_answers_none() -> None:
    """A strategy that has nothing to ask about declines without a request."""
    for resolver in (
        CrossRefBibliographyResolver(),
        ArxivBibliographyResolver(),
        PubMedBibliographyResolver(),
        OpenAlexBibliographyResolver(),
    ):
        rec = resolver.resolve(source_id="src_1", source_kind=SourceKind.PDF)
        assert rec is None, f"{resolver.kind} must decline when it has no identifier"


def test_a_refused_identifier_raises_the_domain_error() -> None:
    """An identifier the policy refuses is reported as the domain error.

    The refusal happens before any request, so this needs no network: it is
    the door's contract that the failure reaches the caller as the error the
    callers already catch.
    """
    resolver = CrossRefBibliographyResolver()
    with pytest.raises(BibliographyResolutionError) as excinfo:
        resolver.resolve(doi="not-a-doi", source_id="src_1", source_kind=SourceKind.PDF)

    assert "crossref" in str(excinfo.value)
    assert excinfo.value.resolver == "crossref"


# ============================================================================
# 3. BibliographyResolverChain (first-success / all-fail semantics)
# ============================================================================


def test_chain_returns_first_success() -> None:
    """The chain returns the first non-None result, in declared order."""
    pdf_meta = {"/Title": "From PDF", "/Author": "A"}
    chain = BibliographyResolverChain([
        PdfHeaderBibliographyResolver(),
        CrossRefBibliographyResolver(),
    ])
    rec = chain.resolve(pdf_metadata=pdf_meta, source_id="src_1", source_kind=SourceKind.PDF)
    assert rec is not None
    assert rec.resolver == BibliographyResolverKind.PDF_HEADER


def test_chain_returns_none_when_all_fail() -> None:
    """The chain returns None when every strategy raises or returns None."""
    chain = BibliographyResolverChain([CrossRefBibliographyResolver()])
    rec = chain.resolve(doi="not-a-doi", source_id="src_1", source_kind=SourceKind.PDF)
    assert rec is None


def test_chain_resolves_a_pdf_without_an_identifier_from_its_header() -> None:
    """A PDF with no identifier is resolved from its own metadata."""
    chain = BibliographyResolverChain.default()
    rec = chain.resolve(
        pdf_metadata={"title": "An Unpublished Note", "author": "Jane Doe"},
        source_id="src_1",
        source_kind=SourceKind.PDF,
    )

    assert rec is not None
    assert rec.resolver == BibliographyResolverKind.PDF_HEADER
    assert rec.title == "An Unpublished Note"
    assert rec.authors == ["Jane Doe"]


def test_the_default_chain_carries_the_five_upstream_strategies() -> None:
    """The default chain is the five strategies, in the documented order."""
    kinds = [resolver.kind for resolver in BibliographyResolverChain.default()._resolvers]
    assert kinds == [
        BibliographyResolverKind.PDF_HEADER,
        BibliographyResolverKind.CROSSREF,
        BibliographyResolverKind.ARXIV,
        BibliographyResolverKind.PUBMED,
        BibliographyResolverKind.OPENALEX,
    ]


# ============================================================================
# 4. The user resolver (P9)
# ============================================================================


def test_user_resolver_returns_the_record_the_operator_supplied() -> None:
    """The resolver returns the hand-entered record instead of nothing."""
    record = BibliographicRecord(
        title="A Working Paper",
        authors=["Jane Doe"],
        resolver=BibliographyResolverKind.NONE,
    )
    resolver = UserBibliographyResolver(record)

    rec = resolver.resolve(source_id="src_1", source_kind=SourceKind.PDF)

    assert rec is not None
    assert rec.title == "A Working Paper"
    assert rec.resolver == BibliographyResolverKind.USER


def test_user_resolver_declines_when_it_has_no_record() -> None:
    """With nothing to return, the resolver declines rather than inventing one."""
    resolver = UserBibliographyResolver()
    assert resolver.resolve(source_id="src_1", source_kind=SourceKind.PDF) is None


def test_user_resolver_only_answers_for_the_sources_it_was_given() -> None:
    """A resolver scoped to one source declines for the others."""
    record = BibliographicRecord(title="A Working Paper", authors=[])
    resolver = UserBibliographyResolver(record, source_ids={"src_1"})

    assert resolver.resolve(source_id="src_1", source_kind=SourceKind.PDF) is not None
    assert resolver.resolve(source_id="src_2", source_kind=SourceKind.PDF) is None


def test_the_user_resolver_is_not_part_of_the_default_chain() -> None:
    """The default chain resolves what a document says about itself."""
    kinds = [resolver.kind for resolver in BibliographyResolverChain.default()._resolvers]
    assert BibliographyResolverKind.USER not in kinds


# ============================================================================
# 5. The door itself
# ============================================================================


def test_the_door_carries_no_second_implementation() -> None:
    """The module must not keep a Python copy of the rules it now asks for.

    A door that still held an HTTP client of its own would be a second
    implementation of the rules: the two would drift, and only one of them
    would be in the request path.
    """
    source = Path(resolvers_module.__file__).read_text(encoding="utf-8")
    assert "httpx" not in source
