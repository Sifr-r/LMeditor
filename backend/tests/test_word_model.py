"""Whitebox tests for the shared Word document model.

These tests are whitebox by construction: they assert the contract's invariants
(§3.1 I1-I8 and §3.2 D1-D3) directly, including the *failing* case for every
invariant, and they assert the plain-text rendering rules (§4 T5-T9) one by one.

They are deliberately independent of the reader and the writer: the model is the
contract between them, so it must hold on its own.
"""

from __future__ import annotations

import pytest

from lmeditor.word.model import (
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordFootnote,
    WordRun,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.text import normalize_whitespace


def _paragraph(block_id: str = "b1", text: str = "Body text.") -> WordBlock:
    """Build a simple PARAGRAPH block."""
    return WordBlock(block_id=block_id, kind=WordBlockKind.PARAGRAPH, text=text)


def _table(block_id: str = "t1") -> WordBlock:
    """Build a two-by-two TABLE block with a header row."""
    table = WordTable(
        rows=(
            WordTableRow(
                cells=(
                    WordTableCell(text="Evidence", is_header=True),
                    WordTableCell(text="Verdict", is_header=True),
                )
            ),
            WordTableRow(
                cells=(
                    WordTableCell(text="chunk-abc"),
                    WordTableCell(text="entailed"),
                )
            ),
        ),
        header_rows=1,
    )
    return WordBlock(block_id=block_id, kind=WordBlockKind.TABLE, table=table)


# ============================================================================
# §3.1 I1 - a TABLE block requires a table payload, and only a TABLE may have one
# ============================================================================


def test_table_block_requires_a_table_payload() -> None:
    """I1: a TABLE block without a payload is rejected."""
    with pytest.raises(ValueError, match="requires a table payload"):
        WordBlock(block_id="t1", kind=WordBlockKind.TABLE)


def test_non_table_block_rejects_a_table_payload() -> None:
    """I1: a PARAGRAPH block carrying a table payload is rejected."""
    with pytest.raises(ValueError, match="must not carry a table payload"):
        WordBlock(
            block_id="p1",
            kind=WordBlockKind.PARAGRAPH,
            text="Body.",
            table=WordTable(),
        )


# ============================================================================
# §3.1 I2 - a TABLE block carries no inline text or runs
# ============================================================================


def test_table_block_rejects_inline_text() -> None:
    """I2: a TABLE block with text is rejected."""
    with pytest.raises(ValueError, match="carries no inline text or runs"):
        WordBlock(
            block_id="t1",
            kind=WordBlockKind.TABLE,
            text="Evidence",
            table=WordTable(),
        )


def test_table_block_rejects_inline_runs() -> None:
    """I2: a TABLE block with runs is rejected."""
    with pytest.raises(ValueError, match="carries no inline text or runs"):
        WordBlock(
            block_id="t1",
            kind=WordBlockKind.TABLE,
            runs=(WordRun(text="Evidence"),),
            table=WordTable(),
        )


# ============================================================================
# §3.1 I3 - list items require a marker, and only list items may carry one
# ============================================================================


def test_list_item_requires_a_marker() -> None:
    """I3: a LIST_ITEM block without a marker is rejected."""
    with pytest.raises(ValueError, match="requires a list marker"):
        WordBlock(block_id="l1", kind=WordBlockKind.LIST_ITEM, text="Item.")


def test_non_list_block_rejects_a_marker() -> None:
    """I3: a PARAGRAPH block carrying a list marker is rejected."""
    with pytest.raises(ValueError, match="must not carry a list marker"):
        WordBlock(
            block_id="p1",
            kind=WordBlockKind.PARAGRAPH,
            text="Body.",
            list_marker=ListMarker.BULLET,
        )


# ============================================================================
# §3.1 I4 - heading levels are 1..9
# ============================================================================


@pytest.mark.parametrize("level", [0, 10])
def test_heading_level_outside_one_to_nine_is_rejected(level: int) -> None:
    """I4: a HEADING block must sit between level 1 and level 9."""
    with pytest.raises(ValueError, match="level between 1 and 9"):
        WordBlock(
            block_id="h1",
            kind=WordBlockKind.HEADING,
            text="Heading.",
            level=level,
        )


def test_negative_heading_level_is_rejected_by_the_field_bound() -> None:
    """I4: a negative level is refused by the field constraint before the validator."""
    with pytest.raises(ValueError):
        WordBlock(
            block_id="h1",
            kind=WordBlockKind.HEADING,
            text="Heading.",
            level=-1,
        )


def test_heading_accepts_every_valid_level() -> None:
    """I4: levels 1 through 9 are all accepted."""
    for level in range(1, 10):
        block = WordBlock(
            block_id=f"h{level}",
            kind=WordBlockKind.HEADING,
            text="Heading.",
            level=level,
        )
        assert block.level == level


# ============================================================================
# §3.1 I5 - flat blocks carry level 0
# ============================================================================


@pytest.mark.parametrize(
    "kind",
    [
        WordBlockKind.TITLE,
        WordBlockKind.PARAGRAPH,
        WordBlockKind.QUOTE,
        WordBlockKind.CODE,
    ],
)
def test_flat_block_rejects_a_non_zero_level(kind: WordBlockKind) -> None:
    """I5: TITLE/PARAGRAPH/QUOTE/CODE blocks must have level 0."""
    with pytest.raises(ValueError, match="must have level 0"):
        WordBlock(block_id="b1", kind=kind, text="Text.", level=2)


# ============================================================================
# §3.1 I10 - the nesting depth of a list item is bounded
# ============================================================================


def test_a_list_item_cannot_nest_deeper_than_eight() -> None:
    """I10: a list item's nesting depth is bounded at eight.

    Word's numbering expresses nine levels (`w:ilvl` 0-8).  A deeper item
    would be handed to a renderer that cannot express it, so the model
    refuses it at construction instead of letting it fail silently later.
    """
    with pytest.raises(ValueError, match="cannot nest deeper than 8"):
        WordBlock(
            block_id="l9",
            kind=WordBlockKind.LIST_ITEM,
            text="Too deep.",
            level=9,
            list_marker=ListMarker.BULLET,
        )


@pytest.mark.parametrize("depth", [0, 1, 8])
def test_a_list_item_accepts_every_valid_depth(depth: int) -> None:
    """I10: the legal depths are accepted."""
    block = WordBlock(
        block_id=f"l{depth}",
        kind=WordBlockKind.LIST_ITEM,
        text="Item.",
        level=depth,
        list_marker=ListMarker.BULLET,
    )
    assert block.level == depth


# ============================================================================
# §3.1 I6 - a page break carries no text
# ============================================================================


def test_page_break_rejects_text() -> None:
    """I6: a PAGE_BREAK block with text is rejected."""
    with pytest.raises(ValueError, match="carries no text"):
        WordBlock(
            block_id="pb1",
            kind=WordBlockKind.PAGE_BREAK,
            text="page break",
        )


def test_page_break_is_accepted_without_text() -> None:
    """I6: a PAGE_BREAK block without text is accepted."""
    block = WordBlock(block_id="pb1", kind=WordBlockKind.PAGE_BREAK)
    assert block.kind is WordBlockKind.PAGE_BREAK


# ============================================================================
# §3.1 I8 - block text must agree with its runs
# ============================================================================


def test_block_text_must_match_its_runs() -> None:
    """I8: a block may not claim one text while its runs say another."""
    with pytest.raises(ValueError, match="must equal the normalised concatenation"):
        WordBlock(
            block_id="p1",
            kind=WordBlockKind.PARAGRAPH,
            text="Different text.",
            runs=(WordRun(text="Actual text."),),
        )


def test_block_text_matching_its_runs_is_accepted() -> None:
    """I8: a block whose text equals its normalised runs is accepted."""
    block = WordBlock(
        block_id="p1",
        kind=WordBlockKind.PARAGRAPH,
        text="A grounded claim with emphasis",
        runs=(
            WordRun(text="A grounded claim "),
            WordRun(text="with emphasis", bold=True),
        ),
    )
    assert block.text == "A grounded claim with emphasis"


def test_a_block_whose_runs_carry_the_next_line_control_is_accepted() -> None:
    """I8: the block the tier builds for a document carrying U+0085 is accepted.

    The runs carry the document's text verbatim, so they carry the
    next-line control; the block's text is the normalised concatenation of
    them, which is what both tiers agree the control normalises to. The
    block the tier sends is therefore a block the model accepts -- which it
    did not, while the tier still believed Java's idea of whitespace.
    """
    raw = "abc\u0085def"
    block = WordBlock(
        block_id="p1",
        kind=WordBlockKind.PARAGRAPH,
        text=normalize_whitespace(raw),
        runs=(WordRun(text=raw),),
    )
    assert block.runs[0].text == raw
    assert block.text == "abc def"


# ============================================================================
# §3.2 D1 - block ids are unique within a document
# ============================================================================


def test_duplicate_block_ids_are_rejected() -> None:
    """I7/D1: two blocks sharing a block_id are rejected."""
    with pytest.raises(ValueError, match="duplicate block_id"):
        WordDocument(
            blocks=(
                _paragraph(block_id="same"),
                _paragraph(block_id="same"),
            )
        )


# ============================================================================
# §3.2 D2 - footnote references must resolve
# ============================================================================


def test_run_referencing_an_unknown_footnote_is_rejected() -> None:
    """D2: a run pointing at a footnote that does not exist is rejected."""
    with pytest.raises(ValueError, match="unknown footnote"):
        WordDocument(
            blocks=(
                WordBlock(
                    block_id="p1",
                    kind=WordBlockKind.PARAGRAPH,
                    text="Claim",
                    runs=(WordRun(text="Claim", footnote_ref="f9"),),
                ),
            )
        )


def test_run_referencing_a_known_footnote_is_accepted() -> None:
    """D2: a run pointing at a declared footnote is accepted."""
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="p1",
                kind=WordBlockKind.PARAGRAPH,
                text="Claim",
                runs=(WordRun(text="Claim", footnote_ref="f1"),),
            ),
        ),
        footnotes=(WordFootnote(footnote_id="f1", text="the cited passage"),),
    )
    assert document.blocks[0].runs[0].footnote_ref == "f1"


