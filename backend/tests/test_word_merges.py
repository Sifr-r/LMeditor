"""Vertical merges, in both directions.

An audit of the first pass found that the merge path had **no test at all**:
mutating the writer to stop synthesising continuation cells, or to emit a bare
``w:vMerge`` instead of ``w:vMerge w:val="restart"``, left every test green.
This module closes that hole.

Three things have to hold, and each is asserted separately:

* the **reader** must model a merge the way Word does — the cell that starts
  the merge carries the span and the text, the cells inside the merged region
  are continuations with no text of their own;
* the **writer** must emit a merge that Word can read back, with the grid
  computed from grid columns rather than cell counts;
* the **round trip** must not change anything: same grid, same spans, same
  text.

The requirement ids are from ``WORD_PROCESSING_CONTRACT.md``: §3.1 I9, §3.2 D4,
§5 R4, §6 W4, and gate 3.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

import pytest

from lmeditor.core.exceptions import WordRenderError
from lmeditor.word.model import (
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordProperties,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter
from tests.word_fixtures import (
    build_merge_region_package,
    build_merged_table_package,
    build_wide_merge_package,
)


def _tables(document: WordDocument) -> list[WordTable]:
    """The document's tables, in order."""
    return [
        block.table
        for block in document.blocks
        if block.kind is WordBlockKind.TABLE and block.table is not None
    ]


def _frozen_tables(package: bytes) -> list[WordTable]:
    """The tables the frozen witness reads out of a package, in order."""
    from lmeditor._reference.word_reader import WordReader as FrozenReader

    return _tables(FrozenReader().read_bytes(package))


def _xml(payload: bytes, part: str = "word/document.xml") -> str:
    """A package part as text."""
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        return archive.read(part).decode("utf-8")


def _grid_columns(payload: bytes) -> int:
    """The number of grid columns in the **first** table of the package.

    A package can hold several tables, so the count is taken from the first
    ``w:tbl`` only — a document-wide count would say nothing about the table
    under test.
    """
    xml = _xml(payload)
    table = xml.split("<w:tbl>")[1].split("</w:tbl>")[0]
    return table.count("<w:gridCol")


# ============================================================================
# §5 R4 — the reader models the merge the way Word does
# ============================================================================


def test_a_two_column_wide_merge_is_read_as_one_merged_region() -> None:
    """R4: a 2x2 merged block keeps its text in the origin cell."""
    tables = _tables(WordReader().read(build_wide_merge_package()))
    origin = tables[0].rows[0].cells[0]
    assert origin.col_span == 2
    assert origin.row_span == 2
    assert origin.text == "Wide origin"
    assert origin.is_merge_continuation is False


def test_the_continuation_of_a_wide_merge_is_flagged() -> None:
    """R4: the cell below a wide merge is a continuation with no text."""
    tables = _tables(WordReader().read(build_wide_merge_package()))
    continuation = tables[0].rows[1].cells[0]
    assert continuation.is_merge_continuation is True
    assert continuation.row_span == 1
    assert continuation.col_span == 2
    assert continuation.text == ""


def test_a_cell_inside_the_merged_region_is_not_content() -> None:
    """R4: a cell the merge covers carries no content of its own."""
    tables = _tables(WordReader().read(build_wide_merge_package()))
    ragged = tables[1]
    covered = ragged.rows[1].cells[1]
    assert covered.is_merge_continuation is True
    assert covered.text == ""
    assert "Covered by the merge" not in ragged.rows[0].cells[0].text


def test_a_cell_outside_the_merged_region_keeps_its_text() -> None:
    """R4: a merge only swallows the grid columns it covers."""
    tables = _tables(WordReader().read(build_merged_table_package()))
    two_row = tables[0]
    outside = two_row.rows[1].cells[1]
    assert outside.is_merge_continuation is False
    assert outside.text == "Row two column two"


# ============================================================================
# §6 W4 — the writer emits a merge Word can read back
# ============================================================================


