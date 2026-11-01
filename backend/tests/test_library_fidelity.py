"""The library keeps what a Word document was made of.

A book imported from a ``.docx`` used to keep nothing but the text of the
document: the tables, the images, the footnotes and the tracked changes were
read, thrown away, and the export handed the writer a plain-text document
back.  A writer who opened their own book, edited one paragraph and exported
it got a document with none of their work in it.

These tests pin the behaviour that replaced that.  The library keeps the
blocks every section was made of, and the notes, the media and the styles of
the document the book was imported from:

* an untouched section is exported as it was imported -- its table, its image
  and its notes are still there;
* an edited section is rendered again from its new text, so the markdown the
  editor wrote becomes real Word structure;
* a book that was never imported from a document is rendered from its text,
  exactly as it was before;
* the export reads every section file once, because a section's text and its
  blocks live in the same file.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Final

from lmeditor.library import (
    BookStore,
    build_book_document,
    import_sections,
    import_word_document,
)
from lmeditor.library.export import _IdAllocator, _remap_run
from lmeditor.word.model import (
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordFootnote,
    WordRun,
)
from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter
from tests.word_fixtures import (
    _CORE_PROPERTIES,
    _DOCUMENT_SHELL,
    _FIXED_TIMESTAMP,
    _FOOTNOTES,
    _NUMBERING,
    _ROOT_RELS,
    _STYLES,
    _TINY_PNG,
)

# ============================================================================
# The fixture: a document with two sections, a table and an image
# ============================================================================

#: The prose of the first section.
FIRST_PROSE: Final[str] = "The claim the first section makes."

#: The prose of the second section.
SECOND_PROSE: Final[str] = "The prose of the second section."

#: A cell of the table the second section carries.
TABLE_CELL: Final[str] = "chunk-abc"

#: The relationships of the fixture.  The image is reached by ``rId9``, the
#: id the body refers to.
_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>"""

