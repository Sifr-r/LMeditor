"""Whitebox tests for the Word reader.

The reader is tested against :mod:`tests.word_fixtures`, a raw OPC package with
Word 365's own part layout. The point of these tests is not "the text comes
out" but "the structure comes out": every element the old reader dropped
(content controls, tracked changes, text boxes, tables, footnotes, field codes)
is asserted individually, so a regression names the structure that was lost.

Expectations are written against the contract
(``WORD_PROCESSING_CONTRACT.md`` §5, requirements R1-R13).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lmeditor.core.exceptions import WordParseError
from lmeditor.word.model import ListMarker, WordBlockKind
from lmeditor.word.reader import WordReader
from tests.word_fixtures import (
    EXPECTED_PLAIN_TEXT,
    FIXTURE_AFTER_FIGURE,
    FIXTURE_BEFORE_FIGURE,
    FIXTURE_DELETED_TEXT,
    FIXTURE_FINAL_PARAGRAPH,
    FIXTURE_FOOTER_TEXT,
    FIXTURE_FOOTNOTE_TEXT,
    FIXTURE_HEADER_TEXT,
    FIXTURE_HEADING_1,
    FIXTURE_HEADING_2,
    FIXTURE_INSERTED_TEXT,
    FIXTURE_LINK_TARGET,
    FIXTURE_LINK_TEXT,
    FIXTURE_NUMBERED_1,
    FIXTURE_NUMBERED_2,
    FIXTURE_PARAGRAPH,
    FIXTURE_SDT_PARAGRAPH,
    FIXTURE_TABLE_HEADER,
    FIXTURE_TABLE_ROW,
    FIXTURE_TEXTBOX_TEXT,
    FIXTURE_TITLE,
    build_image_document_package,
    build_nested_table_package,
    build_two_image_document_package,
    build_word365_fixture,
)


@pytest.fixture()
def fixture_bytes() -> bytes:
    """The Word-365-shaped package under test."""
    return build_word365_fixture()


@pytest.fixture()
def document(fixture_bytes: bytes):  # type: ignore[no-untyped-def]
    """The parsed fixture document."""
    return WordReader().read(fixture_bytes)


# ============================================================================
# The whole-document contract
# ============================================================================


def test_plain_text_matches_the_expected_recovery(document) -> None:  # type: ignore[no-untyped-def]
    """The reader recovers every block of the fixture, in order."""
    assert document.plain_text() == EXPECTED_PLAIN_TEXT


def test_reading_is_deterministic(fixture_bytes: bytes) -> None:
    """R12: identical bytes produce an identical document."""
    assert WordReader().read(fixture_bytes) == WordReader().read(fixture_bytes)


def test_reader_accepts_a_path(fixture_bytes: bytes, tmp_path: Path) -> None:
    """The reader accepts a filesystem path as well as raw bytes."""
    path = tmp_path / "fixture.docx"
    path.write_bytes(fixture_bytes)
    assert WordReader().read(path).plain_text() == EXPECTED_PLAIN_TEXT
    assert WordReader().read(str(path)).plain_text() == EXPECTED_PLAIN_TEXT


# ============================================================================
# R2 - styles become structural blocks
# ============================================================================


def test_title_style_becomes_a_title_block(document) -> None:  # type: ignore[no-untyped-def]
    """R2: the Title style maps to a TITLE block."""
    titles = [b for b in document.blocks if b.kind is WordBlockKind.TITLE]
    assert len(titles) == 1
    assert titles[0].text == FIXTURE_TITLE


def test_heading_styles_become_heading_blocks_with_levels(document) -> None:  # type: ignore[no-untyped-def]
    """R2: heading styles map to HEADING blocks carrying their level."""
    headings = {b.text: b.level for b in document.blocks if b.kind is WordBlockKind.HEADING}
    assert headings[FIXTURE_HEADING_1] == 1
    assert headings[FIXTURE_HEADING_2] == 2


def test_plain_paragraphs_become_paragraph_blocks(document) -> None:  # type: ignore[no-untyped-def]
    """R2: a paragraph with no special style stays a PARAGRAPH block."""
    paragraphs = [
        b.text for b in document.blocks if b.kind is WordBlockKind.PARAGRAPH
    ]
    assert FIXTURE_PARAGRAPH in paragraphs
    assert FIXTURE_FINAL_PARAGRAPH in paragraphs


def test_bold_runs_survive_with_their_formatting(document) -> None:  # type: ignore[no-untyped-def]
    """R5/T1: run formatting survives and the block text agrees with the runs."""
    block = next(b for b in document.blocks if b.text == FIXTURE_PARAGRAPH)
    assert block.runs, "the paragraph must expose its runs"
    assert any(run.bold for run in block.runs)
    assert any(run.text == "with emphasis" for run in block.runs)


def test_a_run_keeps_its_text_verbatim(document) -> None:  # type: ignore[no-untyped-def]
    """T1: a run's own text is not normalised at read time.

    The fixture's paragraph carries a run with a trailing space and a run
    with a leading space.  Both must survive into the model untouched:
    normalisation belongs to the *block* (T2), not to the runs, so a run
    can still be rendered exactly as Word wrote it.
    """
    block = next(b for b in document.blocks if b.text == FIXTURE_PARAGRAPH)
    texts = [run.text for run in block.runs]
    assert "A grounded claim " in texts
    assert " and a citation marker." in texts


def test_the_block_text_is_normalised_after_the_runs_are_joined(
    document,  # type: ignore[no-untyped-def]
) -> None:
    """T2: normalisation happens at block level, once the runs are joined.

    The three runs of the paragraph concatenate to "A grounded claim with
    emphasis and a citation marker." — the edge spaces collapse into the
    single space between the words, while the runs themselves keep theirs.
    Normalising per run instead would glue "claim" to "with".
    """
    block = next(b for b in document.blocks if b.text == FIXTURE_PARAGRAPH)
    assert block.text == FIXTURE_PARAGRAPH
    assert [run.text for run in block.runs] == [
        "A grounded claim ",
        "with emphasis",
        " and a citation marker.",
    ]


# ============================================================================
# R3 - lists
# ============================================================================


def test_bullet_list_items_carry_the_bullet_marker(document) -> None:  # type: ignore[no-untyped-def]
    """R3: a bullet numbering format maps to ListMarker.BULLET."""
    bullets = [
        b for b in document.blocks if b.kind is WordBlockKind.LIST_ITEM
    ]
    bullet_texts = {b.text for b in bullets}
    assert "First bullet" in bullet_texts
    assert all(b.list_marker is ListMarker.BULLET for b in bullets if b.text == "First bullet")


def test_nested_list_items_carry_their_depth(document) -> None:  # type: ignore[no-untyped-def]
    """R3: the numbering level becomes the block level."""
    nested = next(b for b in document.blocks if b.text == "Nested bullet")
    assert nested.level == 1


def test_ordered_list_items_carry_the_number_marker(document) -> None:  # type: ignore[no-untyped-def]
    """R3: a decimal numbering format maps to ListMarker.NUMBER."""
    numbered = [
        b for b in document.blocks if b.text in {FIXTURE_NUMBERED_1, FIXTURE_NUMBERED_2}
    ]
    assert len(numbered) == 2
    assert all(b.list_marker is ListMarker.NUMBER for b in numbered)


def test_ordered_items_share_a_numbering_group(document) -> None:  # type: ignore[no-untyped-def]
    """R3: items of one ordered list share a continuity group."""
    groups = {
        b.text: b.list_group
        for b in document.blocks
        if b.kind is WordBlockKind.LIST_ITEM
    }
    assert groups[FIXTURE_NUMBERED_1] is not None
    assert groups[FIXTURE_NUMBERED_1] == groups[FIXTURE_NUMBERED_2]
    assert groups[FIXTURE_NUMBERED_1] != groups["First bullet"]


# ============================================================================
# R4 - tables
# ============================================================================


def test_table_cells_are_recovered(document) -> None:  # type: ignore[no-untyped-def]
    """R4: the table's cell text is recovered, which the old reader dropped."""
    tables = [b for b in document.blocks if b.kind is WordBlockKind.TABLE]
    assert len(tables) == 1
    table = tables[0].table
    assert table is not None
    cell_texts = [cell.text for row in table.rows for cell in row.cells]
    assert cell_texts == ["Evidence", "Verdict", "chunk-abc", "entailed"]