# ============================================================================
# §4 T5-T9 - plain-text rendering rules
# ============================================================================


def test_plain_text_drops_empty_blocks() -> None:
    """T5: a block whose text is empty does not reach the plain text."""
    document = WordDocument(
        blocks=(
            _paragraph(block_id="p1", text="Kept."),
            _paragraph(block_id="p2", text="   "),
        )
    )
    assert document.plain_text() == "Kept."


def test_plain_text_joins_blocks_with_a_blank_line() -> None:
    """T6: surviving blocks are joined with a single blank line."""
    document = WordDocument(
        blocks=(
            _paragraph(block_id="p1", text="First."),
            _paragraph(block_id="p2", text="Second."),
        )
    )
    assert document.plain_text() == "First.\n\nSecond."


def test_plain_text_renders_table_rows_pipe_separated() -> None:
    """T7: a table renders one pipe-separated line per row."""
    document = WordDocument(blocks=(_table(),))
    assert document.plain_text() == "Evidence | Verdict\nchunk-abc | entailed"


def test_plain_text_omits_a_page_break() -> None:
    """T8: a PAGE_BREAK block contributes no characters."""
    document = WordDocument(
        blocks=(
            _paragraph(block_id="p1", text="Before."),
            WordBlock(block_id="pb1", kind=WordBlockKind.PAGE_BREAK),
            _paragraph(block_id="p2", text="After."),
        )
    )
    assert document.plain_text() == "Before.\n\nAfter."
    # A form feed would be filtered out of the plain text as whitespace, so
    # the equality above would not notice it. The clause says the page break
    # contributes *no characters*, and that is what is asserted here.
    assert "\f" not in document.plain_text()
    assert document.blocks[1].render_text() == ""


