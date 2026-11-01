"""Comprehensive test suite for LMeditor source ingestion loaders.

The URL loader's own rules -- the fetch, the redirect discipline, the peer
check and the size cap -- live in the Kotlin tier now, and the tier's own
tests prove them there
(``kotlin/ingest/src/test/kotlin/lmeditor/ingest/LoadersTest.kt`` and
``HttpSafetyTest.kt``). A Python test can no longer stub the transport: the
request is issued in the JVM.

What this file covers is the *door*: the per-kind loaders read a file on disk
into normalised text, and they report a failure as the domain error their
callers catch.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.ingestion.loaders import (
    DocxSourceLoader,
    HtmlSourceLoader,
    MarkdownSourceLoader,
    PdfSourceLoader,
    TextSourceLoader,
)


# ============================================================================
# 1. TextSourceLoader Tests (Task 5)
# ============================================================================


def test_text_loader_reads_plain_file() -> None:
    """Verify TextSourceLoader reads UTF-8 text and reports page_count=None."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("hello world\nsecond line\n")
        path = Path(f.name)

    loader = TextSourceLoader()
    text, page_count = loader.load(path)

    assert "hello world" in text
    assert "second line" in text
    assert page_count is None

    path.unlink()


def test_text_loader_rejects_missing_file() -> None:
    """Verify TextSourceLoader raises SourceLoadError when the path does not exist."""
    loader = TextSourceLoader()
    with pytest.raises(SourceLoadError, match="Failed to load text source"):
        loader.load(Path("/nonexistent/path/file.txt"))


def test_text_loader_rejects_binary_file() -> None:
    """Verify TextSourceLoader raises SourceLoadError when the file is not valid UTF-8."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        # \xff and \xfe are invalid UTF-8 start bytes -> UnicodeDecodeError on decode
        f.write(b"\xff\xfe\xfd\xfcnot utf-8")
        path = Path(f.name)

    loader = TextSourceLoader()
    with pytest.raises(SourceLoadError):
        loader.load(path)

    path.unlink()


# ============================================================================
# 2. MarkdownSourceLoader Tests (Task 6)
# ============================================================================


def test_markdown_loader_strips_heading_markers() -> None:
    """Verify MarkdownSourceLoader strips ATX heading markers and emphasis."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# Title\n\nSome **bold** text.\n\n## Sub\nMore.\n")
        path = Path(f.name)

    loader = MarkdownSourceLoader()
    text, page_count = loader.load(path)

    assert "Title" in text
    assert "Some bold text." in text  # markdown package + tag-strip removes **
    assert page_count is None

    path.unlink()


def test_markdown_loader_preserves_paragraph_breaks() -> None:
    """Verify MarkdownSourceLoader keeps each paragraph individually readable."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("First paragraph.\n\nSecond paragraph.\n\nThird.\n")
        path = Path(f.name)

    loader = MarkdownSourceLoader()
    text, _ = loader.load(path)
    assert "First paragraph." in text
    assert "Second paragraph." in text
    assert "Third." in text

    path.unlink()


# ============================================================================
# 3. HtmlSourceLoader Tests (Task 6)
# ============================================================================


def test_html_loader_extracts_visible_text() -> None:
    """Verify HtmlSourceLoader extracts visible text and strips <script> bodies."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(
            "<html><head><title>Ignored Title</title></head>"
            "<body><h1>Real Title</h1><p>Body text here.</p>"
            "<script>alert('nope')</script></body></html>"
        )
        path = Path(f.name)

    loader = HtmlSourceLoader()
    text, _ = loader.load(path)

    assert "Real Title" in text
    assert "Body text here." in text
    assert "alert('nope')" not in text  # script stripped

    path.unlink()


# ============================================================================
# 4. PdfSourceLoader Tests (Task 8)
# ============================================================================


def test_pdf_loader_reads_page_count() -> None:
    """Verify PdfSourceLoader reports page_count>=1 for a valid PDF fixture."""
    fixture = Path(__file__).parent / "fixtures" / "minimal.pdf"
    if not fixture.exists():
        pytest.skip("tests/fixtures/minimal.pdf not generated; see Task 8 fixture helper")
    loader = PdfSourceLoader()
    text, page_count = loader.load(fixture)
    assert page_count is not None and page_count >= 1
    assert isinstance(text, str)


def test_pdf_loader_rejects_non_pdf() -> None:
    """Verify PdfSourceLoader raises SourceLoadError when given non-PDF bytes."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"this is not a pdf")
        path = Path(f.name)
    try:
        loader = PdfSourceLoader()
        with pytest.raises(SourceLoadError, match="Failed to load pdf source"):
            loader.load(path)
    finally:
        path.unlink(missing_ok=True)


# ============================================================================
# 5. DocxSourceLoader Tests (Task 8)
# ============================================================================


def test_docx_loader_reads_text() -> None:
    """Verify DocxSourceLoader extracts paragraph text from a valid DOCX fixture."""
    fixture = Path(__file__).parent / "fixtures" / "minimal.docx"
    if not fixture.exists():
        pytest.skip("tests/fixtures/minimal.docx not generated")
    loader = DocxSourceLoader()
    text, page_count = loader.load(fixture)
    assert isinstance(text, str) and len(text) > 0
    assert "Hello from a test DOCX." in text
    assert page_count is None


# ============================================================================
# 6. Resource limits, omissions, metadata and sizes
# ============================================================================


def test_docx_loader_surfaces_the_omission_register(tmp_path) -> None:
    """The DOCX loader reports the content the reader could not preserve."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from word_fixtures import build_image_document_package
    finally:
        sys.path.pop(0)

    package = tmp_path / "with-image.docx"
    package.write_bytes(build_image_document_package())

    loader = DocxSourceLoader()
    loaded = loader.load_detailed(package)

    assert loaded.omissions, "the omission register must not be dropped"
    assert any("image" in omission for omission in loaded.omissions)
    assert "Before the figure." in loaded.text


def test_pdf_loader_extracts_the_document_metadata(tmp_path) -> None:
    """The PDF loader reports the /Info metadata, including the DOI."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata(
        {
            "/Title": "A Verifiable Writing Harness",
            "/Author": "Jane Doe",
            "/Subject": "doi:10.1234/harness",
            "/Keywords": "provenance, retrieval",
        }
    )
    pdf_path = tmp_path / "paper.pdf"
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    loader = PdfSourceLoader()
    loaded = loader.load_detailed(pdf_path)

    assert loaded.pdf_metadata["title"] == "A Verifiable Writing Harness"
    assert loaded.pdf_metadata["author"] == "Jane Doe"
    assert loaded.pdf_metadata["subject"] == "doi:10.1234/harness"
    assert loaded.pdf_metadata["doi"] == "10.1234/harness"


def test_loaders_report_the_size_of_the_artifact(tmp_path) -> None:
    """A loader reports the real size of the file it read."""
    text_path = tmp_path / "note.txt"
    content = b"a small document"
    text_path.write_bytes(content)

    loaded = TextSourceLoader().load_detailed(text_path)

    assert loaded.byte_size == len(content)


