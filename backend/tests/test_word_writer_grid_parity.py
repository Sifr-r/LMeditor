"""The live door measures the grid the Kotlin tier produces (W16).

The writer in :mod:`lmeditor.word.writer` is a *door*: it refuses a document
whose table rows do not all fill the table's grid, and it does so **before**
the tier is asked to render anything.  The grid it measures therefore has to
be the grid the tier lays the table out on -- otherwise the door measures a
table that does not exist:

* it refuses documents the tier renders correctly, because it measures a grid
  wider than the tier's, and
* it accepts documents whose rows do not fill the grid the tier produces,
  because it measures a grid narrower than the tier's.

W12 gave the tier -- and the frozen oracle, which is the tier's plain-Python
twin -- the rule for a **ragged** merged region: the region covers the columns
its origin spans, less the columns the continuation cells that follow it
inside the region cover.  The door kept the older rule, so for a ragged
region the two measured different grids.  This module holds the door to the
tier's rule:

* the grid the door measures is compared with the grid the tier **renders**
  (the number of ``w:gridCol`` the written table declares), and
* the door's refusal is compared with what the tier laid out: the door must
  refuse a table some row of which does not fill the grid, and must not
  refuse a table every row of which does.

The documents are the ones the divergence shows on: a region whose row carries
a continuation wider than the region, a region that follows an ordinary cell,
and a table whose row simply falls short of the grid -- together with the
committed fixture, a merge that covers two of the table's three columns, which
the door and the tier measure alike.

The tier's *layout* -- which grid column every cell sits on -- is not visible
in the rendered package: a rendered row sums the spans of its cells, and for
a model whose continuation is wider than its region that sum is not the
coverage the layout gives.  The layout is therefore compared with the frozen
oracle, which is the tier's layout in Python, while the *grid* is compared
with the tier's rendered package.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Final

import pytest

from lmeditor._reference import word_writer as frozen_writer
from lmeditor.core.exceptions import WordRenderError
from lmeditor.tier import word as tier_word
from lmeditor.word import writer as door
from lmeditor.word.model import (
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.writer import WordWriter

#: The ragged merged region, committed as a package.
FIXTURE: Final[Path] = (
    Path(__file__).parent / "fixtures" / "word_merge_region.docx"
)


# ---------------------------------------------------------------------------
# The measurements
# ---------------------------------------------------------------------------


def _coverage(layout: list[list[object]]) -> list[int]:
    """The grid columns every row of a layout covers."""
    return [
        max(
            (
                placement.column + placement.col_span  # type: ignore[attr-defined]
                for placement in placements
            ),
            default=0,
        )
        for placements in layout
    ]


def _door_coverage(table: WordTable) -> list[int]:
    """The grid columns every row covers, as the **door** measures them."""
    return _coverage(door._layout_table(table))


def _tier_coverage(table: WordTable) -> list[int]:
    """The grid columns every row covers, as the **tier** lays them out.

    The frozen oracle is the tier's layout in Python: it is the yardstick the
    parity harness measures the tier against. It is plain Python and it never
    calls the tier; it is re-aligned to the tier's *current* semantics only
    when the tier's rule deliberately changes, and every such change is
    recorded in the harness's ``REFERENCE_WITNESSES``.
    """
    return _coverage(frozen_writer._layout_table(table))


def _rendered_tables(payload: bytes) -> list[tuple[int, list[int]]]:
    """The grid and the row widths of every table the tier rendered.

    The grid is the number of ``w:gridCol`` the written table declares -- the
    grid the tier produced.  The row widths are the spans the written cells
    sum to, which is what the package says about the row.
    """
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    tables: list[tuple[int, list[int]]] = []
    for table in re.findall(r"<w:tbl>.*?</w:tbl>", xml, re.DOTALL):
        grid = table.count("<w:gridCol")
        rows: list[int] = []
        for row in re.findall(r"<w:tr>.*?</w:tr>", table, re.DOTALL):
            width = 0
            for cell in re.findall(r"<w:tc>.*?</w:tc>", row, re.DOTALL):
                span = re.search(r'<w:gridSpan w:val="(\d+)"/>', cell)
                width += int(span.group(1)) if span else 1
            rows.append(width)
        tables.append((grid, rows))
    return tables


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------


def _document(rows: list[list[WordTableCell]]) -> WordDocument:
    """A document holding one table."""
    return WordDocument(
        blocks=[
            WordBlock(
                block_id="b0001",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=[WordTableRow(cells=cells) for cells in rows]
                ),
            )
        ]
    )


def _merge_region_document() -> WordDocument:
    """The committed ragged merge region, read the way production reads it."""
    return tier_word.read_document(FIXTURE.read_bytes()).document


def _wide_merge_document() -> WordDocument:
    """The wide-merge package: a merged block and a ragged region."""
    from tests.word_fixtures import build_wide_merge_package

    return tier_word.read_document(build_wide_merge_package()).document


def _continuation_wider_than_the_region() -> WordDocument:
    """A region of two columns whose row carries a two-column continuation.

    The origin spans two columns, so the region is as wide as its origin; the
    continuation that follows it inside the region is narrower than the
    origin, and the second continuation is wider than the region is.  The tier
    lays the row out over three columns, and the row that started the merge
    covers only two of them.
    """
    return _document(
        [
            [WordTableCell(text="ORIGIN", row_span=2, col_span=2)],
            [
                WordTableCell(col_span=1, is_merge_continuation=True),
                WordTableCell(col_span=2, is_merge_continuation=True),
            ],
        ]
    )


def _region_after_an_ordinary_cell() -> WordDocument:
    """A region that follows an ordinary cell in the row that continues it.

    The origin is the second cell of the first row, so the merge covers the
    second column of the grid; the row that continues it carries a
    continuation that covers the region and one that is wider than it.  The
    tier lays the row out over two columns, and every row fills them.
    """
    return _document(
        [
            [
                WordTableCell(text="ORDINARY"),
                WordTableCell(text="ORIGIN", row_span=2),
            ],
            [
                WordTableCell(col_span=1, is_merge_continuation=True),
                WordTableCell(col_span=2, is_merge_continuation=True),
            ],
        ]
    )


def _row_that_falls_short() -> WordDocument:
    """A table whose second row covers one of the grid's two columns."""
    return _document(
        [
            [WordTableCell(text="a"), WordTableCell(text="b")],
            [WordTableCell(text="c")],
        ]
    )


