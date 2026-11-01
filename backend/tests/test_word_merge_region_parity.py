"""The frozen witness and the Kotlin tier agree on a ragged merged region.

A merged region is as wide as its origin spans, but only as wide as the row
has room for: the reader clamps the continuation it hands the model to the
room the row had.  A row that therefore holds a cell the merged region covers
is *ragged*.  The writer has to lay such a row out the way the reader reads it
back, or the round trip changes the shape of the table and hands back a table
whose rows do not fill its grid (W12) -- a table Word re-lays out, so the
document the reader opens is no longer the document the model describes.

The parity harness (``backend/scripts/verify_kotlin_parity.py``) has no merged
table among its fixtures, so this is the coverage it lacks: both writers are
given the same document, each package is read back by the frozen reader -- the
judge -- and the two documents have to agree.
"""

from __future__ import annotations

from typing import Any

import pytest

from lmeditor._reference.word_reader import WordReader as FrozenReader
from lmeditor._reference.word_writer import WordWriter as FrozenWriter
from lmeditor.tier import word as tier_word
from lmeditor.word.model import WordDocument
from tests.word_fixtures import (
    build_merge_region_package,
    build_wide_merge_package,
)


def _table_shapes(document: WordDocument) -> list[list[list[str]]]:
    """The shape of every table in a document.

    The shape is what a table *means*: the spans of its cells, whether they
    continue a merge, and the text they carry.  The packages the two writers
    build cannot be compared byte for byte -- ``python-docx`` and the hand
    written OOXML of the Kotlin tier lay the same document out differently --
    so they are compared on what they mean.
    """
    return [
        [
            [
                f"{cell.col_span}/{cell.row_span}/{cell.is_merge_continuation}/{cell.text}"
                for cell in row.cells
            ]
            for row in table.rows
        ]
        for block in document.blocks
        if block.table is not None
        for table in [block.table]
    ]


def _read_back(package: bytes) -> WordDocument:
    """Read a written package with the frozen reader, the judge of both writers."""
    return FrozenReader().read_bytes(package)


@pytest.mark.usefixtures("kotlin_tier")
@pytest.mark.parametrize(
    "builder",
    [build_wide_merge_package, build_merge_region_package],
    ids=["wide-merge", "merge-region"],
)
def test_the_frozen_witness_and_the_kotlin_tier_agree_on_a_merged_region(
    builder: Any,
) -> None:
    """Both writers are given the same document and mean the same thing by it."""
    source = FrozenReader().read_bytes(builder())

    witness = _read_back(FrozenWriter().write(source))
    tier = _read_back(tier_word.write_document(source))

    assert _table_shapes(witness) == _table_shapes(tier)


@pytest.mark.usefixtures("kotlin_tier")
def test_a_merged_region_survives_the_round_trip_in_both_tiers() -> None:
    """A ragged merged region keeps its shape: the round trip changes nothing."""
    source = FrozenReader().read_bytes(build_wide_merge_package())

    witness = _read_back(FrozenWriter().write(source))
    tier = _read_back(tier_word.write_document(source))

    assert _table_shapes(tier) == _table_shapes(source)
    assert _table_shapes(witness) == _table_shapes(source)


@pytest.mark.usefixtures("kotlin_tier")
def test_the_ragged_region_keeps_the_grid_it_declares() -> None:
    """The ragged table keeps its two columns, and every row fills them.

    The second table of the wide-merge fixture is the ragged variant: the
    origin spans the whole grid and the row below holds a cell the merge
    covers.  A writer that places that cell after the region instead of inside
    it widens the grid, and the row that started the merge no longer fills it.
    """
    source = FrozenReader().read_bytes(build_wide_merge_package())

    tier = _read_back(tier_word.write_document(source))
    grids = [len(table.column_widths) for table in _tables(tier)]

    assert grids == [2, 2], "the ragged table keeps the two columns it declares"


def _tables(document: WordDocument) -> list[Any]:
    """Every table in a document, in document order."""
    return [block.table for block in document.blocks if block.table is not None]