def test_table_header_row_is_marked(document) -> None:  # type: ignore[no-untyped-def]
    """R4: a row flagged with w:tblHeader is a header row."""
    table = next(b for b in document.blocks if b.kind is WordBlockKind.TABLE).table
    assert table is not None
    assert table.header_rows == 1
    assert all(cell.is_header for cell in table.rows[0].cells)


def test_table_renders_pipe_separated_rows(document) -> None:  # type: ignore[no-untyped-def]
    """T7: a table renders one pipe-separated line per row."""
    rendered = document.plain_text()
    assert FIXTURE_TABLE_HEADER in rendered
    assert FIXTURE_TABLE_ROW in rendered


# ============================================================================
# R5 - hyperlinks
# ============================================================================


def test_hyperlink_target_is_resolved(document) -> None:  # type: ignore[no-untyped-def]
    """R5: the hyperlink target comes from the document relationships."""
    block = next(b for b in document.blocks if b.text == FIXTURE_LINK_TEXT)
    targets = {run.hyperlink for run in block.runs}
    assert FIXTURE_LINK_TARGET in targets


# ============================================================================
# R6 - footnotes
# ============================================================================


def test_footnote_text_is_recovered(document) -> None:  # type: ignore[no-untyped-def]
    """R6: footnote bodies are read into the document model."""
    texts = {footnote.text for footnote in document.footnotes}
    assert FIXTURE_FOOTNOTE_TEXT in texts


