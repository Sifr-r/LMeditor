"""Tests for the two constructs the Word reader used to drop: equations and symbols.

An equation (``m:oMath``) and a symbol (``w:sym``) were both recorded in the
reader omission register and thrown away.  For a research manuscript that is the
worst kind of loss: the formula's text was not in the document at all, so it
could not be searched, quoted, verified or cited, and the mathematical
typography -- ``→``, ``±``, ``≤`` -- simply vanished.

Each test here exists because the construct used to be missing.  They assert on
the construct that was lost -- the equation's flattened text, its OMML markup,
the character a symbol stands for -- and not on the mere absence of an
exception.
"""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from lmeditor.word.model import WordBlock, WordBlockKind, WordDocument, WordRun
from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter
from tests.word_fixtures import (
    EXPECTED_PLAIN_TEXT,
    _package,
    build_word365_fixture,
)

#: The OMML namespace an equation lives in.
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

#: The namespace prefix Word binds for that namespace.
MATH_PREFIX = "m"

#: An inline equation: ``E=mc2``, written the way Word writes it.
INLINE_EQUATION = (
    f'<m:oMath xmlns:m="{MATH_NS}">'
    "<m:r><m:t>E</m:t></m:r>"
    "<m:r><m:t>=</m:t></m:r>"
    "<m:r><m:t>mc2</m:t></m:r>"
    "</m:oMath>"
)

#: A display equation: a superscript wrapped in ``m:oMathPara``.
DISPLAY_EQUATION = (
    f'<m:oMathPara xmlns:m="{MATH_NS}">'
    "<m:oMath>"
    "<m:sSup>"
    "<m:e><m:r><m:t>E</m:t></m:r></m:e>"
    "<m:sup><m:r><m:t>2</m:t></m:r></m:sup>"
    "</m:sSup>"
    "<m:r><m:t>=</m:t></m:r>"
    "<m:r><m:t>mc</m:t></m:r>"
    "<m:r><m:t>2</m:t></m:r>"
    "</m:oMath>"
    "</m:oMathPara>"
)

#: The text the inline equation reads as once its ``m:t`` elements are joined.
INLINE_EQUATION_TEXT = "E=mc2"

#: The text the display equation reads as: the ``m:t`` texts in document order.
DISPLAY_EQUATION_TEXT = "E2=mc2"


def _reader() -> WordReader:
    """A fresh reader, so the omission register is not shared."""
    return WordReader()


def _document_xml(package: bytes) -> str:
    """Return the ``word/document.xml`` of a package."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _equation_elements(package: bytes) -> list[etree._Element]:
    """Return every OMML element a package's body carries."""
    root = etree.fromstring(_document_xml(package).encode("utf-8"))
    return root.findall(f".//{{{MATH_NS}}}oMath")


# ============================================================================
# Equations are read, not dropped
# ============================================================================


def test_equation_is_read_as_a_run_with_its_flattened_text() -> None:
    """An ``m:oMath`` becomes a run whose text is the formula it reads as."""
    body = (
        f'<w:p xmlns:m="{MATH_NS}">'
        "<w:r><w:t>Before </w:t></w:r>"
        f"{INLINE_EQUATION}"
        "<w:r><w:t> after</w:t></w:r>"
        "</w:p>"
    )
    document = _reader().read_bytes(_package(body))

    assert len(document.blocks) == 1
    runs = document.blocks[0].runs
    assert len(runs) == 3
    equation_run = runs[1]
    assert equation_run.text == INLINE_EQUATION_TEXT
    assert equation_run.equation_xml
    assert "oMath" in equation_run.equation_xml


def test_equation_text_is_part_of_the_block_text() -> None:
    """The formula is searchable: it is part of the text the block carries."""
    body = (
        f'<w:p xmlns:m="{MATH_NS}">'
        "<w:r><w:t>Before </w:t></w:r>"
        f"{INLINE_EQUATION}"
        "<w:r><w:t> after</w:t></w:r>"
        "</w:p>"
    )
    document = _reader().read_bytes(_package(body))

    assert document.blocks[0].text == f"Before {INLINE_EQUATION_TEXT} after"
    assert INLINE_EQUATION_TEXT in document.plain_text()


def test_display_equation_is_read_as_well() -> None:
    """A display equation (``m:oMathPara``) is kept, not dropped either."""
    body = f'<w:p xmlns:m="{MATH_NS}">{DISPLAY_EQUATION}</w:p>'
    document = _reader().read_bytes(_package(body))

    runs = document.blocks[0].runs
    assert len(runs) == 1
    assert runs[0].text == DISPLAY_EQUATION_TEXT
    assert runs[0].equation_xml
    assert "oMathPara" in runs[0].equation_xml


