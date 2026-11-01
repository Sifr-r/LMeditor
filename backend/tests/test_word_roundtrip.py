"""Round-trip tests for the word-processing element.

A reader and a writer that are individually correct can still disagree about
the format, and the disagreement only shows up when the two are composed. These
tests compose them:

* **model -> write -> read**: the text that goes in comes back out unchanged.
* **Word 365 fixture -> read -> write -> read**: a document Word produced
  survives being taken apart and put back together.
* **draft -> bridge -> package -> read**: the path the product actually takes,
  from the manuscript the agent drafted to the text a reader gets back.

The second test is the one that matters for the product: it is the difference
between "we wrote a .docx" and "we can read a .docx".
"""

from __future__ import annotations

import pytest

from lmeditor.core.schemas import (
    BibliographicRecord,
    CitationLineage,
    DocumentDraft,
    SectionDraft,
)
from lmeditor.core.types import SectionStatus
from lmeditor.word.manuscript import build_manuscript_document
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
from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter
from tests.word_fixtures import (
    FIXTURE_BULLET,
    FIXTURE_FINAL_PARAGRAPH,
    FIXTURE_HEADING_1,
    FIXTURE_HEADING_2,
    FIXTURE_LINK_TEXT,
    FIXTURE_NUMBERED_1,
    FIXTURE_NUMBERED_2,
    FIXTURE_PARAGRAPH,
    FIXTURE_TABLE_HEADER,
    FIXTURE_TABLE_ROW,
    FIXTURE_TITLE,
    build_word365_fixture,
)


def _document() -> WordDocument:
    """A document using every construct the writer supports."""
    return WordDocument(
        blocks=(
            WordBlock(
                block_id="b1",
                kind=WordBlockKind.TITLE,
                text="A Grounded Manuscript",
                runs=(WordRun(text="A Grounded Manuscript"),),
            ),
            WordBlock(
                block_id="b2",
                kind=WordBlockKind.HEADING,
                text="Grounding Section",
                level=1,
                runs=(WordRun(text="Grounding Section"),),
            ),
            WordBlock(
                block_id="b3",
                kind=WordBlockKind.HEADING,
                text="Nested Subsection",
                level=2,
                runs=(WordRun(text="Nested Subsection"),),
            ),
            WordBlock(
                block_id="b4",
                kind=WordBlockKind.PARAGRAPH,
                text="A grounded claim with emphasis",
                runs=(
                    WordRun(text="A grounded claim "),
                    WordRun(text="with emphasis", bold=True),
                ),
            ),
            WordBlock(
                block_id="b5",
                kind=WordBlockKind.LIST_ITEM,
                text=FIXTURE_BULLET,
                list_marker=ListMarker.BULLET,
                level=0,
                runs=(WordRun(text=FIXTURE_BULLET),),
            ),
            WordBlock(
                block_id="b6",
                kind=WordBlockKind.LIST_ITEM,
                text=FIXTURE_NUMBERED_1,
                list_marker=ListMarker.NUMBER,
                list_group="g1",
                level=0,
                runs=(WordRun(text=FIXTURE_NUMBERED_1),),
            ),
            WordBlock(
                block_id="b7",
                kind=WordBlockKind.LIST_ITEM,
                text=FIXTURE_NUMBERED_2,
                list_marker=ListMarker.NUMBER,
                list_group="g1",
                level=0,
                runs=(WordRun(text=FIXTURE_NUMBERED_2),),
            ),
            WordBlock(
                block_id="b8",
                kind=WordBlockKind.TABLE,
                table=WordTable(
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
                ),
            ),
            WordBlock(
                block_id="b9",
                kind=WordBlockKind.PARAGRAPH,
                text="Claim with a footnote1",
                runs=(
                    WordRun(text="Claim with a footnote"),
                    WordRun(text="1", superscript=True, footnote_ref="f1"),
                ),
            ),
        ),
        footnotes=(WordFootnote(footnote_id="f1", text="the cited passage"),),
    )


def test_model_survives_a_write_read_round_trip() -> None:
    """A document written and read back yields the same plain text."""
    original = _document()
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    assert recovered.plain_text() == original.plain_text()


def test_heading_levels_survive_a_round_trip() -> None:
    """Heading levels are structure, and structure must survive."""
    original = _document()
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    levels = {
        block.text: block.level
        for block in recovered.blocks
        if block.kind is WordBlockKind.HEADING
    }
    assert levels[FIXTURE_HEADING_1] == 1
    assert levels[FIXTURE_HEADING_2] == 2


def test_list_markers_survive_a_round_trip() -> None:
    """A bullet stays a bullet and a number stays a number."""
    original = _document()
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    markers = {
        block.text: block.list_marker
        for block in recovered.blocks
        if block.kind is WordBlockKind.LIST_ITEM
    }
    assert markers[FIXTURE_BULLET] is ListMarker.BULLET
    assert markers[FIXTURE_NUMBERED_1] is ListMarker.NUMBER
    assert markers[FIXTURE_NUMBERED_2] is ListMarker.NUMBER


def test_table_content_survives_a_round_trip() -> None:
    """A table survives being written and read back."""
    original = _document()
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    assert FIXTURE_TABLE_HEADER in recovered.plain_text()
    assert FIXTURE_TABLE_ROW in recovered.plain_text()