def test_plain_text_omits_list_markers() -> None:
    """T9: a list item renders as its text, without a bullet or number."""
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="l1",
                kind=WordBlockKind.LIST_ITEM,
                text="First bullet",
                list_marker=ListMarker.BULLET,
                level=0,
            ),
        )
    )
    assert document.plain_text() == "First bullet"
    assert "•" not in document.plain_text()


# ============================================================================
# §3.2 D3 - plain text is deterministic
# ============================================================================


def test_plain_text_is_deterministic() -> None:
    """D3: the same document always renders the same plain text."""
    first = WordDocument(
        blocks=(
            _paragraph(block_id="p1", text="First."),
            _table(block_id="t1"),
        )
    )
    second = WordDocument(
        blocks=(
            _paragraph(block_id="p1", text="First."),
            _table(block_id="t1"),
        )
    )
    assert first.plain_text() == second.plain_text()
    assert first.plain_text() == (
        "First.\n\nEvidence | Verdict\nchunk-abc | entailed"
    )


# ============================================================================
# Model surface
# ============================================================================


# ============================================================================
# §3.1 I9 / §3.2 D4 — merge cells
# ============================================================================


def test_a_continuation_cell_may_not_span_rows() -> None:
    """I9: a merge continuation cell spans a single row."""
    with pytest.raises(ValueError, match="spans a single row"):
        WordTableCell(text="", row_span=2, is_merge_continuation=True)


def test_a_continuation_cell_may_not_carry_text() -> None:
    """D4: a merge continuation cell carries no text of its own."""
    with pytest.raises(ValueError, match="carries no text"):
        WordTableCell(text="LEAKED", is_merge_continuation=True)


def test_a_continuation_cell_without_text_is_accepted() -> None:
    """I9/D4: the legal shape is accepted."""
    cell = WordTableCell(text="", row_span=1, is_merge_continuation=True)
    assert cell.is_merge_continuation is True


def test_a_merge_origin_may_span_rows_and_columns() -> None:
    """I9: the cell that starts a merge carries the span it occupies."""
    cell = WordTableCell(text="origin", row_span=3, col_span=2)
    assert cell.row_span == 3
    assert cell.col_span == 2
    assert cell.is_merge_continuation is False


def test_blocks_are_immutable() -> None:
    """The model is frozen: a block cannot be mutated after construction."""
    block = _paragraph()
    with pytest.raises(ValueError):
        block.text = "mutated"  # type: ignore[misc]


def test_unknown_fields_are_rejected() -> None:
    """The model forbids extra fields, so a typo cannot pass silently."""
    with pytest.raises(ValueError):
        WordBlock(
            block_id="p1",
            kind=WordBlockKind.PARAGRAPH,
            text="Body.",
            unexpected="field",  # type: ignore[call-arg]
        )