#: The documents the divergence shows on, with a builder for each.
CASES: Final[tuple[tuple[str, Callable[[], WordDocument]], ...]] = (
    ("merge-region", _merge_region_document),
    ("wide-merge", _wide_merge_document),
    ("continuation-wider-than-the-region", _continuation_wider_than_the_region),
    ("region-after-an-ordinary-cell", _region_after_an_ordinary_cell),
    ("row-that-falls-short", _row_that_falls_short),
)


def _tables(document: WordDocument) -> list[WordTable]:
    """Every table of a document, in document order."""
    return [
        block.table for block in document.blocks if block.table is not None
    ]


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("kotlin_tier")
@pytest.mark.parametrize(
    ("name", "builder"),
    CASES,
    ids=[name for name, _ in CASES],
)
def test_the_door_measures_the_grid_the_tier_produces(
    name: str,
    builder: Callable[[], WordDocument],
) -> None:
    """The grid the door measures is the grid the tier renders.

    The door's grid is the one it refuses on; the tier's grid is the one the
    written package declares.  A door that measured a different grid would
    refuse documents the tier renders and accept documents the tier renders
    malformed.
    """
    document = builder()

    rendered = _rendered_tables(tier_word.write_document(document))

    for table, (tier_grid, _tier_rows) in zip(_tables(document), rendered, strict=True):
        door_grid = max(_door_coverage(table), default=0)
        assert door_grid == tier_grid, (
            f"{name}: the door measured a grid of {door_grid} columns, "
            f"the tier rendered a grid of {tier_grid} columns"
        )


