"""Tests for the thirteen word-processing gaps the audit found.

Each test in this module exists because the audit found a construct the Word
subsystem lost, dropped or mis-mapped.  The gap id (G1..G13) is named in every
docstring, so a reader can trace a gap to the test that proves it and back.

Every test is written so that it **fails on the code that existed before the
gap was closed**: each one asserts on the construct that used to be missing,
not on the mere absence of an exception.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone

import pytest

from lmeditor.core.exceptions import WordParseError
from lmeditor.word.formats import (
    LEGACY_DOC_MAGIC,
    is_legacy_doc,
    is_zip_package,
)
from lmeditor.word.model import (
    WordComment,
    WordDocument,
    WordEndnote,
    WordRevisionKind,
    WordRun,
)
from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter
from tests.word_fixtures import (
    FIXTURE_COLOUR,
    FIXTURE_COMMENT_AUTHOR,
    FIXTURE_COMMENT_INITIALS,
    FIXTURE_COMMENT_TEXT,
    FIXTURE_CUSTOM_STYLE_NAME,
    FIXTURE_ENDNOTE_TEXT,
    FIXTURE_FONT,
    build_comments_package,
    build_compression_bomb_package,
    build_custom_style_package,
    build_endnotes_package,
    build_image_document_package,
    build_legacy_doc_bytes,
    build_media_package,
    build_nested_table_package,
    build_roman_numbering_package,
    build_run_formatting_package,
    build_section_package,
    build_too_many_members_package,
    build_word365_fixture,
)


def _reader() -> WordReader:
    """A fresh reader, so the omission register is not shared."""
    return WordReader()


def _round_trip(document: WordDocument) -> WordDocument:
    """Write a document and read it back."""
    return _reader().read_bytes(WordWriter().write(document))


# ============================================================================
# G1 — comments survive a read -> write -> read round trip
# ============================================================================


def test_a_comment_is_read_with_its_text_and_author() -> None:
    """G1: ``word/comments.xml`` is parsed into the document model."""
    document = _reader().read(build_comments_package())
    assert len(document.comments) == 1
    comment = document.comments[0]
    assert isinstance(comment, WordComment)
    assert comment.text == FIXTURE_COMMENT_TEXT
    assert comment.author == FIXTURE_COMMENT_AUTHOR
    assert comment.initials == FIXTURE_COMMENT_INITIALS


def test_a_comment_is_anchored_on_the_block_it_covers() -> None:
    """G1: the runs a comment covers carry its id."""
    document = _reader().read(build_comments_package())
    assert document.blocks
    anchored = [
        run
        for block in document.blocks
        for run in block.runs
        if run.comment_refs
    ]
    assert anchored, "no run carries the comment reference"
    assert anchored[0].comment_refs == (document.comments[0].comment_id,)


def test_a_comment_survives_a_round_trip() -> None:
    """G1: the text of a comment survives a read -> write -> read round trip."""
    document = _reader().read(build_comments_package())
    again = _round_trip(document)
    assert len(again.comments) == 1
    assert again.comments[0].text == FIXTURE_COMMENT_TEXT
    assert again.comments[0].author == FIXTURE_COMMENT_AUTHOR


def test_a_written_comment_is_a_real_comments_part() -> None:
    """G1: the writer emits ``word/comments.xml`` and its relationship."""
    document = _reader().read(build_comments_package())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        assert "word/comments.xml" in names
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
        assert "relationships/comments" in rels
        comments = archive.read("word/comments.xml").decode("utf-8")
    assert FIXTURE_COMMENT_TEXT in comments


def test_a_written_comment_is_anchored_in_the_document() -> None:
    """G1: the writer emits the in-document anchors of a comment."""
    document = _reader().read(build_comments_package())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    assert "commentRangeStart" in body
    assert "commentRangeEnd" in body
    assert "commentReference" in body


# ============================================================================
# G2 — tracked changes are represented, not flattened away
# ============================================================================


def test_a_deletion_is_kept_in_the_model() -> None:
    """G2: the text Word deleted is kept, not silently dropped."""
    document = _reader().read(build_word365_fixture())
    deleted = [
        run
        for block in document.blocks
        for run in block.runs
        if run.revision is not None and run.revision.kind is WordRevisionKind.DELETION
    ]
    assert deleted, "the deleted run vanished from the model"
    assert deleted[0].text == "Deleted by review"
    assert deleted[0].revision is not None
    assert deleted[0].revision.author == "Reviewer"


def test_a_deletion_is_not_part_of_the_final_text() -> None:
    """G2: deleted text is recoverable but is not the document's text."""
    document = _reader().read(build_word365_fixture())
    texts = [block.text for block in document.blocks]
    assert "Deleted by review" not in texts
    assert "Inserted by review" in texts


