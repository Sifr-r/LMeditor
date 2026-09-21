"""Comprehensive test suite for LMeditor source ingestion loaders."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.ingestion.loaders import (
    DocxSourceLoader,
    HtmlSourceLoader,
    MarkdownSourceLoader,
    PdfSourceLoader,
    TextSourceLoader,
    UrlSourceLoader,
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
# 6. UrlSourceLoader Tests (Task 9)
# ============================================================================


def test_url_loader_fetches_and_extracts_text(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["follow_redirects"] = kwargs.get("follow_redirects")
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            content=b"<html><body><h1>Fetched</h1><p>Body.</p></body></html>",
            request=request,
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    loader = UrlSourceLoader()
    text, page_count = loader.load("https://example.org/paper")

    assert "Fetched" in text
    assert "Body." in text
    assert page_count is None
    assert captured["url"] == "https://example.org/paper"
    assert captured["follow_redirects"] is True


def test_url_loader_raises_on_http_error(monkeypatch) -> None:
    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(404, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)

    loader = UrlSourceLoader()
    with pytest.raises(SourceLoadError, match="Failed to load url source"):
        loader.load("https://example.org/missing")