#: The content types of the fixture.  The image is named by a default for the
#: ``png`` extension, which is how a real package names one.
_CONTENT_TYPES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
</Types>"""

#: The table of the second section.
_TABLE: Final[str] = (
    "<w:tbl>"
    '<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
    '<w:tblGrid><w:gridCol w:w="4675"/><w:gridCol w:w="4675"/></w:tblGrid>'
    "<w:tr><w:trPr><w:tblHeader/></w:trPr>"
    "<w:tc><w:p><w:r><w:t>Evidence</w:t></w:r></w:p></w:tc>"
    "<w:tc><w:p><w:r><w:t>Verdict</w:t></w:r></w:p></w:tc>"
    "</w:tr>"
    "<w:tr>"
    f"<w:tc><w:p><w:r><w:t>{TABLE_CELL}</w:t></w:r></w:p></w:tc>"
    "<w:tc><w:p><w:r><w:t>entailed</w:t></w:r></w:p></w:tc>"
    "</w:tr>"
    "</w:tbl>"
)

#: The picture of the second section, drawn the way Word draws one.
_PICTURE: Final[str] = (
    "<w:p><w:r><w:drawing>"
    '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
    '<wp:extent cx="1905000" cy="1905000"/>'
    '<wp:docPr id="1" name="Picture 1"/>'
    '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    '<pic:nvPicPr><pic:cNvPr id="1" name="image1.png"/><pic:nvPicPr/></pic:nvPicPr>'
    '<pic:blipFill><a:blip r:embed="rId9"/>'
    "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
    '<pic:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
    "</pic:pic></a:graphicData></a:graphic></wp:inline>"
    "</w:drawing></w:r></w:p>"
)

#: The body of the fixture: two sections, the second with a table and a picture.
_BODY: Final[str] = (
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
    "<w:r><w:t>First Section</w:t></w:r></w:p>"
    f"<w:p><w:r><w:t>{FIRST_PROSE}</w:t></w:r></w:p>"
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
    "<w:r><w:t>Second Section</w:t></w:r></w:p>"
    f"<w:p><w:r><w:t>{SECOND_PROSE}</w:t></w:r></w:p>"
    + _TABLE
    + _PICTURE
)

#: The body of the document that replaces the first section.
_REPLACEMENT_BODY: Final[str] = (
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
    "<w:r><w:t>Replacement Section</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>The prose that replaced the first section.</w:t></w:r></w:p>"
)


def _package(body: str) -> bytes:
    """Return a Word package with the given body, a table and a picture.

    The package is assembled the way the word tests assemble theirs: the raw
    OPC parts, written from bytes, in a fixed order and with fixed
    timestamps.
    """
    members: list[tuple[str, bytes]] = [
        ("[Content_Types].xml", _CONTENT_TYPES.encode("utf-8")),
        ("_rels/.rels", _ROOT_RELS.encode("utf-8")),
        ("word/document.xml", _DOCUMENT_SHELL.format(body=body).encode("utf-8")),
        ("word/_rels/document.xml.rels", _RELS.encode("utf-8")),
        ("word/styles.xml", _STYLES.encode("utf-8")),
        ("word/numbering.xml", _NUMBERING.encode("utf-8")),
        ("word/footnotes.xml", _FOOTNOTES.encode("utf-8")),
        ("docProps/core.xml", _CORE_PROPERTIES.encode("utf-8")),
        ("word/media/image1.png", _TINY_PNG),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def _read(body: str) -> WordDocument:
    """Return the document the fixture package holds."""
    return WordReader().read(_package(body))


def _export(store: BookStore, book_id: str) -> bytes:
    """Return the ``.docx`` package the book exports to."""
    return WordWriter().write(build_book_document(store, book_id))


def _document_xml(package: bytes) -> str:
    """Return the body of the exported package."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _names(package: bytes) -> list[str]:
    """Return the names of the parts of the exported package."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return archive.namelist()


def _imported(store: BookStore) -> None:
    """Import the fixture into a book called ``book``."""
    import_word_document(store, "book", "The Book", _read(_BODY))


# ============================================================================
# The round trip keeps what is not plain text
# ============================================================================


def test_the_table_and_the_image_survive_the_export(tmp_path: Path) -> None:
    """An untouched section is exported with the table and the image it had."""
    store = BookStore(tmp_path)
    _imported(store)

    package = _export(store, "book")

    body = _document_xml(package)
    assert "<w:tbl>" in body, "the table of the second section must survive"
    assert TABLE_CELL in body, "the cells of the table must survive"
    assert "word/media/image1.png" in _names(package), (
        "the image of the second section must survive"
    )
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert archive.read("word/media/image1.png") == _TINY_PNG, (
            "the bytes of the image must survive"
        )


def test_an_edited_section_is_rendered_again_and_the_other_one_keeps_its_table(
    tmp_path: Path,
) -> None:
    """An edited section becomes Word structure; the other section is untouched."""
    store = BookStore(tmp_path)
    _imported(store)
    store.write_section(
        "book",
        "section_0001",
        "# A new heading\n\nThe new prose with **bold** words.\n\n"
        "- a bullet\n- another bullet\n\n"
        "| cell | value |\n| --- | --- |\n| a | 1 |\n",
    )

    document = build_book_document(store, "book")
    package = _export(store, "book")
    body = _document_xml(package)

    assert "The new prose with bold words." in document.plain_text()
    assert "A new heading" in document.plain_text()
    # The markdown of the edited section became real Word structure.
    kinds = [block.kind for block in document.blocks]
    assert WordBlockKind.HEADING in kinds
    assert WordBlockKind.LIST_ITEM in kinds
    assert WordBlockKind.TABLE in kinds
    runs = [run for block in document.blocks for run in block.runs]
    assert any(run.bold and run.text == "bold" for run in runs), (
        "the bold of the markdown must survive as bold"
    )
    # The other section was not edited, so its table and its image are still there.
    assert TABLE_CELL in body, "the table of the untouched section must survive"
    assert "word/media/image1.png" in _names(package), (
        "the image of the untouched section must survive"
    )


def test_a_book_of_plain_sections_is_rendered_from_its_text(tmp_path: Path) -> None:
    """A book without an origin is rendered as it always was."""
    store = BookStore(tmp_path)
    import_sections(
        store,
        "book",
        "The Book",
        [
            ("s1", "First", "The prose of the first section."),
            ("s2", "Second", "The prose of the second section."),
        ],
    )

    assert store.read_book_origin("book") is None
    assert not (tmp_path / "book" / "origin.json").exists()

    document = build_book_document(store, "book")

    kinds = [block.kind for block in document.blocks]
    assert kinds[0] is WordBlockKind.TITLE
    assert kinds.count(WordBlockKind.HEADING) == 2
    assert document.plain_text().startswith("The Book")
    assert "The prose of the second section." in document.plain_text()


def test_the_rebuilt_document_is_a_valid_word_document(tmp_path: Path) -> None:
    """The rebuilt document satisfies the model's own invariants."""
    store = BookStore(tmp_path)
    _imported(store)
    store.write_section("book", "section_0001", "# A new heading\n\nThe new prose.\n")

    document = build_book_document(store, "book")

    assert WordDocument.validate_document(document) is document
    block_ids = [block.block_id for block in document.blocks]
    assert len(block_ids) == len(set(block_ids)), "the block ids must be unique"