@pytest.mark.usefixtures("kotlin_tier")
@pytest.mark.parametrize(
    ("name", "builder"),
    CASES,
    ids=[name for name, _ in CASES],
)
def test_the_door_lays_a_row_out_the_way_the_tier_does(
    name: str,
    builder: Callable[[], WordDocument],
) -> None:
    """The door covers the same columns per row as the tier lays them out."""
    document = builder()

    for table in _tables(document):
        assert _door_coverage(table) == _tier_coverage(table), (
            f"{name}: the door covered {_door_coverage(table)} columns per row, "
            f"the tier laid out {_tier_coverage(table)}"
        )


@pytest.mark.usefixtures("kotlin_tier")
@pytest.mark.parametrize(
    ("name", "builder"),
    CASES,
    ids=[name for name, _ in CASES],
)
def test_the_door_refuses_exactly_the_tables_a_row_does_not_fill(
    name: str,
    builder: Callable[[], WordDocument],
) -> None:
    """The door refuses a table some row of which does not fill the grid.

    And it does not refuse a table every row of which does: that refusal is
    the door's own rule, and it may not fire on a table the tier renders
    correctly.
    """
    document = builder()

    malformed = False
    for table in _tables(document):
        coverage = _tier_coverage(table)
        grid = max(coverage, default=0)
        malformed = malformed or any(width != grid for width in coverage)

    if malformed:
        with pytest.raises(WordRenderError, match="grid columns"):
            WordWriter().write(document)
    else:
        WordWriter().write(document)


def test_the_ragged_merged_region_is_not_refused() -> None:
    """The merged region of the fixture is a table the door must accept.

    The origin of the region spans two of the table's three columns, and the
    row below continues the region over those two and carries a plain cell in
    the third, so every row tiles the three columns of the grid.  The tier
    lays the row out over the three columns of the grid and the door measures
    the same three, so the door has no short row to refuse.

    The refusal itself is asserted by the parametrized case
    ``test_the_door_refuses_exactly_the_tables_a_row_does_not_fill``; what this
    test pins is the coverage the tier and the door measure on the fixture.
    """
    from lmeditor._reference.word_reader import WordReader as FrozenReader

    document = FrozenReader().read_bytes(FIXTURE.read_bytes())
    table = _tables(document)[0]

    coverage = _tier_coverage(table)
    assert coverage == [3, 3], (
        f"the ragged region tiles the three columns of the grid: {coverage}"
    )
    assert _door_coverage(table) == [3, 3]


def test_a_row_that_falls_short_of_the_grid_is_refused() -> None:
    """A row that does not fill the grid is still refused, with the message."""
    with pytest.raises(WordRenderError, match="row 1 covers 1 of 2 grid columns"):
        WordWriter().write(_row_that_falls_short())


def test_the_committed_fixture_is_the_ragged_merge_region_package() -> None:
    """The committed fixture is the package the shared builder builds.

    The fixture is a file on disk, so the parity harness can read it; the
    builder is the source of truth, so the two cannot drift apart silently.

    The comparison is over the package's members -- their names, their
    order, their metadata and their contents -- and not over its bytes. A
    DEFLATE stream is the output of the compressor that produced it, and
    the stock zlib and the zlib-ng a Linux runner links against do not
    produce the same stream for the same input. The two packages hold the
    same document on every platform; the compressed form is not the same
    bytes, so asserting on it made this test fail on Linux for a reason
    that had nothing to do with the document.
    """
    from tests.word_fixtures import build_merge_region_package, package_members

    assert package_members(FIXTURE.read_bytes()) == package_members(build_merge_region_package())
