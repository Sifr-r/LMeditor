"""The Word doors reach the Kotlin tier.

The reader and the writer used to carry their own implementations of the OOXML
rules.  They are doors now: the Python API is the same, but the answer comes
from the tier.  These tests prove that -- an answer the tier did not give is
not an answer the door may give -- and that the model, which both tiers share
as plain JSON, decodes and encodes exactly what the contract pins.

The tests come in two kinds:

* the **pure-Python** ones (the wire form, and the frozen witnesses) need
  nothing but Python, and they must pass whether or not the tier is built;
* the **integration** ones ask the tier for a document and compare, so they
  need the tier and are marked with ``kotlin_tier``.
"""

from __future__ import annotations

import ast
import base64
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from lmeditor.core.exceptions import ValidationError, WordParseError, WordRenderError
from lmeditor.tier import word as tier_word
from lmeditor.word.model import (
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordComment,
    WordDocument,
    WordEndnote,
    WordFootnote,
    WordHeaderFooterRef,
    WordMedia,
    WordPart,
    WordPartRelationship,
    WordProperties,
    WordRevision,
    WordRevisionKind,
    WordRun,
    WordSection,
    WordStyle,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter
from tests.word_fixtures import build_paragraph_package, build_word365_fixture

# ---------------------------------------------------------------------------
# The wire form (kotlin/word/WIRE.md §2)
# ---------------------------------------------------------------------------

#: Every key of every type, as the contract pins them.  A wire form that does
#: not carry all of them is a wire form the other tier will refuse.
_WIRE_KEYS: dict[str, tuple[str, ...]] = {
    "WordDocument": (
        "blocks",
        "properties",
        "footnotes",
        "endnotes",
        "comments",
        "headers",
        "footers",
        "sections",
        "styles",
        "media",
        "parts",
    ),
    "WordBlock": (
        "block_id",
        "kind",
        "text",
        "level",
        "list_marker",
        "list_group",
        "number_format",
        "number_text",
        "style",
        "runs",
        "table",
    ),
    "WordRun": (
        "text",
        "bold",
        "italic",
        "underline",
        "superscript",
        "subscript",
        "strike",
        "font",
        "size",
        "color",
        "highlight",
        "hyperlink",
        "footnote_ref",
        "endnote_ref",
        "revision",
        "comment_refs",
        "image_ref",
        "equation_xml",
        "part_ref",
        "anchor_extent",
        "format_change_xml",
    ),
    "WordRevision": ("kind", "author", "date", "revision_id"),
    "WordTable": ("rows", "header_rows", "column_widths"),
    "WordTableRow": ("cells",),
    "WordTableCell": (
        "text",
        "row_span",
        "col_span",
        "is_header",
        "is_merge_continuation",
        "width",
        "runs",
        "tables",
    ),
    "WordFootnote": ("footnote_id", "text", "runs"),
    "WordEndnote": ("endnote_id", "text", "runs"),
    "WordComment": (
        "comment_id",
        "author",
        "initials",
        "date",
        "text",
        "runs",
        "anchor_block_id",
        "anchor_text",
    ),
    "WordHeaderFooterRef": ("ref_type", "part_name", "text"),
    "WordSection": (
        "page_width",
        "page_height",
        "orientation",
        "margin_top",
        "margin_right",
        "margin_bottom",
        "margin_left",
        "header_refs",
        "footer_refs",
        "end_block_id",
    ),
    "WordStyle": ("style_id", "name", "based_on", "style_type", "num_id", "num_level"),
    "WordMedia": ("media_id", "part_name", "content_type", "data", "relationship_id"),
    "WordPart": (
        "part_name",
        "content_type",
        "data",
        "relationship_id",
        "relationship_type",
        "relationships",
        "package_relationship",
    ),
    "WordPartRelationship": (
        "relationship_id",
        "relationship_type",
        "target",
        "is_external",
    ),
    "WordProperties": (
        "title",
        "author",
        "subject",
        "keywords",
        "comments",
        "created",
        "modified",
        "revision",
    ),
}

#: The bytes a media part and a carried part carry: not text, so that a
#: decoder which treats them as a string is caught.
_BINARY = b"\x00\x01\xfe\xff\x80binary\x00"

#: A complete wire form of a run, so that the decoder is exercised on every
#: field of a run and not only on the ones that happen to be interesting.
_RUN_WIRE: dict[str, Any] = {
    "text": "A list item",
    "bold": True,
    "italic": False,
    "underline": True,
    "superscript": False,
    "subscript": False,
    "strike": True,
    "font": "Times New Roman",
    "size": 24,
    "color": "FF0000",
    "highlight": "yellow",
    "hyperlink": "https://example.org/target",
    "footnote_ref": "fn1",
    "endnote_ref": "en1",
    "revision": {
        "kind": "insertion",
        "author": "Reviewer",
        "date": "2026-01-02T03:04:05Z",
        "revision_id": "rev1",
    },
    "comment_refs": ["c1"],
    "image_ref": "m1",
    "equation_xml": "<m:oMath>x</m:oMath>",
    "part_ref": "word/charts/chart1.xml",
    "anchor_extent": [914400, 457200],
    "format_change_xml": "<w:rPrChange/>",
}

#: A complete wire form of a table, with its grid and its header row.
_TABLE_WIRE: dict[str, Any] = {
    "rows": [
        {
            "cells": [
                {
                    "text": "Cell",
                    "row_span": 1,
                    "col_span": 1,
                    "is_header": True,
                    "is_merge_continuation": False,
                    "width": 2880,
                    "runs": [],
                    "tables": [],
                }
            ]
        }
    ],
    "header_rows": 1,
    "column_widths": [2880],
}

#: A complete wire form of a block.  A TABLE block carries the table and no
#: inline text, which is what the model insists on.
_BLOCK_WIRE: dict[str, Any] = {
    "block_id": "b0001",
    "kind": "table",
    "text": "",
    "level": 0,
    "list_marker": None,
    "list_group": None,
    "number_format": None,
    "number_text": None,
    "style": None,
    "runs": [],
    "table": _TABLE_WIRE,
}

#: A complete wire form of a list item, which carries a run and no table.
_LIST_BLOCK_WIRE: dict[str, Any] = {
    "block_id": "b0001",
    "kind": "list_item",
    "text": "A list item",
    "level": 2,
    "list_marker": "bullet",
    "list_group": "group-1",
    "number_format": "decimal",
    "number_text": "%1.",
    "style": "ListParagraph",
    "runs": [_RUN_WIRE],
    "table": None,
}


def _document_wire() -> dict[str, Any]:
    """A complete wire form of a document, with every key the contract pins."""
    block = {
        "block_id": "b0001",
        "kind": "paragraph",
        "text": "Body",
        "level": 0,
        "list_marker": None,
        "list_group": None,
        "number_format": None,
        "number_text": None,
        "style": None,
        "runs": [],
        "table": None,
    }
    return {
        "blocks": [block],
        "properties": {
            "title": "The Title",
            "author": "The Author",
            "subject": "The Subject",
            "keywords": "a, b",
            "comments": "A comment",
            "created": "2026-01-02T03:04:05Z",
            "modified": "2026-01-02T03:04:05+00:00",
            "revision": 3,
        },
        "footnotes": [{"footnote_id": "fn1", "text": "The note", "runs": []}],
        "endnotes": [{"endnote_id": "en1", "text": "The endnote", "runs": []}],
        "comments": [
            {
                "comment_id": "c1",
                "author": "Reviewer",
                "initials": "RV",
                "date": "2026-01-02T03:04:05Z",
                "text": "Check this",
                "runs": [],
                "anchor_block_id": "b0001",
                "anchor_text": "Body",
            }
        ],
        "headers": ["The Header"],
        "footers": ["The Footer"],
        "sections": [
            {
                "page_width": 12240,
                "page_height": 15840,
                "orientation": "portrait",
                "margin_top": 1440,
                "margin_right": 1440,
                "margin_bottom": 1440,
                "margin_left": 1440,
                "header_refs": [
                    {
                        "ref_type": "default",
                        "part_name": "word/header1.xml",
                        "text": "The Header",
                    }
                ],
                "footer_refs": [
                    {
                        "ref_type": "default",
                        "part_name": "word/footer1.xml",
                        "text": "The Footer",
                    }
                ],
                "end_block_id": "b0001",
            }
        ],
        "styles": [
            {
                "style_id": "Heading1",
                "name": "heading 1",
                "based_on": "Normal",
                "style_type": "paragraph",
                "num_id": None,
                "num_level": 0,
            }
        ],
        "media": [
            {
                "media_id": "m1",
                "part_name": "word/media/image1.png",
                "content_type": "image/png",
                "data": base64.b64encode(_BINARY).decode("ascii"),
                "relationship_id": "rId1",
            }
        ],
        "parts": [
            {
                "part_name": "word/charts/chart1.xml",
                "content_type": "application/xml",
                "data": base64.b64encode(_BINARY).decode("ascii"),
                "relationship_id": "rId2",
                "relationship_type": "http://example.org/chart",
                "relationships": [
                    {
                        "relationship_id": "rId3",
                        "relationship_type": "http://example.org/style",
                        "target": "word/charts/style1.xml",
                        "is_external": False,
                    }
                ],
                "package_relationship": {
                    "relationship_id": "rId4",
                    "relationship_type": "http://example.org/package",
                    "target": "word/charts/chart1.xml",
                    "is_external": False,
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# The wire form: decoding
# ---------------------------------------------------------------------------


def test_the_decoder_reads_every_field_of_every_type() -> None:
    """A full wire form decodes into a model that carries every field."""
    document = WordDocument.from_wire(_document_wire())

    assert document.properties.title == "The Title"
    assert document.properties.author == "The Author"
    assert document.properties.subject == "The Subject"
    assert document.properties.keywords == "a, b"
    assert document.properties.comments == "A comment"
    assert document.properties.revision == 3
    assert document.properties.created == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert document.properties.modified == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc
    )

    assert document.headers == ("The Header",)
    assert document.footers == ("The Footer",)

    section = document.sections[0]
    assert section.page_width == 12240
    assert section.page_height == 15840
    assert section.orientation == "portrait"
    assert section.margin_top == 1440
    assert section.margin_right == 1440
    assert section.margin_bottom == 1440
    assert section.margin_left == 1440
    assert section.end_block_id == "b0001"
    assert section.header_refs[0].ref_type == "default"
    assert section.header_refs[0].part_name == "word/header1.xml"
    assert section.header_refs[0].text == "The Header"
    assert section.footer_refs[0].ref_type == "default"

    style = document.styles[0]
    assert style.style_id == "Heading1"
    assert style.name == "heading 1"
    assert style.based_on == "Normal"
    assert style.style_type == "paragraph"
    assert style.num_id is None
    assert style.num_level == 0

    media = document.media[0]
    assert media.media_id == "m1"
    assert media.part_name == "word/media/image1.png"
    assert media.content_type == "image/png"
    assert media.data == _BINARY
    assert media.relationship_id == "rId1"

    part = document.parts[0]
    assert part.part_name == "word/charts/chart1.xml"
    assert part.content_type == "application/xml"
    assert part.data == _BINARY
    assert part.relationship_id == "rId2"
    assert part.relationship_type == "http://example.org/chart"
    assert part.relationships[0].relationship_id == "rId3"
    assert part.relationships[0].relationship_type == "http://example.org/style"
    assert part.relationships[0].target == "word/charts/style1.xml"
    assert part.relationships[0].is_external is False
    assert part.package_relationship is not None
    assert part.package_relationship.relationship_id == "rId4"

    footnote = document.footnotes[0]
    assert footnote.footnote_id == "fn1"
    assert footnote.text == "The note"
    assert footnote.runs == ()

    endnote = document.endnotes[0]
    assert endnote.endnote_id == "en1"
    assert endnote.text == "The endnote"

    comment = document.comments[0]
    assert comment.comment_id == "c1"
    assert comment.author == "Reviewer"
    assert comment.initials == "RV"
    assert comment.date == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert comment.text == "Check this"
    assert comment.anchor_block_id == "b0001"
    assert comment.anchor_text == "Body"


def test_the_decoder_reads_every_field_of_a_run() -> None:
    """A run carries its formatting, its revision and its anchors."""
    run = WordRun.from_wire(_RUN_WIRE)

    assert run.text == "A list item"
    assert run.bold is True
    assert run.italic is False
    assert run.underline is True
    assert run.superscript is False
    assert run.subscript is False
    assert run.strike is True
    assert run.font == "Times New Roman"
    assert run.size == 24
    assert run.color == "FF0000"
    assert run.highlight == "yellow"
    assert run.hyperlink == "https://example.org/target"
    assert run.footnote_ref == "fn1"
    assert run.endnote_ref == "en1"
    assert run.comment_refs == ("c1",)
    assert run.image_ref == "m1"
    assert run.equation_xml == "<m:oMath>x</m:oMath>"
    assert run.part_ref == "word/charts/chart1.xml"
    assert run.anchor_extent == (914400, 457200)
    assert run.format_change_xml == "<w:rPrChange/>"

    revision = run.revision
    assert revision is not None
    assert revision.kind is WordRevisionKind.INSERTION
    assert revision.author == "Reviewer"
    assert revision.date == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert revision.revision_id == "rev1"


def test_the_decoder_reads_every_field_of_a_block_and_its_table() -> None:
    """A block carries its shape, and a table carries its grid."""
    block = WordBlock.from_wire(_BLOCK_WIRE)
    assert block.block_id == "b0001"
    assert block.kind is WordBlockKind.TABLE
    assert block.text == ""
    assert block.level == 0
    assert block.list_marker is None
    assert block.list_group is None
    assert block.number_format is None
    assert block.number_text is None
    assert block.style is None
    assert block.runs == ()

    table = block.table
    assert table is not None
    assert table.header_rows == 1
    assert table.column_widths == (2880,)

    cell = table.rows[0].cells[0]
    assert cell.text == "Cell"
    assert cell.row_span == 1
    assert cell.col_span == 1
    assert cell.is_header is True
    assert cell.is_merge_continuation is False
    assert cell.width == 2880
    assert cell.runs == ()
    assert cell.tables == ()


def test_the_decoder_reads_every_field_of_a_list_item() -> None:
    """A list item carries its marker, its numbering and its run."""
    block = WordBlock.from_wire(_LIST_BLOCK_WIRE)
    assert block.kind is WordBlockKind.LIST_ITEM
    assert block.text == "A list item"
    assert block.level == 2
    assert block.list_marker is ListMarker.BULLET
    assert block.list_group == "group-1"
    assert block.number_format == "decimal"
    assert block.number_text == "%1."
    assert block.style == "ListParagraph"
    assert len(block.runs) == 1
    assert block.table is None


def test_an_absent_optional_key_reads_as_null() -> None:
    """A key the model declares optional may be absent, and reads as null."""
    run = WordRun.from_wire(
        {
            "text": "x",
            "bold": False,
            "italic": False,
            "underline": False,
            "superscript": False,
            "subscript": False,
            "strike": False,
            "comment_refs": [],
        }
    )
    assert run.font is None
    assert run.size is None
    assert run.color is None
    assert run.hyperlink is None
    assert run.revision is None
    assert run.anchor_extent is None


def test_an_explicit_null_reads_as_null() -> None:
    """An explicit null and an absent key mean the same thing."""
    absent = WordRun.from_wire(
        {
            "text": "x",
            "bold": False,
            "italic": False,
            "underline": False,
            "superscript": False,
            "subscript": False,
            "strike": False,
            "comment_refs": [],
        }
    )
    explicit = WordRun.from_wire(
        {
            "text": "x",
            "bold": False,
            "italic": False,
            "underline": False,
            "superscript": False,
            "subscript": False,
            "strike": False,
            "comment_refs": [],
            "font": None,
            "size": None,
            "revision": None,
            "anchor_extent": None,
        }
    )
    assert absent == explicit


def test_an_unknown_key_is_ignored() -> None:
    """A key the contract does not know is ignored, so the wire can grow."""
    run = WordRun.from_wire(
        {
            "text": "x",
            "bold": False,
            "italic": False,
            "underline": False,
            "superscript": False,
            "subscript": False,
            "strike": False,
            "comment_refs": [],
            "a_key_from_the_future": {"nested": [1, 2, 3]},
        }
    )
    assert run.text == "x"


def test_a_missing_required_key_is_refused() -> None:
    """A required key that is not there is an error, not a default."""
    with pytest.raises(ValidationError) as excinfo:
        WordBlock.from_wire({"kind": "paragraph"})
    assert "block_id" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        WordDocument.from_wire({})
    assert "blocks" in str(excinfo.value)


def test_a_malformed_value_is_refused() -> None:
    """A value of the wrong type is a malformed payload, not a coercion."""
    with pytest.raises(ValidationError):
        WordBlock.from_wire({**_BLOCK_WIRE, "kind": "not_a_kind"})
    with pytest.raises(ValidationError):
        WordBlock.from_wire({**_BLOCK_WIRE, "level": "two"})
    with pytest.raises(ValidationError):
        WordRun.from_wire(
            {
                "text": "x",
                "bold": "yes",
                "italic": False,
                "underline": False,
                "superscript": False,
                "subscript": False,
                "strike": False,
                "comment_refs": [],
            }
        )


def test_bytes_travel_as_base64() -> None:
    """The bytes of a media part survive the wire unchanged."""
    media = WordMedia.from_wire(
        {
            "media_id": "m1",
            "part_name": "word/media/image1.png",
            "content_type": "image/png",
            "data": base64.b64encode(_BINARY).decode("ascii"),
            "relationship_id": "rId1",
        }
    )
    assert media.data == _BINARY
    assert media.to_wire()["data"] == base64.b64encode(_BINARY).decode("ascii")


def test_bytes_that_are_not_base64_are_refused() -> None:
    """A payload that is not base64 is a malformed payload."""
    with pytest.raises(ValidationError):
        WordMedia.from_wire(
            {
                "media_id": "m1",
                "part_name": "word/media/image1.png",
                "content_type": "image/png",
                "data": "not base64 at all!",
                "relationship_id": "rId1",
            }
        )


def test_timestamps_travel_as_iso_8601() -> None:
    """A timestamp keeps its offset, and a naive one stays naive."""
    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    naive = datetime(2026, 1, 2, 3, 4, 5)

    assert WordProperties(created=aware).to_wire()["created"] == "2026-01-02T03:04:05+00:00"
    assert WordProperties(created=naive).to_wire()["created"] == "2026-01-02T03:04:05"

    decoded = WordProperties.from_wire(
        {
            "title": "",
            "author": "",
            "subject": "",
            "keywords": "",
            "comments": "",
            "created": "2026-01-02T03:04:05Z",
            "modified": "2026-01-02T03:04:05",
            "revision": 1,
        }
    )
    assert decoded.created == aware
    assert decoded.modified == naive


def test_a_timestamp_that_is_not_iso_8601_is_refused() -> None:
    """A timestamp the contract does not spell is a malformed payload."""
    with pytest.raises(ValidationError):
        WordProperties.from_wire(
            {
                "title": "",
                "author": "",
                "subject": "",
                "keywords": "",
                "comments": "",
                "created": "the second of January",
                "modified": None,
                "revision": 1,
            }
        )


def test_an_enum_travels_as_its_wire_string() -> None:
    """An enum is spelled by its wire value, never by its name."""
    assert WordBlockKind.TITLE.value == "title"
    assert ListMarker.BULLET.value == "bullet"
    assert WordRevisionKind.FORMAT_CHANGE.value == "format_change"

    block = WordBlock.from_wire(_LIST_BLOCK_WIRE)
    assert block.kind is WordBlockKind.LIST_ITEM
    assert block.to_wire()["kind"] == "list_item"


def test_the_encoder_emits_every_key_the_contract_pins() -> None:
    """The encoder writes every key of every type, always."""
    document = WordDocument.from_wire(_document_wire())

    assert tuple(document.to_wire().keys()) == _WIRE_KEYS["WordDocument"]
    assert tuple(document.blocks[0].to_wire().keys()) == _WIRE_KEYS["WordBlock"]
    assert tuple(document.footnotes[0].to_wire().keys()) == _WIRE_KEYS["WordFootnote"]
    assert tuple(document.endnotes[0].to_wire().keys()) == _WIRE_KEYS["WordEndnote"]
    assert tuple(document.comments[0].to_wire().keys()) == _WIRE_KEYS["WordComment"]
    assert tuple(document.sections[0].to_wire().keys()) == _WIRE_KEYS["WordSection"]
    assert tuple(document.styles[0].to_wire().keys()) == _WIRE_KEYS["WordStyle"]
    assert tuple(document.media[0].to_wire().keys()) == _WIRE_KEYS["WordMedia"]
    assert tuple(document.parts[0].to_wire().keys()) == _WIRE_KEYS["WordPart"]
    assert (
        tuple(document.parts[0].relationships[0].to_wire().keys())
        == _WIRE_KEYS["WordPartRelationship"]
    )
    assert tuple(document.properties.to_wire().keys()) == _WIRE_KEYS["WordProperties"]
    assert tuple(document.sections[0].header_refs[0].to_wire().keys()) == _WIRE_KEYS[
        "WordHeaderFooterRef"
    ]

    run = WordRun.from_wire(_RUN_WIRE)
    assert tuple(run.to_wire().keys()) == _WIRE_KEYS["WordRun"]
    revision = run.revision
    assert revision is not None
    assert tuple(revision.to_wire().keys()) == _WIRE_KEYS["WordRevision"]

    table = WordTable.from_wire(_TABLE_WIRE)
    assert tuple(table.to_wire().keys()) == _WIRE_KEYS["WordTable"]
    assert tuple(table.rows[0].to_wire().keys()) == _WIRE_KEYS["WordTableRow"]
    assert tuple(table.rows[0].cells[0].to_wire().keys()) == _WIRE_KEYS["WordTableCell"]


def test_a_document_survives_the_wire_unchanged() -> None:
    """What goes on the wire comes back as the same document."""
    document = WordDocument.from_wire(_document_wire())
    assert WordDocument.from_wire(document.to_wire()) == document


# ---------------------------------------------------------------------------
# The frozen witnesses
# ---------------------------------------------------------------------------


def test_the_frozen_reader_still_reads_a_package() -> None:
    """The witness is alive: it is the yardstick the tier is measured against."""
    from lmeditor._reference.word_reader import WordReader as FrozenReader

    document = FrozenReader().read_bytes(build_word365_fixture())
    assert document.blocks
    assert document.plain_text()


def test_the_frozen_writer_still_writes_a_package() -> None:
    """The witness writes a package the frozen reader can read back."""
    from lmeditor._reference.word_reader import WordReader as FrozenReader
    from lmeditor._reference.word_writer import WordWriter as FrozenWriter

    document = FrozenReader().read_bytes(build_word365_fixture())
    package = FrozenWriter().write(document)

    assert package[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert "word/document.xml" in archive.namelist()


def test_the_model_never_reaches_the_tier() -> None:
    """The model is shared by the frozen witnesses, so it must stay pure.

    The witnesses in ``lmeditor._reference`` import this module: a decoder that
    reached across the seam would make the witness reach it too, and the
    parity harness would compare the tier with itself.
    """
    source = Path(__file__).resolve().parents[1] / "src" / "lmeditor" / "word" / "model.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any(name.startswith("lmeditor.tier") for name in imported)
    assert not any(name.startswith("lmeditor.bridge") for name in imported)


# ---------------------------------------------------------------------------
# The doors, against the tier
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("kotlin_tier")
def test_the_reader_door_answers_with_the_tiers_document() -> None:
    """The document the door returns is the document the tier read."""
    package = build_word365_fixture()

    door = WordReader().read_bytes(package)
    tier = tier_word.read_document(package, origin="<bytes>").document

    assert door == tier


@pytest.mark.usefixtures("kotlin_tier")
def test_read_and_read_bytes_agree(tmp_path: Path) -> None:
    """A path and the bytes of that path answer the same document."""
    package = build_word365_fixture()
    path = tmp_path / "door-fixture.docx"
    path.write_bytes(package)

    from_path = WordReader().read(path)
    from_bytes = WordReader().read_bytes(package)

    assert from_path == from_bytes


@pytest.mark.usefixtures("kotlin_tier")
def test_the_omission_register_is_the_tiers() -> None:
    """The omissions the door reports are the omissions the tier recorded."""
    reader = WordReader()
    reader.read_bytes(build_word365_fixture())

    tier = tier_word.read_document(build_word365_fixture(), origin="<bytes>")
    assert reader.last_omissions == tier.omissions


@pytest.mark.usefixtures("kotlin_tier")
def test_the_package_relationships_are_the_tiers() -> None:
    """The package root's relationships are the ones the tier read."""
    reader = WordReader()
    reader.read_bytes(build_word365_fixture())

    tier = tier_word.read_document(build_word365_fixture(), origin="<bytes>")
    assert reader.last_package_relationships == tier.package_relationships


@pytest.mark.usefixtures("kotlin_tier")
def test_the_package_relationships_are_sorted_by_id() -> None:
    """The relationships come back in a stable order."""
    reader = WordReader()
    reader.read_bytes(build_word365_fixture())

    ids = [rel.relationship_id for rel in reader.last_package_relationships]
    assert ids == sorted(ids)


@pytest.mark.usefixtures("kotlin_tier")
def test_a_document_carrying_the_next_line_control_is_read() -> None:
    """A document whose text carries U+0085 is read, not refused.

    The tier normalises the next-line control the way Python does -- it is
    whitespace in Python -- so the block it sends carries the normalised text
    and the model the door rebuilds accepts it. While the tier still believed
    Java's idea of whitespace, it kept the control, the model removed it, and
    the document the tier had just parsed was refused by the model.
    """
    package = build_paragraph_package("abc\u0085def")

    document = WordReader().read_bytes(package)

    block = document.blocks[0]
    assert block.text == "abc def"
    assert block.runs[0].text == "abc\u0085def"


@pytest.mark.usefixtures("kotlin_tier")
def test_the_writer_door_returns_the_tiers_bytes() -> None:
    """The bytes the door returns are the bytes the tier rendered."""
    document = WordReader().read_bytes(build_word365_fixture())

    door = WordWriter().write(document)
    tier = tier_word.write_document(document)

    assert door == tier


@pytest.mark.usefixtures("kotlin_tier")
def test_the_writer_is_byte_deterministic() -> None:
    """The same document always produces the same bytes."""
    document = WordReader().read_bytes(build_word365_fixture())

    first = WordWriter().write(document)
    second = WordWriter().write(document)

    assert first == second


@pytest.mark.usefixtures("kotlin_tier")
def test_a_read_write_read_round_trip() -> None:
    """What the reader read, the writer writes, and the reader reads again."""
    document = WordReader().read_bytes(build_word365_fixture())

    package = WordWriter().write(document)
    again = WordReader().read_bytes(package)

    assert again.plain_text() == document.plain_text()
    assert len(again.blocks) == len(document.blocks)


@pytest.mark.usefixtures("kotlin_tier")
def test_a_missing_path_is_a_parse_error() -> None:
    """A path that is not there is refused, as it always was."""
    missing = Path(__file__).resolve().parent / "fixtures" / "not-a-real-file.docx"

    with pytest.raises(WordParseError) as excinfo:
        WordReader().read(missing)
    assert str(missing) in str(excinfo.value)


@pytest.mark.usefixtures("kotlin_tier")
def test_bytes_that_are_not_bytes_are_refused() -> None:
    """A payload that is not bytes is refused, as it always was."""
    with pytest.raises(WordParseError) as excinfo:
        WordReader().read_bytes("not bytes")  # type: ignore[arg-type]
    assert "<bytes>" in str(excinfo.value)


@pytest.mark.usefixtures("kotlin_tier")
def test_a_malformed_package_is_a_parse_error(tmp_path: Path) -> None:
    """A package that is not a package is refused, as it always was."""
    with pytest.raises(WordParseError):
        WordReader().read_bytes(b"this is not a .docx package")


@pytest.mark.usefixtures("kotlin_tier")
def test_the_origin_reaches_the_tier(tmp_path: Path) -> None:
    """The path the reader was given is the origin the tier names.

    The tier never touches the filesystem, so the reader reports the origin
    itself; the error the caller reads must name the path it asked for.
    """
    path = tmp_path / "broken.docx"
    path.write_bytes(b"this is not a .docx package")

    with pytest.raises(WordParseError) as excinfo:
        WordReader().read(path)
    assert str(path) in str(excinfo.value)


@pytest.mark.usefixtures("kotlin_tier")
def test_an_invalid_document_is_a_render_error() -> None:
    """A document the model refuses is refused by the writer, too.

    The writer validates the model before it renders anything, so the
    refusal happens on the Python side and never reaches the tier.
    """
    broken = WordDocument.model_construct(
        blocks=(WordBlock.model_construct(block_id="b1", kind=WordBlockKind.HEADING),)
    )

    with pytest.raises(WordRenderError):
        WordWriter().write(broken)