def test_an_insertion_is_marked_as_an_insertion() -> None:
    """G2: an insertion is kept in the text and marked as a revision."""
    document = _reader().read(build_word365_fixture())
    inserted = [
        run
        for block in document.blocks
        for run in block.runs
        if run.revision is not None and run.revision.kind is WordRevisionKind.INSERTION
    ]
    assert inserted
    assert inserted[0].text == "Inserted by review"


def test_a_deletion_survives_a_round_trip() -> None:
    """G2: a tracked deletion is written back out and read back in."""
    document = _reader().read(build_word365_fixture())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    assert "w:del " in body
    assert "delText" in body
    again = _reader().read_bytes(package)
    deleted = [
        run
        for block in again.blocks
        for run in block.runs
        if run.revision is not None and run.revision.kind is WordRevisionKind.DELETION
    ]
    assert deleted
    assert deleted[0].text == "Deleted by review"


# ============================================================================
# G3 — endnotes are read and written
# ============================================================================


def test_an_endnote_is_read() -> None:
    """G3: ``word/endnotes.xml`` is parsed into the document model."""
    document = _reader().read(build_endnotes_package())
    assert len(document.endnotes) == 1
    endnote = document.endnotes[0]
    assert isinstance(endnote, WordEndnote)
    assert endnote.text == FIXTURE_ENDNOTE_TEXT


def test_a_run_references_its_endnote() -> None:
    """G3: a run carries the id of the endnote it references."""
    document = _reader().read(build_endnotes_package())
    referenced = [
        run
        for block in document.blocks
        for run in block.runs
        if run.endnote_ref is not None
    ]
    assert referenced
    assert referenced[0].endnote_ref == document.endnotes[0].endnote_id


def test_an_endnote_survives_a_round_trip() -> None:
    """G3: an endnote survives a read -> write -> read round trip."""
    document = _reader().read(build_endnotes_package())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert "word/endnotes.xml" in archive.namelist()
    again = _reader().read_bytes(package)
    assert len(again.endnotes) == 1
    assert again.endnotes[0].text == FIXTURE_ENDNOTE_TEXT


# ============================================================================
# G4 — section properties are modelled and written
# ============================================================================


def test_a_section_carries_the_page_setup() -> None:
    """G4: page size, orientation and margins are read from ``w:sectPr``."""
    document = _reader().read(build_section_package())
    assert len(document.sections) == 1
    section = document.sections[0]
    assert section.page_width == 15840
    assert section.page_height == 12240
    assert section.orientation == "landscape"
    assert section.margin_top == 720
    assert section.margin_left == 720


def test_a_section_carries_its_header_references() -> None:
    """G4: the header references of a section are read."""
    document = _reader().read(build_section_package())
    refs = {ref.ref_type: ref for ref in document.sections[0].header_refs}
    assert set(refs) == {"default", "even", "first"}
    assert refs["default"].text == "Default page header"


def test_the_page_setup_is_written_out() -> None:
    """G4: the writer emits the page size, orientation and margins."""
    document = _reader().read(build_section_package())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    assert 'w:w="15840"' in body
    assert 'w:h="12240"' in body
    assert 'w:orient="landscape"' in body
    assert 'w:top="720"' in body


# ============================================================================
# G5 — run formatting beyond bold, italic, underline and superscript
# ============================================================================


def test_the_colour_of_a_run_is_read() -> None:
    """G5: the font colour of a run is read."""
    document = _reader().read(build_run_formatting_package())
    colours = [
        run.color
        for block in document.blocks
        for run in block.runs
        if run.color is not None
    ]
    assert colours == [FIXTURE_COLOUR]


def test_the_highlight_of_a_run_is_read() -> None:
    """G5: the highlight of a run is read."""
    document = _reader().read(build_run_formatting_package())
    highlights = [
        run.highlight
        for block in document.blocks
        for run in block.runs
        if run.highlight is not None
    ]
    assert highlights == ["yellow"]


def test_the_strike_of_a_run_is_read() -> None:
    """G5: the strike-through of a run is read."""
    document = _reader().read(build_run_formatting_package())
    struck = [
        run for block in document.blocks for run in block.runs if run.strike
    ]
    assert [run.text for run in struck] == ["Struck run"]