def test_equation_is_not_reported_as_an_omission() -> None:
    """An equation is kept, so the register -- which names what was lost -- is empty."""
    body = (
        f'<w:p xmlns:m="{MATH_NS}">'
        "<w:r><w:t>Before </w:t></w:r>"
        f"{INLINE_EQUATION}"
        "<w:r><w:t> after</w:t></w:r>"
        "</w:p>"
    )
    reader = _reader()
    reader.read_bytes(_package(body))

    assert reader.last_omissions == []


# ============================================================================
# Equations survive the round trip
# ============================================================================


def test_equation_survives_a_write_and_read_round_trip() -> None:
    """Read -> write -> read keeps the equation, its text and its OMML."""
    body = (
        f'<w:p xmlns:m="{MATH_NS}">'
        "<w:r><w:t>Before </w:t></w:r>"
        f"{INLINE_EQUATION}"
        "<w:r><w:t> after</w:t></w:r>"
        "</w:p>"
    )
    document = _reader().read_bytes(_package(body))

    written = WordWriter().write(document)
    elements = _equation_elements(written)
    assert len(elements) == 1
    assert elements[0].tag == f"{{{MATH_NS}}}oMath"

    reread = _reader().read_bytes(written)
    equation_runs = [
        run for block in reread.blocks for run in block.runs if run.equation_xml
    ]
    assert len(equation_runs) == 1
    assert equation_runs[0].text == INLINE_EQUATION_TEXT
    assert reread.blocks[0].text == f"Before {INLINE_EQUATION_TEXT} after"


def test_written_equation_is_written_as_omml_and_not_as_text() -> None:
    """The writer emits the real equation, so Word renders the formula."""
    body = f'<w:p xmlns:m="{MATH_NS}">{INLINE_EQUATION}</w:p>'
    document = _reader().read_bytes(_package(body))

    written = WordWriter().write(document)

    assert "oMath" in _document_xml(written)
    assert INLINE_EQUATION_TEXT not in _document_xml(written)


# ============================================================================
# A broken equation falls back to its text instead of failing the write
# ============================================================================


def test_unparsable_equation_falls_back_to_the_flattened_text() -> None:
    """Markup the writer cannot parse costs the equation, not the document."""
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="b0001",
                kind=WordBlockKind.PARAGRAPH,
                text="E=mc2",
                runs=(
                    WordRun(
                        text="E=mc2",
                        equation_xml="<m:oMath><m:r><m:t>E=mc2</m:t></m:r>",
                    ),
                ),
            ),
        )
    )
    writer = WordWriter()
    written = writer.write(document)

    assert any("equation" in omission for omission in writer.last_omissions)
    reread = _reader().read_bytes(written)
    assert reread.blocks[0].text == "E=mc2"
    assert reread.blocks[0].runs[0].equation_xml is None


def test_equation_markup_without_a_namespace_is_still_written() -> None:
    """The writer binds the math namespace itself when the markup does not."""
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="b0001",
                kind=WordBlockKind.PARAGRAPH,
                text="E=mc2",
                runs=(
                    WordRun(
                        text="E=mc2",
                        equation_xml="<m:oMath><m:r><m:t>E=mc2</m:t></m:r></m:oMath>",
                    ),
                ),
            ),
        )
    )
    written = WordWriter().write(document)

    assert len(_equation_elements(written)) == 1


# ============================================================================
# Symbols become the character they stand for
# ============================================================================


def test_symbol_is_read_as_the_character_it_stands_for() -> None:
    """A ``w:sym`` becomes text, so the mathematical typography survives."""
    body = (
        "<w:p>"
        "<w:r><w:t>before </w:t></w:r>"
        '<w:r><w:sym w:font="Symbol" w:char="F0B7"/></w:r>'
        "<w:r><w:t> after</w:t></w:r>"
        "</w:p>"
    )
    document = _reader().read_bytes(_package(body))

    assert document.blocks[0].runs[1].text == "•"
    assert document.blocks[0].text == "before • after"


def test_symbol_survives_a_write_and_read_round_trip() -> None:
    """The symbol is written back as the character it was read as."""
    body = (
        "<w:p>"
        "<w:r><w:t>before </w:t></w:r>"
        '<w:r><w:sym w:font="Symbol" w:char="F0B7"/></w:r>'
        "<w:r><w:t> after</w:t></w:r>"
        "</w:p>"
    )
    document = _reader().read_bytes(_package(body))

    written = WordWriter().write(document)
    reread = _reader().read_bytes(written)

    assert reread.blocks[0].text == "before • after"
    assert reread.blocks[0].runs[1].text == "•"


def test_symbol_is_not_written_twice() -> None:
    """A mapped symbol becomes ordinary text, written exactly once."""
    body = (
        "<w:p>"
        '<w:r><w:sym w:font="Symbol" w:char="F0B7"/></w:r>'
        "</w:p>"
    )
    document = _reader().read_bytes(_package(body))

    written = WordWriter().write(document)

    assert _document_xml(written).count("•") == 1


