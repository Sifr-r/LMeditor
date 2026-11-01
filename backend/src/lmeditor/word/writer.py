"""Word document writer -- a door to the Kotlin tier.

This module used to *be* the writer: it built an OPC package with
``python-docx`` and mapped the shared model of :mod:`lmeditor.word.model` onto
it.  The rendering rules are the Kotlin tier's now
(``kotlin/word/WordWriter.kt``), and this module keeps only the Python API:
the same class, the same constructor, the same method and the same
exceptions, answering with the tier's bytes.

What stays here is what is genuinely Python's:

* the **refusals**.  The writer validates the document it was handed before
  anything is rendered: the model's own validators run again, and the writer's
  extra checks run on top of them.  A document that breaks the contract is
  refused with a :class:`~lmeditor.core.exceptions.WordRenderError`, exactly
  as it was, and it is refused *before* the tier is asked to render anything.
* the **wire form**.  The document is described to the tier by the model
  itself, in the shape ``kotlin/word/WIRE.md`` pins.

There is deliberately **no fallback** to the Python implementation.  If the
tier is not there, the writer says so.

The frozen original lives in :mod:`lmeditor._reference.word_writer`.  It is the
yardstick the parity harness measures the tier against.  It is re-aligned to
the tier's semantics only when the tier's rule *deliberately* changes, and
every such change is recorded in the harness's ``REFERENCE_WITNESSES``.
"""

from __future__ import annotations

from typing import Final

from pydantic import ValidationError

from lmeditor.bridge.errors import BridgeUnavailableError
from lmeditor.core.exceptions import WordRenderError
from lmeditor.tier.word import write_document_result
from lmeditor.word.model import (
    WordBlock,
    WordDocument,
    WordTable,
    WordTableCell,
    WordTableRow,
)

#: A single section can carry at most one header and one footer of each of the
#: three reference types (default, first page, even page).
_MAX_HEADERS: Final[int] = 3

#: The cell a synthesised merge continuation is rendered with.
_EMPTY_CELL: Final[WordTableCell] = WordTableCell(text="")

__all__ = ["WordWriter"]


