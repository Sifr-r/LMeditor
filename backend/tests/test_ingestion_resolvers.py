"""Tests for the bibliography resolver chain (PDF header → CrossRef → arXiv → PubMed → OpenAlex)."""

from __future__ import annotations

import httpx
import pytest

from lmeditor.core.exceptions import BibliographyResolutionError
from lmeditor.core.schemas import BibliographicRecord
from lmeditor.core.types import BibliographyResolverKind, SourceKind
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
# 1. PdfHeaderBibliographyResolver (synchronous, no HTTP)
# ============================================================================


def test_pdf_header_resolver_extracts_title_and_author() -> None:
    """PdfHeaderBibliographyResolver returns a BibliographicRecord when /Title and /Author are present."""
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
    """PdfHeaderBibliographyResolver returns None when no title is present."""
    resolver = PdfHeaderBibliographyResolver()
    rec = resolver.resolve(pdf_metadata={"/Author": "Anon"}, source_id="src_1", source_kind=SourceKind.PDF)
    assert rec is None


# ============================================================================
# 2. CrossRef HTTP resolver (mocked httpx.get)
# ============================================================================


def test_crossref_resolver_fetches_metadata(monkeypatch) -> None:
    """CrossRefBibliographyResolver parses the Crossref /works/{doi} payload via mocked httpx.get."""
    payload = {
        "message": {
            "title": ["Crossref Title"],
            "author": [{"family": "Doe", "given": "Jane"}],
            "issued": {"date-parts": [[2024, 6, 1]]},
            "container-title": ["Journal X"],
            "DOI": "10.1234/abc",
            "URL": "https://doi.org/10.1234/abc",
        }
    }

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)

    resolver = CrossRefBibliographyResolver()
    rec = resolver.resolve(doi="10.1234/abc", source_id="src_1", source_kind=SourceKind.PDF)
    assert rec is not None
    assert rec.title == "Crossref Title"
    assert rec.year == 2024
    assert rec.doi == "10.1234/abc"
    assert rec.resolver == BibliographyResolverKind.CROSSREF


# ============================================================================
# 3. BibliographyResolverChain (first-success / all-fail semantics)
# ============================================================================


def test_chain_returns_first_success() -> None:
    """BibliographyResolverChain returns the first non-None result, in declared order."""
    pdf_meta = {"/Title": "From PDF", "/Author": "A"}
    chain = BibliographyResolverChain([
        PdfHeaderBibliographyResolver(),
        CrossRefBibliographyResolver(),
    ])
    rec = chain.resolve(pdf_metadata=pdf_meta, source_id="src_1", source_kind=SourceKind.PDF)
    assert rec is not None
    assert rec.resolver == BibliographyResolverKind.PDF_HEADER


def test_chain_returns_none_when_all_fail(monkeypatch) -> None:
    """BibliographyResolverChain returns None when every resolver raises or returns None."""
    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(500, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    chain = BibliographyResolverChain([CrossRefBibliographyResolver()])
    rec = chain.resolve(doi="10.1234/missing", source_id="src_1", source_kind=SourceKind.PDF)
    assert rec is None
