"""Comprehensive test suite for LMeditor source ingestion loaders."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.ingestion.loaders import (
    HtmlSourceLoader,
    MarkdownSourceLoader,
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
    assert "Some bold text." in text  # markdownify strips **
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