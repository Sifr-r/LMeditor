"""Whitebox tests for the manuscript bridge's table support.

A markdown table is the one construct of the manuscript's syntax that the
bridge used to flatten into prose, and a table that arrives as prose cannot
be read as a table in Word.  The tests below therefore assert the *shape* of
the table the bridge builds: the rows and columns of its grid, the header
row that repeats across pages, the runs of a cell, and the fact that a line
which only looks like a table is still prose.

The last test walks the whole path -- from the draft through the bridge and
the writer to the exported package -- because a table that the model holds
but the package does not carry is no table at all.
"""

from __future__ import annotations

import io
import zipfile

from lmeditor.core.schemas import (
    CitationLineage,
    DocumentDraft,
    SectionDraft,
)
from lmeditor.word.manuscript import build_manuscript_document
from lmeditor.word.model import WordBlock, WordBlockKind, WordDocument, WordTable
from lmeditor.word.text import normalize_whitespace
from lmeditor.word.writer import WordWriter


def _section(
    section_id: str = "s1",
    *,
    title: str = "Grounding Section",
    content: str = "A grounded claim.",
    citations: tuple[CitationLineage, ...] = (),
) -> SectionDraft:
    """Build a section draft that carries the content of a table."""
    return SectionDraft(
        section_id=section_id,
        title=title,
        content=content,
        order=0,
        is_verified=True,
        citations=list(citations),
    )


def _document(*sections: SectionDraft, title: str = "A Grounded Manuscript") -> DocumentDraft:
    """Build a document draft for the bridge."""
    return DocumentDraft(
        document_id="d1",
        title=title,
        sections=list(sections),
    )


def _blocks_of_kind(document: WordDocument, kind: WordBlockKind) -> list[WordBlock]:
    """Return the document's blocks of one kind."""
    return [block for block in document.blocks if block.kind is kind]


def _table_of(document: WordDocument) -> WordTable:
    """Return the one table of the document.

    The helper fails loudly when the document carries no table or more than
    one: a test about one table must not pass because it looked at the first
    of two.
    """
    tables = _blocks_of_kind(document, WordBlockKind.TABLE)
    assert len(tables) == 1, f"expected exactly one table, got {len(tables)}"
    table = tables[0].table
    assert table is not None
    return table


def _cells(table: WordTable, row: int) -> list[str]:
    """Return the text of the cells of one row."""
    return [cell.text for cell in table.rows[row].cells]


def _package_parts(payload: bytes) -> dict[str, str]:
    """Return the text of every part of a written Word package."""
    parts: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            parts[name] = archive.read(name).decode("utf-8", errors="replace")
    return parts


# ============================================================================
# A table becomes a table block
# ============================================================================


def test_a_pipe_table_becomes_one_table_block() -> None:
    """A pipe table becomes a single table block of the right shape."""
    section = _section(
        content="| Column A | Column B |\n| -------- | -------- |\n| a1 | b1 |\n| a2 | b2 |"
    )
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert len(table.rows) == 3
    assert all(len(row.cells) == 2 for row in table.rows)
    assert _cells(table, 0) == ["Column A", "Column B"]
    assert _cells(table, 1) == ["a1", "b1"]
    assert _cells(table, 2) == ["a2", "b2"]


def test_a_table_block_carries_no_inline_text() -> None:
    """A table block carries its table and nothing else.

    The model's shape rule rejects a table that also carries text or runs:
    the text of a table is the text of its cells, and a second copy of it
    would be a second, divergent copy.
    """
    section = _section(content="| A | B |\n| --- | --- |\n| a | b |")
    document = build_manuscript_document(_document(section))
    block = _blocks_of_kind(document, WordBlockKind.TABLE)[0]
    assert block.text == ""
    assert block.runs == ()
    assert block.table is not None


def test_a_table_without_outer_pipes_is_the_same_table() -> None:
    """The outer pipes of a row are optional."""
    section = _section(content="Column A | Column B\n-------- | --------\na1 | b1")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert len(table.rows) == 2
    assert _cells(table, 0) == ["Column A", "Column B"]
    assert _cells(table, 1) == ["a1", "b1"]