class WordWriter:
    """Render a :class:`WordDocument` into the bytes of a ``.docx`` package.

    The writer is stateless between calls: the same document always produces
    the same package.  With ``deterministic=True`` (the default) the output is
    byte-identical across runs, which is what makes the round-trip property
    testable.

    The rendering itself is the Kotlin tier's.  This class is the door onto it:
    it keeps the API, the refusals and the registers, and it answers with the
    tier's bytes.

    Anything the writer cannot represent is recorded in
    :attr:`last_omissions`, the same register the reader uses.  A construct
    the model cannot carry is never dropped in silence: it is either rendered
    or reported.
    """

    def __init__(self, *, deterministic: bool = True) -> None:
        """Create a writer.

        Args:
            deterministic: When true, every timestamp in the package is pinned
                to a fixed epoch so that the same document always yields the
                same bytes.  When false, the current time is used.
        """
        self.last_omissions: list[str] = []
        self._deterministic = deterministic

    def write(self, document: WordDocument) -> bytes:
        """Render ``document`` and return the bytes of a ``.docx`` package.

        Raises:
            WordRenderError: If the document cannot be rendered into a valid
                package.  The writer never emits a malformed package to make
                a document fit.
            BridgeUnavailableError: If the Kotlin tier is not there. The writer
                never falls back to a Python implementation of its own rules.
        """
        self.last_omissions = []
        try:
            checked = self._revalidate(document)
            self._validate(checked)
            result = write_document_result(checked, deterministic=self._deterministic)
            self.last_omissions = list(result.omissions)
            return result.data
        except WordRenderError:
            raise
        except BridgeUnavailableError:
            # The tier is not there. That is not a rendering failure: the
            # writer says so and refuses to fall back to a Python
            # implementation of its own rules.
            raise
        except Exception as exc:
            raise WordRenderError(
                f"unexpected failure while rendering the document: {exc!r}"
            ) from exc

    # ------------------------------------------------------------------
    # The refusals
    # ------------------------------------------------------------------

    def _revalidate(self, document: WordDocument) -> WordDocument:
        """Re-validate ``document`` against the model's own validators.

        The contract (W10) makes the writer responsible for the model it was
        handed: a document that bypassed construction-time validation (for
        example through ``model_construct``) must be refused rather than
        rendered with its illegal parts silently dropped.  Re-validating the
        dumped model through :class:`WordDocument` re-runs every field and
        model validator of §3.1 and §3.2, so the check cannot drift away
        from the model it guards.

        The check runs before the tier is asked to render anything, so a
        rejected document never yields bytes.  The re-validated document is
        what is rendered: it is the model that actually passed the check, so a
        value the validators coerce (an enum spelled as a string, say) is
        rendered as the value the model defines.
        """
        try:
            # ``warnings=False`` silences the serializer's complaint about a
            # hand-built model holding plain dictionaries; the model is
            # rejected right afterwards, so the diagnostic adds nothing.
            payload = document.model_dump(warnings=False)
        except Exception as exc:
            raise WordRenderError(
                f"the document model could not be read for validation: {exc!r}"
            ) from exc
        try:
            return WordDocument.model_validate(payload)
        except ValidationError as exc:
            raise WordRenderError(
                "the document model violates the contract invariants: "
                f"{_format_validation_errors(exc, document)}"
            ) from exc
        except Exception as exc:
            raise WordRenderError(
                f"the document model could not be validated: {exc!r}"
            ) from exc

    def _validate(self, document: WordDocument) -> None:
        """Reject documents that cannot be rendered into a valid package."""
        seen_footnotes: set[str] = set()
        for footnote in document.footnotes:
            if footnote.footnote_id in seen_footnotes:
                raise WordRenderError(
                    f"duplicate footnote id {footnote.footnote_id!r}"
                )
            seen_footnotes.add(footnote.footnote_id)

        known = {footnote.footnote_id for footnote in document.footnotes}
        for block in document.blocks:
            for run in block.runs:
                if run.footnote_ref is None:
                    continue
                if run.footnote_ref not in known:
                    raise WordRenderError(
                        f"block {block.block_id!r} references unknown footnote "
                        f"{run.footnote_ref!r}"
                    )
            self._validate_table_cells(block)
            self._validate_table_grid(block)

        if len(document.headers) > _MAX_HEADERS or len(document.footers) > _MAX_HEADERS:
            # The reference path writes one header per *type*, so a document
            # with several sections is not the problem this limit guards
            # against: it is a document that names more headers than Word has
            # roles for. The positional path has no such distinction.
            if not self._is_reference_driven(document):
                raise WordRenderError(
                    f"a single-section document can carry at most {_MAX_HEADERS} "
                    f"headers, got {len(document.headers)}"
                )

    @staticmethod
    def _validate_table_cells(block: WordBlock) -> None:
        """Refuse a table cell that breaks the merge rules (D4, I9).

        A continuation cell carries the content of the cell that *starts* the
        merge; text stored in one is content no renderer can display, and
        Word ignores it.  The model refuses such a cell at construction time
        (W10's re-validation catches a hand-built one), and this check makes
        the writer's own refusal explicit rather than a silent drop.
        """
        table = block.table
        if table is None:
            return
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                if not cell.is_merge_continuation:
                    continue
                if cell.row_span != 1:
                    raise WordRenderError(
                        f"table cell {row_index}:{cell_index} continues a merge "
                        f"and spans {cell.row_span} rows; a continuation spans "
                        "a single row"
                    )
                if cell.text:
                    raise WordRenderError(
                        f"table cell {row_index}:{cell_index} continues a merge "
                        "and must not carry text of its own"
                    )

    @staticmethod
    def _validate_table_grid(block: WordBlock) -> None:
        """Refuse a table whose rows do not all fill the grid (W12).

        A Word table's grid is as wide as its widest row, and **every** row
        must fill that grid.  A row that falls short is a malformed table, and
        Word does not refuse it: it silently re-lays the table out, so the
        document the reader opens is not the document the model describes.
        The writer refuses it instead of emitting something Word will change.

        The check measures the grid the model lays out on, so a row that
        falls short of it is refused.  The grid is measured the way the tier
        measures it -- the merged region covers the columns its origin spans,
        less the columns the continuation cells that follow it inside the
        region cover -- so a ragged region tiles the grid instead of falling
        short of it, and the door measures the grid the tier will produce.
        """
        table = block.table
        if table is None:
            return
        layout = _layout_table(table)
        grid_width = max(
            (
                placement.column + placement.col_span
                for row in layout
                for placement in row
            ),
            default=0,
        )
        for row_index, placements in enumerate(layout):
            covered = max(
                (placement.column + placement.col_span for placement in placements),
                default=0,
            )
            if covered != grid_width:
                raise WordRenderError(
                    f"table block {block.block_id!r} row {row_index} covers "
                    f"{covered} of {grid_width} grid columns; every row of a "
                    "Word table must fill the grid"
                )

    @staticmethod
    def _is_reference_driven(document: WordDocument) -> bool:
        """Report whether the document names its headers through sections.

        A document that does is matched by reference type, so it has no
        "more headers than Word has roles for" failure mode.
        """
        return bool(document.sections) and bool(
            document.sections[0].header_refs or document.sections[0].footer_refs
        )


