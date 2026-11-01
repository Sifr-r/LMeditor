"""The library keeps the relationships a carried part holds.

A part the document does not reach directly -- a chart's colour style, the
workbook embedded behind it -- is reached from *another part's* relationship
list.  The store converts every part to and from its on-disk form, so a book
whose parts were stored without their relationships would lose them: the
exported ``.docx`` would no longer hold the chart's colour style or its
workbook, and the writer would receive a document Word calls corrupt.

These tests pin the three properties that matter:

* the relationships of a part are written to ``origin.json`` and come back
  unchanged -- same ids, same types, same targets, same flags, same order;
* an ``origin.json`` written before the relationships were kept still loads,
  and the parts it holds come back with no relationships at all;
* a part that holds no relationships is stored with none and comes back with
  none.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, cast

from lmeditor.library import BookStore, import_word_document
from lmeditor.word.model import (
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordPart,
    WordPartRelationship,
    WordRun,
)
from lmeditor.word.text import normalize_whitespace

# ============================================================================
# The fixture: a chart that holds the parts it is drawn from
# ============================================================================

#: The name of the chart part the fixture carries.
_CHART_PART: Final[str] = "word/charts/chart1.xml"

#: The bytes of the chart part, so that the round trip can be checked for the
#: content of the part as well as for its relationships.
_CHART_BYTES: Final[bytes] = b"<c:chartSpace/>"

#: The relationship the chart holds to its colour style.
_COLOUR_STYLE: Final[WordPartRelationship] = WordPartRelationship(
    relationship_id="rId1",
    relationship_type=(
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chartColorStyle"
    ),
    target="word/charts/colors1.xml",
)

#: The relationship the chart holds to its quick style.
_QUICK_STYLE: Final[WordPartRelationship] = WordPartRelationship(
    relationship_id="rId2",
    relationship_type=(
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chartQuickStyle"
    ),
    target="word/charts/style1.xml",
)

#: The relationship the chart holds to the workbook embedded behind it.
_WORKBOOK: Final[WordPartRelationship] = WordPartRelationship(
    relationship_id="rId3",
    relationship_type=(
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
    ),
    target="../embeddings/Microsoft_Excel_Worksheet1.xlsx",
)

#: A relationship that points outside the package.
_EXTERNAL: Final[WordPartRelationship] = WordPartRelationship(
    relationship_id="rId4",
    relationship_type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
    target="https://example.org/figure.png",
    is_external=True,
)

#: Every relationship the chart holds, in the order it holds them.
_CHART_RELATIONSHIPS: Final[tuple[WordPartRelationship, ...]] = (
    _COLOUR_STYLE,
    _QUICK_STYLE,
    _WORKBOOK,
    _EXTERNAL,
)


def _block(block_id: str, kind: WordBlockKind, text: str, level: int = 0) -> WordBlock:
    """Return a Word block whose text is the normalisation of its run."""
    runs = (WordRun(text=text),) if text else ()
    return WordBlock(
        block_id=block_id,
        kind=kind,
        text=normalize_whitespace(text),
        level=level,
        runs=runs,
    )


def _document(parts: tuple[WordPart, ...]) -> WordDocument:
    """Return a document with one chapter and the given package parts."""
    return WordDocument(
        blocks=(
            _block("b1", WordBlockKind.HEADING, "The Chapter", level=1),
            _block("b2", WordBlockKind.PARAGRAPH, "The body of the chapter."),
        ),
        parts=parts,
    )


def _chart_part() -> WordPart:
    """Return a chart part that holds the relationships the chart is made of."""
    return WordPart(
        part_name=_CHART_PART,
        content_type="application/vnd.openxmlformats-officedocument.drawingml.chart+xml",
        data=_CHART_BYTES,
        relationship_id="rId7",
        relationship_type=(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
        ),
        relationships=_CHART_RELATIONSHIPS,
    )


def _plain_part() -> WordPart:
    """Return a part that holds no relationship of its own."""
    return WordPart(
        part_name="word/glossary/document.xml",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.glossary+xml"
        ),
        data=b"<w:glossaryDocument/>",
        relationship_id="rId8",
        relationship_type=(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/glossaryDocument"
        ),
    )


def _origin_file(tmp_path: Path, book_id: str = "book") -> dict[str, Any]:
    """Return the stored origin record of a book, as it was written to disk."""
    path = tmp_path / book_id / "origin.json"
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


# ============================================================================
# The relationships of a part are kept
# ============================================================================


def test_the_relationships_of_a_part_are_written_to_the_origin_file(tmp_path: Path) -> None:
    """A stored part carries the relationships the chart holds."""
    store = BookStore(tmp_path)
    import_word_document(store, "book", "The Book", _document((_chart_part(),)))

    stored = _origin_file(tmp_path)
    parts = stored["parts"]
    assert isinstance(parts, list)
    stored_part = parts[0]
    assert isinstance(stored_part, dict)

    relationships = stored_part["relationships"]
    assert isinstance(relationships, list)
    assert [entry["relationship_id"] for entry in relationships] == [
        "rId1",
        "rId2",
        "rId3",
        "rId4",
    ], "the relationships must be stored in the order the part holds them"
    assert relationships[0]["relationship_type"] == _COLOUR_STYLE.relationship_type
    assert relationships[0]["target"] == "word/charts/colors1.xml"
    assert relationships[2]["target"] == "../embeddings/Microsoft_Excel_Worksheet1.xlsx"
    assert relationships[3]["is_external"] is True, (
        "a target outside the package must be marked as such"
    )
    assert relationships[0]["is_external"] is False


def test_reading_the_book_back_yields_the_same_relationships(tmp_path: Path) -> None:
    """The relationships of a part survive the store unchanged."""
    store = BookStore(tmp_path)
    import_word_document(store, "book", "The Book", _document((_chart_part(),)))

    origin = store.read_book_origin("book")
    assert origin is not None

    part = origin.parts[0]
    assert part.part_name == _CHART_PART
    assert part.data == _CHART_BYTES, "the bytes of the part must survive as well"
    assert part.relationships == _CHART_RELATIONSHIPS, (
        "the relationships must come back with the same ids, types, targets and flags"
    )
    assert [
        (entry.relationship_id, entry.relationship_type, entry.target, entry.is_external)
        for entry in part.relationships
    ] == [
        (entry.relationship_id, entry.relationship_type, entry.target, entry.is_external)
        for entry in _CHART_RELATIONSHIPS
    ], "the relationships must come back in the order they were held"


def test_the_relationships_survive_a_save_and_a_load_of_the_whole_book(tmp_path: Path) -> None:
    """Writing the origin record again does not lose the relationships."""
    store = BookStore(tmp_path)
    import_word_document(store, "book", "The Book", _document((_chart_part(),)))

    origin = store.read_book_origin("book")
    assert origin is not None
    store.set_book_origin("book", origin)

    reloaded = store.read_book_origin("book")
    assert reloaded is not None
    assert reloaded.parts[0].relationships == _CHART_RELATIONSHIPS


# ============================================================================
# The files written before the relationships were kept still load
# ============================================================================


def test_an_origin_written_before_the_relationships_were_kept_still_loads(
    tmp_path: Path,
) -> None:
    """An ``origin.json`` without the field loads, and its parts hold nothing."""
    store = BookStore(tmp_path)
    import_word_document(store, "book", "The Book", _document((_chart_part(),)))

    path = tmp_path / "book" / "origin.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    for part in stored["parts"]:
        part.pop("relationships")
    path.write_text(json.dumps(stored, indent=2), encoding="utf-8")

    origin = store.read_book_origin("book")
    assert origin is not None

    part = origin.parts[0]
    assert part.part_name == _CHART_PART, "the part itself must not be lost"
    assert part.data == _CHART_BYTES, "the bytes of the part must not be lost"
    assert part.relationships == (), "a part stored without relationships holds none"


# ============================================================================
# A part that holds no relationship
# ============================================================================


def test_a_part_without_relationships_is_stored_and_read_back_empty(tmp_path: Path) -> None:
    """A part that holds nothing is stored with an empty list, and comes back so."""
    store = BookStore(tmp_path)
    import_word_document(store, "book", "The Book", _document((_plain_part(),)))

    stored = _origin_file(tmp_path)
    parts = stored["parts"]
    assert isinstance(parts, list)
    stored_part = parts[0]
    assert isinstance(stored_part, dict)
    assert stored_part["relationships"] == []

    origin = store.read_book_origin("book")
    assert origin is not None
    assert origin.parts[0].relationships == ()
    assert origin.parts[0].part_name == "word/glossary/document.xml"
