"""Tests for the high-level IngestionService orchestrator."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lmeditor.core.schemas import SourceIngestResult
from lmeditor.core.types import BibliographyResolverKind, SourceKind
from lmeditor.ingestion.registry import SourceLoaderRegistry
from lmeditor.ingestion.service import IngestionService


def test_service_ingests_text_file(tmp_path) -> None:
    """Text file ingestion returns a SourceIngestResult with no bibliography."""
    src_path = tmp_path / "note.txt"
    src_path.write_text("Hello service layer.", encoding="utf-8")

    svc = IngestionService.default()
    result = svc.ingest(
        source_id="src_test",
        kind=SourceKind.TEXT,
        origin=str(src_path),
        title="note.txt",
    )

    assert isinstance(result, SourceIngestResult)
    assert result.source.id == "src_test"
    assert result.source.kind == SourceKind.TEXT
    assert result.source.bibliography is None  # no resolver configured
    assert all(c.document_id == "src_test" for c in result.chunks)
    assert any("Hello service layer." in c.text for c in result.chunks)


def test_service_attaches_bibliography_when_doi_resolves(monkeypatch) -> None:
    """PDF ingestion with a DOI resolves bibliography via the CrossRef resolver."""
    src_path = Path(__file__).parent / "fixtures" / "minimal.pdf"
    if not src_path.exists():
        pytest.skip("PDF fixture missing")

    # Probe the fixture: stub PDFs with no extractable text are valid for
    # bibliography testing but cannot drive the chunker end-to-end.
    probe_text, _probe_pages = SourceLoaderRegistry.default().load(
        SourceKind.PDF, str(src_path)
    )
    if not probe_text.strip():
        pytest.skip("PDF fixture has no extractable text")

    payload = {
        "message": {
            "title": ["Service Layer Title"],
            "author": [{"given": "J", "family": "Doe"}],
            "issued": {"date-parts": [[2025]]},
            "DOI": "10.1234/svc",
            "URL": "https://doi.org/10.1234/svc",
        }
    }

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)

    svc = IngestionService.default()
    result = svc.ingest(
        source_id="src_pdf",
        kind=SourceKind.PDF,
        origin=str(src_path),
        title="minimal.pdf",
        doi="10.1234/svc",
    )
    assert result.source.bibliography is not None
    assert result.source.bibliography.title == "Service Layer Title"
    assert result.source.bibliography.resolver == BibliographyResolverKind.CROSSREF