# ---------------------------------------------------------------------------
# The table layout the grid check measures
# ---------------------------------------------------------------------------


class _CellPlacement:
    """One table cell, positioned in the grid."""

    __slots__ = ("column", "col_span", "cell", "vertical_merge")

    def __init__(
        self,
        cell: WordTableCell,
        col_span: int,
        vertical_merge: str | None,
        column: int = 0,
    ) -> None:
        self.cell = cell
        self.col_span = col_span
        self.vertical_merge = vertical_merge
        self.column = column


class _MergeOrigin:
    """A vertical merge started by a cell, tracked over the rows it covers."""

    __slots__ = ("column", "col_span", "last_row")

    def __init__(self, column: int, col_span: int, last_row: int) -> None:
        self.column = column
        self.col_span = col_span
        self.last_row = last_row


def _layout_table(table: WordTable) -> list[list[_CellPlacement]]:
    """Lay every row of the table out on the grid, in column order.

    A table's grid is made of *grid columns*, not of cells: a cell occupies
    ``col_span`` columns starting at the column it is placed on, and the grid
    is exactly as wide as the widest row needs it to be.

    A vertical merge is described twice in the model and the two mechanisms
    cooperate here:

    * the cell that *starts* a merge carries ``row_span > 1`` and keeps its
      text; it marks its columns as covered in the rows below;
    * the cells below it are flagged ``is_merge_continuation`` and carry no
      text of their own.

    A continuation cell is placed on the column its merge started in — that
    is the column the merge continues in — and the merged region covers the
    columns its origin spans, less the columns the continuation cells that
    follow it inside the region cover, so the cells that follow it start after
    the region and keep their own text.  When the model supplies the
    continuation cells, they are consumed at the covered grid position, so the
    two mechanisms never double up.  When it does not — a hand-authored merge
    that only names the origin — the writer synthesises the continuation cells,
    so the merge is rendered as a merge instead of being flattened away.

    The layout is the reader's layout, read backwards.  The reader reads a
    cell that lands inside a merged region back as a continuation of that
    region, so the model holds the cells of a ragged region as continuations:
    the region the reader gave the model was that much narrower than the origin
    it continues.  The writer lays the region out that much narrower, and
    writes those cells as ordinary cells inside it, so the row tiles the grid
    and reading it back gives the model the writer was handed.  A cell the
    model does not mark as a continuation is never written inside a region:
    the reader would swallow its text.

    This is the rule of the frozen oracle
    (:mod:`lmeditor._reference.word_writer`) and of the Kotlin tier, and it is
    what makes the grid the door measures the grid the tier produces: the door
    measures the tier's grid, so it does not refuse a document the tier would
    render correctly.
    """
    origins: list[_MergeOrigin] = []
    layout: list[list[_CellPlacement]] = []
    for row_index, row in enumerate(table.rows):
        placements, new_origins = _place_row(row, origins, row_index)
        layout.append(placements)
        origins.extend(new_origins)
    return layout


def _width_inside(cells: tuple[WordTableCell, ...], start: int) -> int:
    """The width the continuation cells that follow a merged region cover inside it.

    The reader reads a cell that lands inside a merged region back as a
    continuation of that region, so the model holds those cells as
    continuations: the region the reader gave the model was that much narrower
    than the origin it continues.  The writer lays the region out that much
    narrower, so the cells that follow tile the rest of the region and the row
    keeps the width the grid has.

    Only the continuations that follow the region *immediately* are counted: a
    cell that is not a continuation ends the region, because the writer places
    such a cell outside the region, where the reader keeps its text.
    """
    width = 0
    for cell in cells[start:]:
        if not cell.is_merge_continuation:
            break
        width += cell.col_span
    return width