def test_footnote_survives_a_round_trip() -> None:
    """The cited passage is still attached to the claim after a round trip."""
    original = _document()
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)
    assert "the cited passage" in {footnote.text for footnote in recovered.footnotes}


def test_a_word365_document_survives_read_write_read() -> None:
    """A document Word produced survives being taken apart and rebuilt."""
    original = WordReader().read(build_word365_fixture())
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)

    text = recovered.plain_text()
    assert FIXTURE_TITLE in text
    assert FIXTURE_HEADING_1 in text
    assert FIXTURE_HEADING_2 in text
    assert FIXTURE_PARAGRAPH in text
    assert FIXTURE_BULLET in text
    assert FIXTURE_NUMBERED_1 in text
    assert FIXTURE_NUMBERED_2 in text
    assert FIXTURE_LINK_TEXT in text
    assert FIXTURE_TABLE_HEADER in text
    assert FIXTURE_TABLE_ROW in text
    assert FIXTURE_FINAL_PARAGRAPH in text


def test_a_word365_document_keeps_its_structure_through_the_round_trip() -> None:
    """Headings stay headings and list items stay list items."""
    original = WordReader().read(build_word365_fixture())
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)

    headings = {
        block.text: block.level
        for block in recovered.blocks
        if block.kind is WordBlockKind.HEADING
    }
    assert headings[FIXTURE_HEADING_1] == 1
    assert headings[FIXTURE_HEADING_2] == 2

    list_items = [
        block for block in recovered.blocks if block.kind is WordBlockKind.LIST_ITEM
    ]
    assert len(list_items) >= 3


def test_reading_the_written_document_is_deterministic() -> None:
    """The round trip is stable: the same bytes always read the same way."""
    original = WordReader().read(build_word365_fixture())
    payload = WordWriter().write(original)
    assert WordReader().read(payload) == WordReader().read(payload)


# ============================================================================
# The whole product path: draft -> bridge -> package -> text
# ============================================================================


def test_a_draft_survives_the_whole_word_path() -> None:
    """The path the product actually takes, end to end.

    Every other round-trip test starts from the shared model or from a raw
    Word fixture.  This one starts where the product starts — a
    ``DocumentDraft`` carrying its grounding evidence — and asserts that the
    prose, the cited passage and the reference list all survive a real
    package and come back out of it.
    """
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    record = BibliographicRecord(
        title="A Grounded Paper",
        authors=["Ada Lovelace"],
        year=1843,
    )
    section = SectionDraft(
        section_id="s1",
        title="Grounding Section",
        content="A grounded claim [#abc123].",
        order=0,
        status=SectionStatus.VERIFIED,
        is_verified=True,
        citations=[lineage],
    )
    draft = DocumentDraft(
        document_id="d1",
        title="A Grounded Manuscript",
        sections=[section],
    )

    payload = WordWriter().write(build_manuscript_document(draft, [record]))
    recovered = WordReader().read(payload)

    text = recovered.plain_text()
    assert "A Grounded Manuscript" in text
    assert "Grounding Section" in text
    assert "A grounded claim" in text
    assert "[#abc123]" not in text, "the marker must be consumed, not printed"
    assert "A Grounded Paper" in text, "the reference must reach the document"

    footnote_texts = {footnote.text for footnote in recovered.footnotes}
    assert any(
        "the cited passage" in text and "abc123" in text for text in footnote_texts
    ), "the footnote must carry the cited passage and the chunk it came from"


# ============================================================================
# Adjacent tables (W11)
# ============================================================================


def _two_table_document() -> WordDocument:
    """A document whose two tables are adjacent, with no block between them."""
    return WordDocument(
        blocks=(
            WordBlock(
                block_id="t1",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="First cell"),
                                WordTableCell(text="Second cell"),
                            )
                        ),
                    )
                ),
            ),
            WordBlock(
                block_id="t2",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="Third cell"),
                                WordTableCell(text="Fourth cell"),
                            )
                        ),
                    )
                ),
            ),
        )
    )


def test_two_adjacent_tables_survive_the_round_trip_as_two_tables() -> None:
    """W11: adjacent tables stay two tables, and the text is unchanged.

    The writer separates them with an empty paragraph, because Word reads two
    adjacent ``w:tbl`` elements as one table.  The reader drops that empty
    paragraph again — it carries no content — so the model that comes back is
    the model that went in, and the text is untouched.
    """
    original = _two_table_document()
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)

    tables = [
        block.table
        for block in recovered.blocks
        if block.kind is WordBlockKind.TABLE and block.table is not None
    ]
    assert len(tables) == 2, "the two tables must not be merged into one"
    assert tables[0] is not None and tables[1] is not None
    assert tables[0].rows[0].cells[0].text == "First cell"
    assert tables[1].rows[0].cells[0].text == "Third cell"

    assert recovered.plain_text() == original.plain_text()


def test_the_separator_paragraph_does_not_add_a_blank_line_to_the_text() -> None:
    """W11: the separator is structural, so the extracted text is unchanged.

    An empty paragraph renders as nothing (T5), so the blank line Word shows
    between the two tables never reaches the chunker.
    """
    original = _two_table_document()
    payload = WordWriter().write(original)
    recovered = WordReader().read(payload)

    assert "\n\n\n" not in recovered.plain_text()
    assert recovered.plain_text().count("\n\n") == 1