def test_the_font_and_size_of_a_run_are_read() -> None:
    """G5: the font family and size of a run are read."""
    document = _reader().read(build_run_formatting_package())
    fonts = [
        (run.font, run.size)
        for block in document.blocks
        for run in block.runs
        if run.font is not None
    ]
    assert fonts == [(FIXTURE_FONT, 28)]


def test_the_subscript_of_a_run_is_read() -> None:
    """G5: subscript is read as its own flag."""
    document = _reader().read(build_run_formatting_package())
    subscripts = [
        run for block in document.blocks for run in block.runs if run.subscript
    ]
    assert [run.text for run in subscripts] == ["Subscript run"]


def test_the_colour_and_the_highlight_survive_a_round_trip() -> None:
    """G5: colour, highlight and strike survive a read -> write -> read."""
    document = _reader().read(build_run_formatting_package())
    again = _round_trip(document)
    colours = [run.color for block in again.blocks for run in block.runs if run.color]
    highlights = [
        run.highlight for block in again.blocks for run in block.runs if run.highlight
    ]
    struck = [run for block in again.blocks for run in block.runs if run.strike]
    assert colours == [FIXTURE_COLOUR]
    assert highlights == ["yellow"]
    assert [run.text for run in struck] == ["Struck run"]


# ============================================================================
# G6 — a custom style is defined in the output, not dropped
# ============================================================================


def test_a_custom_style_is_carried_in_the_model() -> None:
    """G6: the style a paragraph uses is carried in the document model."""
    document = _reader().read(build_custom_style_package())
    names = [style.name for style in document.styles]
    assert FIXTURE_CUSTOM_STYLE_NAME in names


def test_a_custom_style_is_defined_in_the_written_package() -> None:
    """G6: the writer defines a style the template does not carry."""
    document = _reader().read(build_custom_style_package())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        styles = archive.read("word/styles.xml").decode("utf-8")
    assert FIXTURE_CUSTOM_STYLE_NAME in styles


def test_a_custom_style_survives_a_round_trip() -> None:
    """G6: the custom style is still the style of the block afterwards."""
    document = _reader().read(build_custom_style_package())
    again = _round_trip(document)
    assert again.blocks[0].style == FIXTURE_CUSTOM_STYLE_NAME


def test_a_style_that_cannot_be_represented_is_reported() -> None:
    """G6: a style the writer cannot represent is reported, not dropped."""
    document = WordDocument(
        blocks=(
            _block_with_style("A Style Nobody Defined"),
        ),
    )
    writer = WordWriter()
    writer.write(document)
    assert any("style" in omission.lower() for omission in writer.last_omissions)


def _block_with_style(name: str):  # type: ignore[no-untyped-def]
    """Build a paragraph block that claims to use a style."""
    from lmeditor.word.model import WordBlock, WordBlockKind

    return WordBlock(
        block_id="b0001",
        kind=WordBlockKind.PARAGRAPH,
        text="Body",
        style=name,
        runs=(WordRun(text="Body"),),
    )


# ============================================================================
# G7 — the numbering format of a list level is the source document's
# ============================================================================


def test_the_number_format_of_a_level_is_read() -> None:
    """G7: the ``w:numFmt`` of a list level is read."""
    document = _reader().read(build_roman_numbering_package())
    formats = [block.number_format for block in document.blocks]
    assert formats == ["lowerRoman", "lowerLetter"]


def test_the_level_text_of_a_list_is_read() -> None:
    """G7: the ``w:lvlText`` of a list level is read."""
    document = _reader().read(build_roman_numbering_package())
    texts = [block.number_text for block in document.blocks]
    assert texts == ["%1.", "%2)"]


def test_the_number_format_survives_a_round_trip() -> None:
    """G7: a roman list stays roman after the round trip."""
    document = _reader().read(build_roman_numbering_package())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        numbering = archive.read("word/numbering.xml").decode("utf-8")
    assert 'w:numFmt w:val="lowerRoman"' in numbering
    assert 'w:numFmt w:val="lowerLetter"' in numbering
    assert 'w:lvlText w:val="%2)"' in numbering
    again = _reader().read_bytes(package)
    assert [block.number_format for block in again.blocks] == [
        "lowerRoman",
        "lowerLetter",
    ]


# ============================================================================
# G8 — headers are matched by reference type, not by part order
# ============================================================================


def test_a_header_is_matched_by_its_reference_type() -> None:
    """G8: the default header is the one the section calls ``default``."""
    document = _reader().read(build_section_package())
    refs = {ref.ref_type: ref.text for ref in document.sections[0].header_refs}
    assert refs["default"] == "Default page header"
    assert refs["even"] == "Even page header"
    assert refs["first"] == "First page header"