def test_symbol_with_an_unknown_code_is_reported_and_not_guessed() -> None:
    """A code the table does not know is named in the register, not invented."""
    body = (
        "<w:p>"
        "<w:r><w:t>before </w:t></w:r>"
        '<w:r><w:sym w:font="Wingdings" w:char="F0A7"/></w:r>'
        "<w:r><w:t> after</w:t></w:r>"
        "</w:p>"
    )
    reader = _reader()
    document = reader.read_bytes(_package(body))

    assert reader.last_omissions
    omission = reader.last_omissions[0]
    assert "Wingdings" in omission
    assert "F0A7" in omission
    assert document.blocks[0].text == "before after"


def test_symbol_of_an_unknown_font_is_reported() -> None:
    """A font whose character set is unknown is reported, not guessed at."""
    body = (
        "<w:p>"
        '<w:r><w:sym w:font="Some Fancy Font" w:char="F030"/></w:r>'
        "</w:p>"
    )
    reader = _reader()
    reader.read_bytes(_package(body))

    assert reader.last_omissions
    assert "Some Fancy Font" in reader.last_omissions[0]


# ============================================================================
# An equation is kept wherever it is found
# ============================================================================


def test_equation_nested_inside_a_run_is_not_lost() -> None:
    """A producer may nest an equation in a run; it is still kept."""
    body = (
        f'<w:p xmlns:m="{MATH_NS}">'
        "<w:r><w:t>before</w:t>"
        f"{INLINE_EQUATION}"
        "</w:r>"
        "</w:p>"
    )
    reader = _reader()
    document = reader.read_bytes(_package(body))

    assert reader.last_omissions == []
    assert document.blocks[0].text == f"before{INLINE_EQUATION_TEXT}"
    assert document.blocks[0].runs[1].equation_xml


def test_equation_in_a_table_cell_survives_the_round_trip() -> None:
    """An equation in a table cell keeps its markup when the cell is written."""
    body = (
        "<w:tbl>"
        "<w:tblPr/>"
        '<w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid>'
        "<w:tr><w:tc><w:tcPr/>"
        f"<w:p>{INLINE_EQUATION}</w:p>"
        "</w:tc></w:tr>"
        "</w:tbl>"
    )
    document = _reader().read_bytes(_package(body))

    cell = document.blocks[0].table.rows[0].cells[0]
    assert cell.runs[0].text == INLINE_EQUATION_TEXT

    written = WordWriter().write(document)
    reread = _reader().read_bytes(written)
    reread_cell = reread.blocks[0].table.rows[0].cells[0]
    assert reread_cell.runs[0].text == INLINE_EQUATION_TEXT
    assert reread_cell.runs[0].equation_xml


def test_equation_in_a_footnote_survives_the_round_trip() -> None:
    """An equation in a footnote keeps its markup when the note is written."""
    footnotes = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        f'xmlns:m="{MATH_NS}">'
        '<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="1">'
        "<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>"
        '<w:footnote w:id="2"><w:p>'
        "<w:r><w:t>see </w:t></w:r>"
        f"{INLINE_EQUATION}"
        "</w:p></w:footnote>"
        "</w:footnotes>"
    )
    body = (
        "<w:p><w:r><w:t>text</w:t></w:r>"
        '<w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
    )
    document = _reader().read_bytes(_package(body, footnotes=footnotes))

    footnote = next(
        note for note in document.footnotes if note.footnote_id == "2"
    )
    assert footnote.runs[1].text == INLINE_EQUATION_TEXT

    written = WordWriter().write(document)
    reread = _reader().read_bytes(written)
    reread_note = next(
        note for note in reread.footnotes if note.footnote_id == "2"
    )
    assert reread_note.runs[1].text == INLINE_EQUATION_TEXT
    assert reread_note.runs[1].equation_xml


# ============================================================================
# A document without equations or symbols is untouched
# ============================================================================

def test_document_without_equations_or_symbols_is_unchanged() -> None:
    """The ordinary document reads and writes exactly as it did before."""
    reader = _reader()
    document = reader.read_bytes(build_word365_fixture())

    assert document.plain_text() == EXPECTED_PLAIN_TEXT
    assert not [
        omission
        for omission in reader.last_omissions
        if "equation" in omission or "symbol" in omission
    ]

    written = WordWriter().write(document)
    reread = _reader().read_bytes(written)
    assert reread.plain_text() == EXPECTED_PLAIN_TEXT


def test_run_without_an_equation_renders_as_it_did() -> None:
    """A run that carries no equation is written as an ordinary text run."""
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="b0001",
                kind=WordBlockKind.PARAGRAPH,
                text="plain text",
                runs=(WordRun(text="plain text"),),
            ),
        )
    )
    written = WordWriter().write(document)

    assert "<w:t>plain text</w:t>" in _document_xml(written)
    assert "oMath" not in _document_xml(written)