def test_separator_footnotes_are_skipped(document) -> None:  # type: ignore[no-untyped-def]
    """R6: the separator footnotes are not real footnotes."""
    assert len(document.footnotes) == 1


def test_footnote_reference_attaches_to_a_run(document) -> None:  # type: ignore[no-untyped-def]
    """R6: a footnote reference marks the run that carries it."""
    refs = [
        run.footnote_ref
        for block in document.blocks
        for run in block.runs
        if run.footnote_ref is not None
    ]
    assert len(refs) == 1


# ============================================================================
# R7 - tracked changes
# ============================================================================


def test_inserted_text_is_included(document) -> None:  # type: ignore[no-untyped-def]
    """R7: inserted content is part of the document."""
    assert FIXTURE_INSERTED_TEXT in document.plain_text()


def test_deleted_text_is_excluded(document) -> None:  # type: ignore[no-untyped-def]
    """R7: deleted content is not part of the document."""
    assert FIXTURE_DELETED_TEXT not in document.plain_text()


# ============================================================================
# R8 - content controls and text boxes
# ============================================================================


def test_content_control_paragraph_is_recovered(document) -> None:  # type: ignore[no-untyped-def]
    """R8: a paragraph inside a content control is part of the document."""
    assert FIXTURE_SDT_PARAGRAPH in document.plain_text()


def test_text_box_content_is_recovered(document) -> None:  # type: ignore[no-untyped-def]
    """R8: a paragraph inside a text box is part of the document."""
    assert FIXTURE_TEXTBOX_TEXT in document.plain_text()


# ============================================================================
# R9 - fields and page breaks
# ============================================================================


def test_field_codes_contribute_no_text(document) -> None:  # type: ignore[no-untyped-def]
    """R9: a field code is an instruction, not content."""
    assert "TOC" not in document.plain_text()


def test_page_break_becomes_a_page_break_block(document) -> None:  # type: ignore[no-untyped-def]
    """R9: a page break is preserved as a structural block."""
    breaks = [b for b in document.blocks if b.kind is WordBlockKind.PAGE_BREAK]
    assert len(breaks) == 1


# ============================================================================
# R10 - headers and footers
# ============================================================================


def test_header_and_footer_text_is_recovered(document) -> None:  # type: ignore[no-untyped-def]
    """R10: header and footer parts are read into the model."""
    assert FIXTURE_HEADER_TEXT in document.headers
    assert FIXTURE_FOOTER_TEXT in document.footers


# ============================================================================
# R11 - core properties
# ============================================================================


def test_core_properties_are_recovered(document) -> None:  # type: ignore[no-untyped-def]
    """R11: the core properties part populates the document properties."""
    properties = document.properties
    assert properties.title == FIXTURE_TITLE
    assert properties.author == "LMeditor Fixtures"
    assert properties.subject == "Fixture subject"
    assert properties.keywords == "fixture,word,365"
    assert properties.comments == "Fixture comments"
    assert properties.revision == 3


def test_core_timestamps_are_timezone_aware(document) -> None:  # type: ignore[no-untyped-def]
    """R11: creation and modification times are timezone-aware datetimes."""
    assert document.properties.created is not None
    assert document.properties.created.tzinfo is not None
    assert document.properties.modified is not None
    assert document.properties.modified.tzinfo is not None