def _place_row(
    row: WordTableRow,
    origins: list[_MergeOrigin],
    row_index: int,
) -> tuple[list[_CellPlacement], list[_MergeOrigin]]:
    """Place the cells of one row on the grid.

    Returns the row's placements in column order together with the merges
    the row starts.
    """
    open_merges = sorted(
        (origin for origin in origins if row_index < origin.last_row),
        key=lambda origin: origin.column,
    )

    taken: set[int] = set()
    matched: list[tuple[int, int]] = []
    loose: list[WordTableCell] = []
    covered: list[tuple[WordTableCell, int]] = []
    used: set[int] = set()
    region: _MergeOrigin | None = None
    column = 0
    for cell_index, cell in enumerate(row.cells):
        if cell.is_merge_continuation:
            index = _match_merge(open_merges, used, column)
            if index is None:
                open_region = region
                if open_region is not None and column < open_region.column + open_region.col_span:
                    # A continuation cell that continues nothing but lands
                    # inside the merged region the row has already continued
                    # belongs to that region: the reader reads a cell inside a
                    # merged region back as a continuation of it, so it is
                    # written as an ordinary cell -- inside the region, so the
                    # row keeps the width the grid has.
                    covered.append((cell, column))
                    taken.update(range(column, column + cell.col_span))
                    column += cell.col_span
                    continue
                # A continuation cell without a merge above it continues
                # nothing: it is placed like any other cell and is still
                # rendered as a continuing merge rather than dropped.
                loose.append(cell)
                column += cell.col_span
                continue
            used.add(index)
            merge = open_merges[index]
            # The merged region covers the columns its origin spans, less the
            # columns the continuation cells that follow it inside the region
            # cover: the reader read those cells back as continuations of the
            # region, so the region it gave the model was that much narrower
            # than the origin, and the cells that follow tile the rest of it. A
            # row that follows a region with an ordinary cell keeps the region
            # as wide as its origin, so that the ordinary cell lands outside it
            # and keeps its text.
            width = max(1, merge.col_span - _width_inside(row.cells, cell_index + 1))
            matched.append((merge.column, width))
            taken.update(range(merge.column, merge.column + width))
            region = merge
            column = max(column, merge.column + width)
            continue
        loose.append(cell)
        column += cell.col_span

    # A merge the row does not continue receives a synthesised continuation,
    # so that a model naming only the origin still renders as a merge.
    for index, merge in enumerate(open_merges):
        if index in used:
            continue
        matched.append((merge.column, merge.col_span))
        taken.update(range(merge.column, merge.column + merge.col_span))

    placements: list[_CellPlacement] = []
    new_origins: list[_MergeOrigin] = []
    cursor = 0
    for cell in loose:
        position = _next_free(taken, cursor)
        taken.update(range(position, position + cell.col_span))
        cursor = position + cell.col_span
        if cell.is_merge_continuation:
            placements.append(
                _CellPlacement(cell, cell.col_span, "continue", position)
            )
            continue
        starts_merge = cell.row_span > 1
        placements.append(
            _CellPlacement(
                cell,
                cell.col_span,
                "restart" if starts_merge else None,
                position,
            )
        )
        if starts_merge:
            new_origins.append(
                _MergeOrigin(position, cell.col_span, row_index + cell.row_span)
            )
    for position, col_span in matched:
        placements.append(_CellPlacement(_EMPTY_CELL, col_span, "continue", position))
    for cell, position in covered:
        # The cell sits inside the merged region it belongs to, so it is
        # written as an ordinary cell: the reader reads a cell inside a merged
        # region back as a continuation of that region.
        placements.append(_CellPlacement(cell, cell.col_span, None, position))
    placements.sort(key=lambda placement: placement.column)
    return placements, new_origins


def _match_merge(
    open_merges: list[_MergeOrigin],
    used: set[int],
    column: int,
) -> int | None:
    """Return the index of the merge a continuation cell continues.

    The merge whose region contains ``column`` wins.  A merge that starts at
    or after the column comes next, and the leftmost merge still free is the
    last resort — the merge survives even when the model puts the
    continuation cell somewhere else in its row.  ``None`` means that the
    cell continues no merge at all.
    """
    for index, merge in enumerate(open_merges):
        if index in used:
            continue
        if merge.column <= column < merge.column + merge.col_span:
            return index
    for index, merge in enumerate(open_merges):
        if index in used:
            continue
        if merge.column >= column:
            return index
    for index in range(len(open_merges)):
        if index not in used:
            return index
    return None


def _next_free(taken: set[int], cursor: int) -> int:
    """Return the first column at or after ``cursor`` that is still free."""
    column = cursor
    while column in taken:
        column += 1
    return column


# ---------------------------------------------------------------------------
# Naming the part of a document a validation failure points at
# ---------------------------------------------------------------------------


def _format_validation_errors(exc: ValidationError, document: WordDocument) -> str:
    """Summarise a validation failure, naming every offending part."""
    parts: list[str] = []
    for error in exc.errors():
        location = _error_location(error["loc"], document)
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def _error_location(
    location: tuple[int | str, ...],
    document: WordDocument,
) -> str:
    """Name the part of the document a validation error points at."""
    path = ".".join(str(item) for item in location)
    if len(location) >= 2 and location[0] == "blocks" and isinstance(location[1], int):
        index = location[1]
        if 0 <= index < len(document.blocks):
            return f"{path} (block_id={document.blocks[index].block_id!r})"
    return path or "document"