def test_the_delimiter_row_is_not_a_row_of_the_table() -> None:
    """The delimiter row separates the header from the body; it is not content."""
    section = _section(content="| A | B |\n| --- | --- |\n| a | b |")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert len(table.rows) == 2
    assert "---" not in "".join(cell.text for row in table.rows for cell in row.cells)


def test_a_table_ends_at_the_first_line_that_carries_no_pipe() -> None:
    """A line of prose after the table is prose again, not a row of the table."""
    section = _section(content="| A | B |\n| --- | --- |\n| a | b |\nNot a row.")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert len(table.rows) == 2
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert [paragraph.text for paragraph in paragraphs] == ["Not a row."]


# ============================================================================
# The header row
# ============================================================================


def test_the_first_row_is_the_header_row() -> None:
    """The first row of a table repeats across pages, so it is marked as one."""
    section = _section(content="| A | B |\n| --- | --- |\n| a | b |")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert table.header_rows == 1
    assert all(cell.is_header for cell in table.rows[0].cells)


def test_the_body_rows_are_not_marked_as_header() -> None:
    """Only the first row of the table is the header row."""
    section = _section(content="| A | B |\n| --- | --- |\n| a | b |")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert not any(cell.is_header for row in table.rows[1:] for cell in row.cells)


# ============================================================================
# The content of a cell
# ============================================================================


def test_inline_formatting_inside_a_cell_becomes_formatted_runs() -> None:
    """A cell is parsed with the same inline machinery as a paragraph."""
    section = _section(content="| A | B |\n| --- | --- |\n| **bold** | `code` |")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    body = table.rows[1].cells
    assert [run.text for run in body[0].runs] == ["bold"]
    assert body[0].runs[0].bold
    assert body[1].runs[0].font == "Consolas"


def test_a_cells_text_is_the_projection_of_its_runs() -> None:
    """The text of a cell is the normalised projection of the runs it carries.

    The model keeps both, and the two must agree: a cell whose text says one
    thing and whose runs say another is a cell that reads differently in
    Word and in every tool that reads the model.
    """
    section = _section(content="| A | B |\n| --- | --- |\n| **bold** and *italic* | plain |")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    cell = table.rows[1].cells[0]
    assert cell.text == "bold and italic"
    assert cell.text == normalize_whitespace("".join(run.text for run in cell.runs))


def test_a_citation_marker_inside_a_cell_becomes_a_citation_run() -> None:
    """A marker in a cell is consumed by the same ledger as one in a paragraph.

    The bridge must not have two grammars for one marker: a cell that keeps
    the marker as literal text would print the chunk id of the manuscript.
    """
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    section = _section(
        content="| A | B |\n| --- | --- |\n| a claim [#abc123] | b |",
        citations=(lineage,),
    )
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    cell = table.rows[1].cells[0]
    assert "abc123" not in cell.text
    assert [run.text for run in cell.runs if run.superscript] == ["1"]
    assert len(document.footnotes) == 1


# ============================================================================
# The grid
# ============================================================================


def test_a_ragged_row_is_padded_to_the_widest_row() -> None:
    """A row with fewer cells is padded on the right with empty cells.

    A Word table's grid is rectangular, and a row that is short of it would
    leave a hole in the table.
    """
    section = _section(content="| A | B |\n| --- | --- |\n| a1 | b1 |\n| a2 |")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert all(len(row.cells) == 2 for row in table.rows)
    assert _cells(table, 2) == ["a2", ""]


def test_a_wider_row_widens_the_table_for_every_row() -> None:
    """The grid is as wide as the widest row, so every row is padded to it."""
    section = _section(content="| A | B |\n| --- | --- |\n| a1 | b1 | c1 |")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert all(len(row.cells) == 3 for row in table.rows)
    assert _cells(table, 0) == ["A", "B", ""]
    assert _cells(table, 1) == ["a1", "b1", "c1"]


# ============================================================================
# What is not a table
# ============================================================================


