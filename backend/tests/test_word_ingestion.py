"""Integration tests for DOCX ingestion through the Word reader.

The defect this suite pins down: the old reader used ``python-docx``'s
``Document.paragraphs``, which walks only the direct children of ``w:body``. A
Word document whose evidence lives in a table therefore ingested as an empty
string, and the grounding guarantee silently failed on exactly the documents
that carry structured evidence.

The fixture used here is the Word-365-shaped package from
:mod:`tests.word_fixtures`, not a ``python-docx``-produced file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.core.types import SourceKind
from lmeditor.ingestion.loaders import DocxSourceLoader
from lmeditor.ingestion.registry import SourceLoaderRegistry
from tests.word_fixtures import (
    EXPECTED_PLAIN_TEXT,
    FIXTURE_TABLE_HEADER,
    FIXTURE_TABLE_ROW,
    FIXTURE_TITLE,
    build_word365_fixture,
)


@pytest.fixture()
def fixture_path(tmp_path: Path) -> Path:
    """The Word-365-shaped package on disk."""
    path = tmp_path / "word365.docx"
    path.write_bytes(build_word365_fixture())
    return path


# ============================================================================
# The regression
# ============================================================================


def test_table_content_survives_ingestion(fixture_path: Path) -> None:
    """Table content reaches the chunker instead of being dropped."""
    text, _ = DocxSourceLoader().load(fixture_path)
    assert FIXTURE_TABLE_HEADER in text
    assert FIXTURE_TABLE_ROW in text


def test_structured_content_survives_ingestion(fixture_path: Path) -> None:
    """The whole document reaches the chunker, in document order."""
    text, _ = DocxSourceLoader().load(fixture_path)
    assert text == EXPECTED_PLAIN_TEXT


def test_content_control_and_text_box_content_survive_ingestion(
    fixture_path: Path,
) -> None:
    """Content the old reader dropped entirely now reaches the chunker."""
    text, _ = DocxSourceLoader().load(fixture_path)
    assert "Content control paragraph" in text
    assert "Text box content" in text


# ============================================================================
# The loader contract
# ============================================================================


def test_docx_has_no_page_count(fixture_path: Path) -> None:
    """DOCX has no native pagination, so the page count stays undefined."""
    _, page_count = DocxSourceLoader().load(fixture_path)
    assert page_count is None


def test_missing_file_raises_a_source_load_error(tmp_path: Path) -> None:
    """A missing file is refused through the loader's typed error."""
    with pytest.raises(SourceLoadError, match="Failed to load docx source"):
        DocxSourceLoader().load(tmp_path / "absent.docx")


def test_a_non_docx_file_raises_a_source_load_error(tmp_path: Path) -> None:
    """A file that is not a Word package is refused, not silently accepted."""
    path = tmp_path / "broken.docx"
    path.write_bytes(b"PK not really a zip")
    with pytest.raises(SourceLoadError, match="Failed to load docx source"):
        DocxSourceLoader().load(path)


@pytest.mark.parametrize("offset", range(0, 4096, 211))
def test_a_corrupted_docx_never_leaks_another_error(
    tmp_path: Path,
    offset: int,
) -> None:
    """A damaged package is either read or refused — never anything else.

    The loader's contract is that every failure to load arrives as a
    ``SourceLoadError``: the server turns one into a 422, because a bad
    file is the client's problem.  A corruption that lands in a part the
    reader does not need — ``[Content_Types].xml``, say — is not a failure
    at all, so the assertion is about the *kind* of outcome, not about
    refusing every damaged file.
    """
    payload = bytearray(build_word365_fixture())
    payload[offset % len(payload)] ^= 0xFF
    path = tmp_path / "corrupt.docx"
    path.write_bytes(bytes(payload))
    try:
        text, _ = DocxSourceLoader().load(path)
    except SourceLoadError:
        return
    except Exception as exc:  # noqa: BLE001 - the point of the test
        pytest.fail(f"the loader leaked {type(exc).__name__}: {exc}")
    assert isinstance(text, str)


def test_the_loader_is_registered_for_the_docx_kind() -> None:
    """The registry still dispatches the docx kind to this loader."""
    loader = SourceLoaderRegistry.default().resolve(SourceKind.DOCX)
    assert isinstance(loader, DocxSourceLoader)


def test_ingestion_output_is_stable(fixture_path: Path) -> None:
    """Reading the same document twice produces the same text."""
    loader = DocxSourceLoader()
    first, _ = loader.load(fixture_path)
    second, _ = loader.load(fixture_path)
    assert first == second


def test_the_title_survives_ingestion(fixture_path: Path) -> None:
    """The document title is the first thing in the ingested text."""
    text, _ = DocxSourceLoader().load(fixture_path)
    assert text.startswith(FIXTURE_TITLE)