def test_re_importing_leaves_the_other_sections_origins_intact(tmp_path: Path) -> None:
    """A second import replaces the section it names and leaves the other alone."""
    store = BookStore(tmp_path)
    _imported(store)

    import_word_document(
        store,
        "book",
        "The Book",
        _read(_REPLACEMENT_BODY),
    )

    package = _export(store, "book")
    body = _document_xml(package)

    assert "The prose that replaced the first section." in body, (
        "the section the second import named must hold the new text"
    )
    assert TABLE_CELL in body, (
        "the table of the section the second import did not name must survive"
    )
    assert "word/media/image1.png" in _names(package), (
        "the image of the section the second import did not name must survive"
    )


def test_the_export_reads_every_section_file_once(tmp_path: Path) -> None:
    """The export costs one read of every section and no more."""
    store = BookStore(tmp_path)
    _imported(store)
    assert store.read_count == 0, "the import reads no section body"

    build_book_document(store, "book")

    assert store.read_count == 2, "the export reads one body per section"
    assert store.read_count == store.section_count("book")


def test_the_index_never_reads_the_origin(tmp_path: Path) -> None:
    """Opening, listing and counting a book reads neither a body nor the origin."""
    store = BookStore(tmp_path)
    _imported(store)

    store.open("book")
    store.list_books()
    store.section_count("book")
    store.brief("book")

    assert store.read_count == 0, "the index reads no section body and no origin"


# ============================================================================
# What the store keeps
# ============================================================================


def test_the_store_keeps_the_origin_beside_the_text(tmp_path: Path) -> None:
    """The blocks of a section live in the section's own file."""
    store = BookStore(tmp_path)
    _imported(store)

    section_file = json.loads(
        (tmp_path / "book" / "sections" / "section_0002.json").read_text(encoding="utf-8")
    )
    assert section_file["origin"]["blocks"], "the blocks of the section must be kept"
    assert section_file["origin"]["text"] == section_file["content"], (
        "the text of the origin is the text the blocks rendered to"
    )
    assert section_file["origin"]["heading"]["text"] == "Second Section"

    origin_file = json.loads(
        (tmp_path / "book" / "origin.json").read_text(encoding="utf-8")
    )
    assert origin_file["media"][0]["part_name"] == "word/media/image1.png"
    assert origin_file["media"][0]["data_base64"], "the bytes of the image must be kept"


def test_a_section_that_was_edited_keeps_the_record_of_what_it_was(
    tmp_path: Path,
) -> None:
    """Editing a section does not throw away the document it came from."""
    store = BookStore(tmp_path)
    _imported(store)

    store.write_section("book", "section_0001", "Something else entirely.")

    section_file = json.loads(
        (tmp_path / "book" / "sections" / "section_0001.json").read_text(encoding="utf-8")
    )
    assert section_file["content"] == "Something else entirely."
    assert section_file["origin"]["blocks"], "the origin of the section must be kept"


# ============================================================================
# The notes
# ============================================================================


def _document_with_a_note() -> WordDocument:
    """Return a document whose footnote is named the way the bridge names its own.

    The ids of the notes in the model are opaque strings, so a document may
    carry a footnote called ``f1`` -- the name the manuscript bridge gives the
    first footnote it writes.
    """
    heading = WordBlock(
        block_id="b0001",
        kind=WordBlockKind.HEADING,
        text="The Section",
        level=1,
        runs=(WordRun(text="The Section"),),
    )
    paragraph = WordBlock(
        block_id="b0002",
        kind=WordBlockKind.PARAGRAPH,
        text="A grounded claim",
        runs=(WordRun(text="A grounded claim", footnote_ref="f1"),),
    )
    footnote = WordFootnote(
        footnote_id="f1",
        text="the cited passage",
        runs=(WordRun(text="the cited passage"),),
    )
    return WordDocument(blocks=(heading, paragraph), footnotes=(footnote,))


def test_the_notes_of_the_imported_document_survive_the_export(tmp_path: Path) -> None:
    """A note of the imported document is carried through, and still resolves."""
    store = BookStore(tmp_path)
    import_word_document(store, "book", "The Book", _document_with_a_note())

    document = build_book_document(store, "book")

    assert [footnote.footnote_id for footnote in document.footnotes] == ["f1"]
    references = [
        run.footnote_ref
        for block in document.blocks
        for run in block.runs
        if run.footnote_ref is not None
    ]
    assert references == ["f1"], "the run must still point at the note it named"
    assert WordDocument.validate_document(document) is document


def test_a_note_id_the_document_already_uses_is_not_given_to_a_new_note() -> None:
    """A new note whose id the document already uses is renamed, and its runs follow."""
    ids = _IdAllocator({"footnote": {"f1"}})

    renamed = ids.take("footnote", "f1")

    assert renamed == "f1-2", "the new note must not take the name of the old one"
    assert ids.take("footnote", "f1") == "f1-3", "the renaming must be deterministic"

    run = WordRun(text="a grounded claim", footnote_ref="f1")
    remapped = _remap_run(run, {"f1": renamed}, {}, {})
    assert remapped.footnote_ref == renamed, "the run must follow the note it names"
