"""Tests for the high-level IngestionService orchestrator."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lmeditor.core.schemas import SourceIngestResult
from lmeditor.core.types import BibliographyResolverKind, SourceKind
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


def _write_pdf_with_metadata(path: Path, metadata: dict[str, str]) -> Path:
    """Write a one-page PDF that has extractable text and an ``/Info`` dictionary.

    A blank PDF has no text to chunk and a PDF built by a writer carries no
    metadata, so the fixture is assembled directly: the ingestion path needs
    both halves at once.
    """
    content = b"BT /F1 12 Tf 72 720 Td (Verifiable writing harness paper text.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< " + b" ".join(
            f"/{key} ({value})".encode() for key, value in metadata.items()
        ) + b" >>",
    ]

    document = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(document))
        document += str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref_offset = len(document)
    document += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        document += ("%010d 00000 n \n" % offset).encode()
    document += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R /Info 6 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    path.write_bytes(bytes(document))
    return path


def test_service_surfaces_the_omissions(tmp_path) -> None:
    """The omission register reaches both the source and the ingest result."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from word_fixtures import build_image_document_package
    finally:
        sys.path.pop(0)

    package = tmp_path / "with-image.docx"
    package.write_bytes(build_image_document_package())

    svc = IngestionService.default()
    result = svc.ingest(
        source_id="src_docx",
        kind=SourceKind.DOCX,
        origin=str(package),
        title="with-image.docx",
    )

    assert result.omissions, "the ingest result must surface the omissions"
    assert result.source.omissions == result.omissions
    assert any("image" in omission for omission in result.omissions)


def test_service_reports_the_size_of_the_artifact(tmp_path) -> None:
    """The source carries the real size of the file that was read."""
    src_path = tmp_path / "note.txt"
    content = b"a small document"
    src_path.write_bytes(content)

    svc = IngestionService.default()
    result = svc.ingest(
        source_id="src_text",
        kind=SourceKind.TEXT,
        origin=str(src_path),
        title="note.txt",
    )

    assert result.source.byte_size == len(content)


def test_service_resolves_a_pdf_without_an_identifier_from_its_header(
    tmp_path,
) -> None:
    """A PDF with a title but no identifier resolves from its own metadata."""
    pdf_path = _write_pdf_with_metadata(
        tmp_path / "note.pdf",
        {"Title": "An Unpublished Note", "Author": "Jane Doe"},
    )

    svc = IngestionService.default()
    result = svc.ingest(
        source_id="src_pdf",
        kind=SourceKind.PDF,
        origin=str(pdf_path),
        title="note.pdf",
    )

    bibliography = result.source.bibliography
    assert bibliography is not None
    assert bibliography.title == "An Unpublished Note"
    assert bibliography.resolver == BibliographyResolverKind.PDF_HEADER