def test_a_line_with_pipes_but_no_delimiter_row_stays_prose() -> None:
    """A line that merely carries a pipe is prose, not a table."""
    section = _section(content="A claim | with a pipe is still prose.")
    document = build_manuscript_document(_document(section))
    assert _blocks_of_kind(document, WordBlockKind.TABLE) == []
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert [paragraph.text for paragraph in paragraphs] == [
        "A claim | with a pipe is still prose."
    ]


def test_a_header_row_without_a_delimiter_row_stays_prose() -> None:
    """A header row on its own does not make a table."""
    section = _section(content="| A | B |\n| a | b |")
    document = build_manuscript_document(_document(section))
    assert _blocks_of_kind(document, WordBlockKind.TABLE) == []
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert len(paragraphs) == 1
    assert "| A | B |" in paragraphs[0].text


def test_a_quoted_pipe_line_stays_a_quote() -> None:
    """A pipe line inside a block quote is quoted prose, not a table."""
    section = _section(content="> | a | b |")
    document = build_manuscript_document(_document(section))
    assert _blocks_of_kind(document, WordBlockKind.TABLE) == []
    quotes = _blocks_of_kind(document, WordBlockKind.QUOTE)
    assert [quote.text for quote in quotes] == ["| a | b |"]


# ============================================================================
# The content of a cell
# ============================================================================


def test_an_escaped_pipe_stays_inside_its_cell() -> None:
    """An escaped pipe is a literal pipe and never opens a column.

    A cell that holds a pipe is common in a manuscript that quotes code or
    a regex, and splitting it would put half of the cell in the wrong column.
    """
    section = _section(content="| A | B |\n| --- | --- |\n| a \\| b | c |")
    document = build_manuscript_document(_document(section))
    table = _table_of(document)
    assert len(table.rows[1].cells) == 2
    assert _cells(table, 1) == ["a | b", "c"]


# ============================================================================
# The table in the flow of the manuscript
# ============================================================================


def test_a_table_interrupts_and_resumes_the_prose() -> None:
    """A table is a block of its own: the prose before and after it survives."""
    section = _section(
        content="Before the table.\n| A | B |\n| --- | --- |\n| a | b |\nAfter the table."
    )
    document = build_manuscript_document(_document(section))
    kinds = [block.kind for block in document.blocks]
    assert kinds == [
        WordBlockKind.TITLE,
        WordBlockKind.HEADING,
        WordBlockKind.PARAGRAPH,
        WordBlockKind.TABLE,
        WordBlockKind.PARAGRAPH,
    ]
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert [paragraph.text for paragraph in paragraphs] == [
        "Before the table.",
        "After the table.",
    ]


def test_a_table_ends_a_numbered_list_and_starts_another() -> None:
    """A table between two numbered lists breaks the numbering sequence.

    The number of the last item continues the one before it, so only the
    table can be the reason the two items belong to different lists.
    """
    section = _section(
        content="1. one\n2. two\n\n| A | B |\n| --- | --- |\n| a | b |\n\n3. three"
    )
    document = build_manuscript_document(_document(section))
    items = _blocks_of_kind(document, WordBlockKind.LIST_ITEM)
    assert len(items) == 3
    assert items[0].list_group == items[1].list_group
    assert items[2].list_group != items[0].list_group


# ============================================================================
# The exported package
# ============================================================================


def test_the_written_package_carries_a_word_table() -> None:
    """The table survives the walk to the exported package.

    This is the end-to-end form of the promise: the reader of the ``.docx``
    must find a table, not a paragraph of pipes.
    """
    section = _section(content="| A | B |\n| --- | --- |\n| a | b |")
    document = build_manuscript_document(_document(section))
    parts = _package_parts(WordWriter().write(document))
    assert "<w:tbl>" in parts["word/document.xml"]


def test_the_written_package_carries_the_cells_of_the_table() -> None:
    """The cells of the table are in the exported package."""
    section = _section(content="| A | B |\n| --- | --- |\n| a1 | b1 |")
    document = build_manuscript_document(_document(section))
    parts = _package_parts(WordWriter().write(document))
    body = parts["word/document.xml"]
    assert "a1" in body
    assert "b1" in body