def _merged_document(
    *,
    explicit_continuation: bool,
    col_span: int = 1,
    continuation_col_span: int | None = None,
) -> WordDocument:
    """A table whose first cell is a merge origin.

    ``continuation_col_span`` is the width of the continuation cell when it
    differs from the origin's: the merged region is as wide as the origin, so
    a narrower continuation must still be rendered as the whole region.
    """
    if continuation_col_span is None:
        continuation_col_span = col_span
    rows = [
        WordTableRow(
            cells=(
                WordTableCell(
                    text="origin",
                    row_span=2,
                    col_span=col_span,
                ),
                WordTableCell(text="right"),
            )
        )
    ]
    second_row_cells: list[WordTableCell] = []
    if explicit_continuation:
        second_row_cells.append(
            WordTableCell(
                text="", is_merge_continuation=True, col_span=continuation_col_span
            )
        )
    second_row_cells.append(WordTableCell(text="lower right"))
    rows.append(WordTableRow(cells=tuple(second_row_cells)))
    return WordDocument(
        blocks=(
            WordBlock(
                block_id="t1",
                kind=WordBlockKind.TABLE,
                table=WordTable(rows=tuple(rows)),
            ),
        ),
        properties=WordProperties(),
    )


def test_the_writer_emits_a_restart_merge_for_the_origin() -> None:
    """W4: the origin emits w:vMerge with val="restart", not a bare w:vMerge."""
    payload = WordWriter().write(_merged_document(explicit_continuation=True))
    xml = _xml(payload)
    assert 'w:vMerge w:val="restart"' in xml
    assert re.search(r"<w:vMerge\s*/>", xml) is not None


def test_the_writer_synthesises_a_continuation_when_the_model_omits_it() -> None:
    """W4: an origin with no explicit continuation still yields a merge."""
    payload = WordWriter().write(_merged_document(explicit_continuation=False))
    xml = _xml(payload)
    assert 'w:vMerge w:val="restart"' in xml
    assert re.search(r"<w:vMerge\s*/>", xml) is not None


def test_the_writer_does_not_double_up_on_an_explicit_continuation() -> None:
    """W4: an explicit continuation is emitted once, not twice."""
    payload = WordWriter().write(_merged_document(explicit_continuation=True))
    assert _xml(payload).count("<w:vMerge") == 2


def test_a_wide_merge_keeps_its_grid_width() -> None:
    """W4: a wide merge contributes exactly the columns it spans.

    The model has a two-column-wide origin plus one ordinary cell, so the
    table uses three grid columns — the merge must not add a fourth.
    """
    payload = WordWriter().write(
        _merged_document(explicit_continuation=True, col_span=2)
    )
    assert _grid_columns(payload) == 3


def test_a_wide_merge_round_trips_without_change() -> None:
    """Gate 3: a wide merge survives read → write → read unchanged."""
    original = _merged_document(explicit_continuation=True, col_span=2)
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    table = _tables(recovered)[0]
    assert table.rows[0].cells[0].row_span == 2
    assert table.rows[0].cells[0].col_span == 2
    assert table.rows[0].cells[0].text == "origin"
    assert table.rows[1].cells[0].is_merge_continuation is True
    assert recovered.plain_text() == original.plain_text()
    assert _grid_columns(payload) == 3


def test_a_narrow_merge_round_trips_without_change() -> None:
    """Gate 3: a one-column merge survives the round trip too."""
    original = _merged_document(explicit_continuation=True)
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    table = _tables(recovered)[0]
    assert table.rows[0].cells[0].row_span == 2
    assert table.rows[1].cells[0].is_merge_continuation is True
    assert recovered.plain_text() == original.plain_text()


def test_a_read_merge_round_trips_without_change() -> None:
    """Gate 3: a merge read from a document is written back as the same merge."""
    original = WordReader().read(build_wide_merge_package())
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    assert _grid_columns(payload) == 2
    assert recovered.plain_text() == original.plain_text()
    original_table = _tables(original)[0]
    recovered_table = _tables(recovered)[0]
    assert recovered_table.rows[0].cells[0].row_span == 2
    assert recovered_table.rows[0].cells[0].col_span == 2
    assert recovered_table.rows[1].cells[0].is_merge_continuation is True


def test_the_merger_survives_a_read_of_a_read_merge() -> None:
    """R4: the reader's own merge fixture round-trips through the writer."""
    original = WordReader().read(build_merged_table_package())
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    assert recovered.plain_text() == original.plain_text()


# ============================================================================
# §5 R4 — the merged region owns the columns its origin spans
# ============================================================================


def test_the_frozen_reader_keeps_the_cell_after_a_merged_region() -> None:
    """R4: the witness keeps a cell that lies outside the merged region.

    The merge covers the first two columns of the table, so the cell in the
    third column is an ordinary cell and its text is the table's own. Reading
    it as a continuation would delete the cell's content without a word.
    """
    table = _frozen_tables(build_merge_region_package())[0]
    assert table.rows[0].cells[0].text == "MERGED-ORIGIN"
    assert table.rows[0].cells[0].row_span == 2
    assert table.rows[1].cells[0].is_merge_continuation is True
    assert table.rows[1].cells[0].text == ""
    assert table.rows[1].cells[1].is_merge_continuation is False
    assert table.rows[1].cells[1].text == "MERGE-REGION-PAYLOAD"