def test_the_headers_are_not_matched_by_part_order() -> None:
    """G8: the header texts are not simply the part order of the package."""
    document = _reader().read(build_section_package())
    assert document.headers != (
        "Even page header",
        "Default page header",
        "First page header",
    )


def test_the_headers_keep_their_roles_after_a_round_trip() -> None:
    """G8: the default header stays the default header after the round trip."""
    document = _reader().read(build_section_package())
    again = _round_trip(document)
    refs = {ref.ref_type: ref.text for ref in again.sections[0].header_refs}
    assert refs["default"] == "Default page header"
    assert refs["even"] == "Even page header"
    assert refs["first"] == "First page header"


# ============================================================================
# G9 — a decompression bomb is refused
# ============================================================================


def test_a_compressed_bomb_is_refused() -> None:
    """G9: a package that decompresses far beyond its size is refused."""
    package = build_compression_bomb_package()

    with pytest.raises(WordParseError) as failure:
        _reader().read(package)

    # The refusal is the reader's own typed error: it names what was refused
    # and why, so a caller can tell a refused package from a broken one.
    refusal = failure.value
    assert refusal.origin
    reason = refusal.reason
    assert reason

    # The reason states the bound that was exceeded, in numbers, so that an
    # operator is told how much was allowed. Which bound fired -- the expanded
    # size or the compression ratio -- is the reader's business; what this
    # test pins is that the bomb is refused *and* the limit is named, not
    # that one particular English word appears in the sentence.
    limits = [int(chunk) for chunk in re.findall(r"\d+", reason)]
    assert limits, reason
    assert all(limit > 0 for limit in limits), reason

    # The bound the reader quotes is one this bomb really is over: the package
    # expands to far more than any limit the refusal names.
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        expanded = sum(info.file_size for info in archive.infolist())
    assert min(limits) < expanded, reason


def test_a_package_with_too_many_members_is_refused() -> None:
    """G9: a package holding more members than allowed is refused."""
    with pytest.raises(WordParseError) as failure:
        _reader().read(build_too_many_members_package())
    assert "members" in str(failure.value).lower()


def test_a_refused_package_names_the_reason() -> None:
    """G9: the refusal says what was wrong with the package."""
    with pytest.raises(WordParseError) as failure:
        _reader().read(build_compression_bomb_package())
    assert str(failure.value)


# ============================================================================
# G10 — a legacy .doc is recognised and refused with a usable reason
# ============================================================================


def test_a_legacy_document_is_recognised() -> None:
    """G10: the OLE2 magic number identifies a legacy ``.doc``."""
    assert is_legacy_doc(build_legacy_doc_bytes()) is True
    assert is_legacy_doc(build_word365_fixture()) is False
    assert is_zip_package(build_word365_fixture()) is True


def test_a_legacy_document_is_refused_with_an_actionable_reason() -> None:
    """G10: the reader refuses a legacy ``.doc`` and says what to do."""
    with pytest.raises(WordParseError) as failure:
        _reader().read(build_legacy_doc_bytes())
    message = str(failure.value)
    assert "legacy .doc (OLE2) is not supported" in message
    assert "convert to .docx" in message


def test_the_legacy_magic_number_is_the_one_word_writes() -> None:
    """G10: the magic number is the OLE2 one, byte for byte."""
    assert LEGACY_DOC_MAGIC == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# ============================================================================
# G11 — a table keeps its content, its nesting and its column widths
# ============================================================================


def test_a_cell_keeps_its_runs() -> None:
    """G11: the inline content of a cell is kept, not flattened to a string."""
    document = _reader().read(build_nested_table_package())
    table = document.blocks[0].table
    assert table is not None
    cell = table.rows[0].cells[0]
    assert [run.text for run in cell.runs] == ["Outer cell"]


def test_a_nested_table_stays_a_table() -> None:
    """G11: a table inside a cell is a table, not a string."""
    document = _reader().read(build_nested_table_package())
    table = document.blocks[0].table
    assert table is not None
    nested = table.rows[0].cells[0].tables
    assert len(nested) == 1
    assert nested[0].rows[0].cells[0].text == "Inner cell"


def test_the_column_widths_of_a_table_are_read() -> None:
    """G11: the declared width of every grid column is read."""
    document = _reader().read(build_nested_table_package())
    table = document.blocks[0].table
    assert table is not None
    assert table.column_widths == (1500, 4500)


