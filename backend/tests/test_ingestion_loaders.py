"""Comprehensive test suite for LMeditor source ingestion loaders."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.ingestion.loaders import TextSourceLoader


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