# ============================================================================
# R13 - error discipline
# ============================================================================


def test_garbage_bytes_raise_a_parse_error() -> None:
    """R13: a malformed package is reported as a WordParseError."""
    with pytest.raises(WordParseError):
        WordReader().read(b"this is not a Word document")


def test_a_zip_without_a_document_part_raises_a_parse_error() -> None:
    """R13: a package missing word/document.xml is refused."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a document")
    with pytest.raises(WordParseError):
        WordReader().read(buffer.getvalue())


def test_a_missing_file_raises_a_parse_error(tmp_path: Path) -> None:
    """R13: a missing file is refused through the same typed error."""
    with pytest.raises(WordParseError):
        WordReader().read(tmp_path / "does-not-exist.docx")


@pytest.mark.parametrize("offset", range(0, 6144, 173))
def test_a_corrupted_package_never_leaks_another_error(offset: int) -> None:
    """R13: a damaged package is refused, never allowed to raise something else.

    The three targeted cases above only prove three ways of being malformed.
    This one walks the bytes of a *valid* package and corrupts one byte at a
    time, so the assertion is about the reader's discipline rather than about
    any particular defect: whatever the damage, the reader either reads a
    document or refuses it with ``WordParseError``.  A ``KeyError``, a
    ``UnicodeDecodeError`` or a bare ``zlib.error`` escaping here would mean a
    malformed file reaches the caller as something it cannot handle.
    """
    payload = bytearray(build_word365_fixture())
    payload[offset % len(payload)] ^= 0xFF
    try:
        WordReader().read(bytes(payload))
    except WordParseError:
        return
    except Exception as exc:  # noqa: BLE001 - the point of the test
        pytest.fail(f"the reader leaked {type(exc).__name__}: {exc}")


def test_a_truncated_package_raises_a_parse_error() -> None:
    """R13: a package cut short is refused, not read as if it were whole."""
    payload = build_word365_fixture()
    with pytest.raises(WordParseError):
        WordReader().read(payload[: len(payload) // 2])


# ============================================================================
# The omission register
# ============================================================================


def test_omissions_are_reported_not_swallowed(fixture_bytes: bytes) -> None:
    """The reader says what it dropped instead of dropping it silently."""
    reader = WordReader()
    reader.read(fixture_bytes)
    omissions = reader.last_omissions
    assert omissions, "the fixture contains an image that the reader must report"
    assert any("image" in omission.lower() for omission in omissions)


def test_an_image_contributes_no_text_but_is_reported() -> None:
    """R13: an image is recorded, not silently dropped.

    The model has no representation for a picture, so the reader cannot keep
    it — but it must *say* so, and the prose around it must survive intact.
    A document whose evidence is a screenshot of a table loses that evidence
    here; the omission register is what makes the loss visible instead of
    silent.
    """
    reader = WordReader()
    document = reader.read(build_image_document_package())
    text = document.plain_text()
    assert FIXTURE_BEFORE_FIGURE in text
    assert FIXTURE_AFTER_FIGURE in text
    assert "figure.png" not in text
    assert any("image" in omission.lower() for omission in reader.last_omissions)


def test_the_omission_register_counts_repeated_omissions() -> None:
    """R13: the register says *how many* times it reported the same thing.

    One picture reports the omission exactly once; two pictures must report
    it twice, and the count is what tells a reader that the loss was
    repeated rather than incidental.
    """
    reader = WordReader()
    reader.read(build_two_image_document_package())
    assert any(
        omission.lower().startswith("image") and omission.endswith(" x2")
        for omission in reader.last_omissions
    ), reader.last_omissions


def test_a_table_inside_a_cell_is_read() -> None:
    """R4: a cell's content is read recursively, including a nested table.

    Word allows a table inside a cell.  The inner table's content is
    content like any other, so it must be recovered rather than dropped
    along with the cell's other children.
    """
    document = WordReader().read(build_nested_table_package())
    tables = [
        block.table for block in document.blocks if block.table is not None
    ]
    assert tables, "the outer table must be recovered"
    outer_cell = tables[0].rows[0].cells[0]
    assert "Outer cell" in outer_cell.text
    assert "Inner cell" in outer_cell.text