def test_the_column_widths_survive_a_round_trip() -> None:
    """G11: the declared column widths are written back out."""
    document = _reader().read(build_nested_table_package())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    assert 'w:gridCol w:w="1500"' in body
    assert 'w:gridCol w:w="4500"' in body


def test_a_nested_table_survives_a_round_trip() -> None:
    """G11: the nested table is still a table after the round trip."""
    document = _reader().read(build_nested_table_package())
    again = _round_trip(document)
    table = again.blocks[0].table
    assert table is not None
    assert len(table.rows[0].cells[0].tables) == 1


# ============================================================================
# G12 — an embedded image is kept, and an unreachable one is named
# ============================================================================


def test_an_embedded_image_is_kept_in_the_model() -> None:
    """G12: the media part of an image travels in the document model."""
    document = _reader().read(build_media_package())
    assert len(document.media) == 1
    media = document.media[0]
    assert media.part_name == "word/media/image1.png"
    assert media.content_type == "image/png"
    assert media.data.startswith(b"\x89PNG")


def test_an_embedded_image_is_referenced_by_a_run() -> None:
    """G12: the run that embeds the image carries the media id."""
    document = _reader().read(build_media_package())
    referenced = [
        run
        for block in document.blocks
        for run in block.runs
        if run.image_ref is not None
    ]
    assert referenced
    assert referenced[0].image_ref == document.media[0].media_id


def test_an_embedded_image_is_written_back_out() -> None:
    """G12: the media part and its picture are written back into the package."""
    document = _reader().read(build_media_package())
    package = WordWriter().write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        assert "word/media/image1.png" in names
        body = archive.read("word/document.xml").decode("utf-8")
    assert "w:drawing" in body
    assert "a:blip" in body


def test_an_image_the_reader_cannot_reach_is_named() -> None:
    """G12: an image that cannot be kept is reported with its rel and part."""
    reader = _reader()
    reader.read(build_image_document_package())
    omissions = [entry for entry in reader.last_omissions if "image" in entry.lower()]
    assert omissions
    assert "rId9" in omissions[0]
    assert "part" in omissions[0]


# ============================================================================
# G13 — the writer reports what it cannot represent
# ============================================================================


def test_the_writer_has_an_omission_register() -> None:
    """G13: the writer exposes the same kind of register the reader uses."""
    writer = WordWriter()
    assert writer.last_omissions == []
    writer.write(WordDocument())
    assert writer.last_omissions == []


def test_the_writer_reports_a_style_it_cannot_represent() -> None:
    """G13: a style that cannot be represented is reported, not dropped."""
    document = WordDocument(blocks=(_block_with_style("Unrepresentable"),))
    writer = WordWriter()
    writer.write(document)
    assert writer.last_omissions
    assert any("Unrepresentable" in entry for entry in writer.last_omissions)


def test_the_writer_reports_the_sections_it_cannot_write() -> None:
    """G13: a section beyond the first is reported rather than dropped."""
    document = _reader().read(build_section_package())
    sections = document.sections
    extra = sections[0].model_copy(update={"page_width": 1000})
    document = document.model_copy(update={"sections": sections + (extra,)})
    writer = WordWriter()
    writer.write(document)
    assert any("section" in entry.lower() for entry in writer.last_omissions)


def test_the_writer_reports_an_unembedded_image() -> None:
    """G13: a run that points at a missing image is reported."""
    from lmeditor.word.model import WordBlock, WordBlockKind

    block = WordBlock(
        block_id="b0001",
        kind=WordBlockKind.PARAGRAPH,
        text="",
        runs=(WordRun(text="", image_ref="word/media/missing.png"),),
    )
    writer = WordWriter()
    writer.write(WordDocument(blocks=(block,)))
    assert any("image" in entry.lower() for entry in writer.last_omissions)


def test_the_writer_registers_are_reset_between_calls() -> None:
    """G13: the register belongs to one render, not to the writer."""
    writer = WordWriter()
    writer.write(WordDocument(blocks=(_block_with_style("Unrepresentable"),)))
    assert writer.last_omissions
    writer.write(WordDocument())
    assert writer.last_omissions == []


# ============================================================================
# The model itself
# ============================================================================


def test_a_run_reports_whether_it_was_deleted() -> None:
    """The model tells a deleted run apart from a live one."""
    from lmeditor.word.model import WordRevision

    revision = WordRevision(
        kind=WordRevisionKind.DELETION,
        author="Reviewer",
        date=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        revision_id="7",
    )
    assert WordRun(text="gone", revision=revision).is_deleted is True
    assert WordRun(text="here").is_deleted is False