def test_the_frozen_reader_gives_the_continuation_the_width_of_the_region() -> None:
    """R4: the continuation is the merged region, so it is as wide as it."""
    table = _frozen_tables(build_merge_region_package())[0]
    assert table.rows[1].cells[0].col_span == 2


def test_the_frozen_reader_keeps_a_cell_inside_the_merged_region_empty() -> None:
    """R4: a cell the merge covers carries no content of its own.

    The ragged variant of the wide-merge fixture holds a plain cell with no
    column of its own left, so it belongs to the merge and Word never
    displays its text.
    """
    ragged = _frozen_tables(build_wide_merge_package())[1]
    covered = ragged.rows[1].cells[1]
    assert covered.is_merge_continuation is True
    assert covered.text == ""


def test_the_witness_and_the_tier_agree_on_a_merged_region() -> None:
    """R4: the witness and the tier must read a merge the same way.

    The witness is the yardstick the tier is measured against: when the two
    disagree about where a merged region ends, one of them loses a cell's
    content, and nothing else in the suite notices.
    """
    package = build_merge_region_package()
    frozen = _frozen_tables(package)[0]
    live = _tables(WordReader().read(package))[0]
    assert frozen.to_wire() == live.to_wire()


def test_the_cell_after_a_merged_region_survives_a_round_trip() -> None:
    """Gate 3: the marker follows the merged region through a round trip.

    The marker is the only text in the cell that follows the merged region.
    If the reader or the writer lost the cell, the marker would be gone for
    good.
    """
    original = WordReader().read(build_merge_region_package())
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    table = _tables(recovered)[0]
    assert table.rows[0].cells[0].text == "MERGED-ORIGIN"
    assert table.rows[0].cells[0].row_span == 2
    assert table.rows[1].cells[0].is_merge_continuation is True
    assert table.rows[1].cells[1].text == "MERGE-REGION-PAYLOAD"


def test_the_writer_renders_the_region_as_wide_as_its_origin() -> None:
    """W4: a continuation narrower than its origin is still the whole region.

    The merged region is as wide as the origin that started it: a
    continuation that covered less would leave a column of the region
    uncovered, and the cell after it would be written inside the region.
    """
    document = _merged_document(
        explicit_continuation=True, col_span=2, continuation_col_span=1
    )
    payload = WordWriter().write(document)
    second_row = _xml(payload).split("</w:tr>")[1]
    assert '<w:gridSpan w:val="2"/>' in second_row
    assert "<w:vMerge/>" in second_row
    assert second_row.index("lower right") > second_row.index("<w:vMerge/>")


def test_the_writer_accepts_a_continuation_narrower_than_its_origin() -> None:
    """W12: such a model fills the grid and must not be refused.

    The region covers the origin's two columns and the cell after it takes
    the third, so every row of the table fills the grid: the table is
    well-formed and the writer has no reason to refuse it.
    """
    document = _merged_document(
        explicit_continuation=True, col_span=2, continuation_col_span=1
    )
    payload = WordWriter().write(document)
    assert _grid_columns(payload) == 3


# ============================================================================
# §6 W10 — a continuation cell that carries text is a rendering error
# ============================================================================


def test_a_continuation_cell_with_text_is_refused_by_the_writer() -> None:
    """W10: a hand-built continuation cell with text is refused, not dropped."""
    illegal = WordTableCell.model_construct(
        text="LEAKED",
        row_span=1,
        col_span=1,
        is_header=False,
        is_merge_continuation=True,
    )
    document = WordDocument.model_construct(
        blocks=(
            WordBlock.model_construct(
                block_id="t1",
                kind=WordBlockKind.TABLE,
                table=WordTable.model_construct(
                    rows=(
                        WordTableRow.model_construct(
                            cells=(
                                WordTableCell(
                                    text="origin", row_span=2, col_span=1
                                ),
                            )
                        ),
                        WordTableRow.model_construct(cells=(illegal,)),
                    ),
                    header_rows=0,
                ),
            ),
        ),
        properties=WordProperties(),
        footnotes=(),
        headers=(),
        footers=(),
    )
    with pytest.raises(WordRenderError):
        WordWriter().write(document)
