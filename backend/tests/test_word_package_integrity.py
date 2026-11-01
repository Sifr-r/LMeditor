"""Tests for the integrity of the package the Word writer builds.

A ``.docx`` is not a folder of files: it is a package, and its parts are
held together by relationships.  Word validates that structure when it opens
a package and refuses the package when the structure is broken -- a part no
relationship reaches, a relationship part handed over as content, a chart
that comes back at the wrong size.

These tests pin the three rules that keep the package intact:

* a part the document reached is related from the document, and a part it did
  not reach is related from the part that owns it;
* a part no relationship path reaches does not travel, and the loss is named
  rather than silent;
* an anchored element is drawn at the size it was recorded at.

The model is built by hand rather than read from a fixture: the package has
to be right for a document that was never in a file to begin with.
"""

from __future__ import annotations

import io
import zipfile
from typing import Final
from xml.etree import ElementTree

from lmeditor.word.model import (
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordMedia,
    WordPart,
    WordPartRelationship,
    WordRun,
)
from lmeditor.word.writer import WordWriter

#: The namespace a relationship part is written in.
_RELS_NAMESPACE: Final[str] = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)

#: The relationship type of a chart, as Word spells it.
_CHART_RELATIONSHIP: Final[str] = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
)

#: The relationship type of the colour style a chart is drawn with.
_CHART_COLORS_RELATIONSHIP: Final[str] = (
    "http://schemas.microsoft.com/office/2011/relationships/chartColorStyle"
)

#: The relationship type of the quick style a chart is drawn with.
_CHART_STYLE_RELATIONSHIP: Final[str] = (
    "http://schemas.microsoft.com/office/2011/relationships/chartStyle"
)

#: The relationship type of the workbook embedded behind a chart.
_PACKAGE_RELATIONSHIP: Final[str] = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
)

#: The name of the chart part.
_CHART_PART: Final[str] = "word/charts/chart1.xml"

#: The name of the colour style the chart is drawn with.
_COLORS_PART: Final[str] = "word/charts/colors1.xml"

#: The name of the quick style the chart is drawn with.
_STYLE_PART: Final[str] = "word/charts/style1.xml"

#: The name of the workbook embedded behind the chart.
_WORKBOOK_PART: Final[str] = "word/embeddings/Microsoft_Excel_Worksheet1.xlsx"

#: The size Word wrote the chart at: 432 by 252 points, in EMUs.
_CHART_EXTENT: Final[tuple[int, int]] = (5486400, 3200400)

#: The relationship type of the custom document properties, as Word spells it.
_CUSTOM_PROPERTIES_RELATIONSHIP: Final[str] = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "custom-properties"
)

#: The name of the custom document properties part.
_CUSTOM_PROPERTIES_PART: Final[str] = "docProps/custom.xml"

#: The relationship parts a package legitimately holds.
_RELATIONSHIP_PARTS: Final[frozenset[str]] = frozenset(
    {
        "_rels/.rels",
        "word/_rels/document.xml.rels",
        "word/charts/_rels/chart1.xml.rels",
    }
)


# ============================================================================
# The model
# ============================================================================


def _chart_part() -> WordPart:
    """Return the chart part, with the parts it owns named by its rels.

    The chart is the part the document reached; the colour style, the quick
    style and the embedded workbook are reached from the chart's *own*
    relationship part, which is why they carry no relationship id of their
    own.
    """
    return WordPart(
        part_name=_CHART_PART,
        content_type=(
            "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
        ),
        data=b"<c:chartSpace/>",
        relationship_id="rId4",
        relationship_type=_CHART_RELATIONSHIP,
        relationships=(
            WordPartRelationship(
                relationship_id="rId1",
                relationship_type=_CHART_COLORS_RELATIONSHIP,
                target=_COLORS_PART,
            ),
            WordPartRelationship(
                relationship_id="rId2",
                relationship_type=_CHART_STYLE_RELATIONSHIP,
                target=_STYLE_PART,
            ),
            WordPartRelationship(
                relationship_id="rId3",
                relationship_type=_PACKAGE_RELATIONSHIP,
                target=_WORKBOOK_PART,
            ),
        ),
    )


def _carried_part(
    part_name: str,
    content_type: str,
    data: bytes,
) -> WordPart:
    """Return a carried part the document did not reach."""
    return WordPart(part_name=part_name, content_type=content_type, data=data)


def _png(width: int, height: int) -> bytes:
    """Return the head of a PNG of the given pixel size.

    The size of an image is read from its head, so a picture whose size is
    to be told apart from its recorded extent needs a head that holds one.
    """
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
    )


def _custom_properties_part(rel_id: str) -> WordPart:
    """Return the custom document properties part of a package.

    The part is reached from the package root and not from the document, so
    the relationship that reaches it belongs to ``_rels/.rels``.
    """
    return WordPart(
        part_name=_CUSTOM_PROPERTIES_PART,
        content_type=(
            "application/vnd.openxmlformats-officedocument.custom-properties+xml"
        ),
        data=b"<Properties/>",
        package_relationship=WordPartRelationship(
            relationship_id=rel_id,
            relationship_type=_CUSTOM_PROPERTIES_RELATIONSHIP,
            target=_CUSTOM_PROPERTIES_PART,
        ),
    )


def _chart_document() -> WordDocument:
    """Return a document that draws a chart of the recorded size."""
    run = WordRun(part_ref=_CHART_PART, anchor_extent=_CHART_EXTENT)
    block = WordBlock(
        block_id="b1",
        kind=WordBlockKind.PARAGRAPH,
        text="",
        runs=(run,),
    )
    return WordDocument(
        blocks=(block,),
        parts=(
            _chart_part(),
            _carried_part(
                _COLORS_PART,
                "application/vnd.ms-office.chartcolorstyle+xml",
                b"<cs:colorStyle/>",
            ),
            _carried_part(
                _STYLE_PART,
                "application/vnd.ms-office.chartstyle+xml",
                b"<cs:chartStyle/>",
            ),
            _carried_part(
                _WORKBOOK_PART,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                b"PK\x03\x04workbook",
            ),
        ),
    )


# ============================================================================
# Helpers
# ============================================================================


def _relationships(rels_xml: str) -> dict[str, dict[str, str]]:
    """Return the relationships of a relationship part, keyed by id.

    Each relationship is returned as its own attributes, so that a test can
    assert on the type, the target and the target mode of the same
    relationship.
    """
    root = ElementTree.fromstring(rels_xml)
    found: dict[str, dict[str, str]] = {}
    for element in root.findall(f"{{{_RELS_NAMESPACE}}}Relationship"):
        found[element.get("Id", "")] = dict(element.attrib)
    return found


def _members(package: bytes) -> list[str]:
    """Return the names of the parts the package holds."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return archive.namelist()


def _member(package: bytes, name: str) -> str:
    """Return the markup of one part of the package."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return archive.read(name).decode("utf-8")


def _builder_root_relationships() -> dict[str, dict[str, str]]:
    """Return the relationships the package builder writes on its own.

    The package root reaches the document and the producer properties of
    the template; a document that carries no part of its own adds nothing
    to that list, and this is what it looks like when nothing is added.
    """
    writer = WordWriter()
    package = writer.write(WordDocument())
    return _relationships(_member(package, "_rels/.rels"))


# ============================================================================
# The parts travel, and travel under the right relationships
# ============================================================================


def test_the_parts_a_chart_owns_travel_with_it() -> None:
    """The parts a chart owns travel, even though the document never reached them."""
    writer = WordWriter()
    package = writer.write(_chart_document())
    assert writer.last_omissions == [], writer.last_omissions

    names = _members(package)
    assert _CHART_PART in names
    assert _COLORS_PART in names
    assert _STYLE_PART in names
    assert _WORKBOOK_PART in names


def test_the_relationships_a_chart_owns_are_written_beside_it() -> None:
    """A chart's own relationships are written into the chart's ``.rels`` file.

    They belong to the chart, not to the document: the colour style, the
    quick style and the embedded workbook are reached from the chart, and the
    targets are relative to the chart's own directory.
    """
    writer = WordWriter()
    package = writer.write(_chart_document())

    rels = _relationships(_member(package, "word/charts/_rels/chart1.xml.rels"))
    assert len(rels) == 3, f"the chart must hold three relationships: {rels}"
    assert rels["rId1"]["Type"] == _CHART_COLORS_RELATIONSHIP
    assert rels["rId1"]["Target"] == "colors1.xml"
    assert rels["rId2"]["Type"] == _CHART_STYLE_RELATIONSHIP
    assert rels["rId2"]["Target"] == "style1.xml"
    assert rels["rId3"]["Type"] == _PACKAGE_RELATIONSHIP
    assert rels["rId3"]["Target"] == "../embeddings/Microsoft_Excel_Worksheet1.xlsx"


def test_the_document_relates_the_chart_and_nothing_that_is_not_reached() -> None:
    """The document holds one relationship to the chart, and no other part.

    A part the document did not reach must not be related from the document:
    the relationship would be one Word cannot resolve.
    """
    writer = WordWriter()
    package = writer.write(_chart_document())

    rels = _relationships(_member(package, "word/_rels/document.xml.rels"))
    charts = [
        rel_id
        for rel_id, rel in rels.items()
        if rel.get("Target") == "charts/chart1.xml"
    ]
    assert charts == ["rId4"], f"the chart must be related exactly once: {rels}"
    assert rels["rId4"]["Type"] == _CHART_RELATIONSHIP
    assert not any(
        rel.get("Target", "").endswith(".rels") for rel in rels.values()
    ), f"no relationship may point at a relationship part: {rels}"


def test_no_relationship_part_is_written_as_content() -> None:
    """A ``.rels`` file is a relationship list, never content."""
    writer = WordWriter()
    package = writer.write(_chart_document())

    names = _members(package)
    written = {name for name in names if name.endswith(".rels")}
    assert set(_RELATIONSHIP_PARTS) <= written, (
        f"the package must hold the relationships it needs: {sorted(written)}"
    )
    assert all(
        name.split("/")[-2] == "_rels" for name in written
    ), f"the package holds a relationship part as content: {sorted(written)}"


def test_the_chart_is_drawn_at_the_size_it_was_recorded_at() -> None:
    """The chart comes back at the size Word wrote it at."""
    writer = WordWriter()
    package = writer.write(_chart_document())

    body = _member(package, "word/document.xml")
    assert 'cx="5486400"' in body, "the width of the chart must survive"
    assert 'cy="3200400"' in body, "the height of the chart must survive"


def test_an_embedded_object_is_drawn_at_the_size_it_was_recorded_at() -> None:
    """The shape of an embedded object is sized from the recorded extent.

    A shape is sized in points, so the extent is converted, and it is
    written as a whole number of them.
    """
    part = WordPart(
        part_name="word/embeddings/oleObject1.bin",
        content_type="application/vnd.openxmlformats-officedocument.oleObject",
        data=b"object",
        relationship_id="rId4",
        relationship_type=(
            "http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/oleObject"
        ),
    )
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="b1",
                kind=WordBlockKind.PARAGRAPH,
                text="",
                runs=(
                    WordRun(
                        part_ref=part.part_name,
                        anchor_extent=(2540000, 1270000),
                    ),
                ),
            ),
        ),
        parts=(part,),
    )
    writer = WordWriter()
    package = writer.write(document)

    body = _member(package, "word/document.xml")
    assert "width:200pt;height:100pt" in body, (
        f"the shape must be sized from the recorded extent: {body}"
    )
    assert ".0pt" not in body, "a size is written as whole points"


def test_a_picture_prefers_the_recorded_extent_over_its_pixel_size() -> None:
    """A picture is drawn at the size it was recorded at.

    The pixel size of an image is only a fallback: Word scales a picture by
    rewriting the extent of the drawing that holds it.
    """
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="b1",
                kind=WordBlockKind.PARAGRAPH,
                text="",
                runs=(
                    WordRun(image_ref="m1", anchor_extent=(1828800, 914400)),
                    WordRun(image_ref="m2"),
                ),
            ),
        ),
        media=(
            WordMedia(
                media_id="m1",
                part_name="word/media/image1.png",
                content_type="image/png",
                data=_png(96, 96),
            ),
            WordMedia(
                media_id="m2",
                part_name="word/media/image2.png",
                content_type="image/png",
                data=_png(96, 96),
            ),
        ),
    )
    writer = WordWriter()
    package = writer.write(document)

    body = _member(package, "word/document.xml")
    assert 'cx="1828800"' in body, "the recorded extent must win"
    assert (
        'cx="914400"' in body
    ), "an image without a recorded extent keeps its own size"


def test_a_diagram_is_drawn_at_the_size_it_was_recorded_at() -> None:
    """A diagram is drawn at the size it was recorded at."""
    parts = tuple(
        WordPart(
            part_name=f"word/diagrams/{name}.xml",
            content_type=(
                "application/vnd.openxmlformats-officedocument.drawingml."
                "diagramData+xml"
            ),
            data=b"<dgm:dataModel/>",
            relationship_id=rel_id,
            relationship_type=(
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
                f"{kind}"
            ),
        )
        for name, kind, rel_id in (
            ("data1", "diagramData", "rId4"),
            ("layout1", "diagramLayout", "rId5"),
            ("quickStyle1", "diagramQuickStyle", "rId6"),
            ("colors1", "diagramColors", "rId7"),
        )
    )
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="b1",
                kind=WordBlockKind.PARAGRAPH,
                text="",
                runs=(
                    WordRun(
                        part_ref="word/diagrams/data1.xml",
                        anchor_extent=(2743200, 1828800),
                    ),
                ),
            ),
        ),
        parts=parts,
    )
    writer = WordWriter()
    package = writer.write(document)

    body = _member(package, "word/document.xml")
    assert 'cx="2743200"' in body, "the width of the diagram must survive"
    assert 'cy="1828800"' in body, "the height of the diagram must survive"
    assert "dgm:relIds" in body, "the diagram must still be drawn from its parts"


# ============================================================================
# The parts the package root reaches
# ============================================================================


def test_a_part_the_package_root_reaches_travels_from_the_package_root() -> None:
    """A part the package root reached is related from the package root.

    The custom document properties belong to the package and not to the
    document, so the relationship that reaches them belongs to the root's
    own list, and the document's list stays exactly what the body needs.
    """
    writer = WordWriter()
    package = writer.write(WordDocument(parts=(_custom_properties_part("rId5"),)))

    assert writer.last_omissions == [], writer.last_omissions
    assert _CUSTOM_PROPERTIES_PART in _members(package)

    root = _relationships(_member(package, "_rels/.rels"))
    assert root["rId5"]["Type"] == _CUSTOM_PROPERTIES_RELATIONSHIP
    assert root["rId5"]["Target"] == _CUSTOM_PROPERTIES_PART

    document_rels = _relationships(_member(package, "word/_rels/document.xml.rels"))
    assert not any(
        rel.get("Target", "").endswith("custom.xml")
        for rel in document_rels.values()
    ), f"the document must not reach a part of the package: {document_rels}"


def test_the_package_root_keeps_the_id_a_part_was_reached_by() -> None:
    """A part the package root reached keeps the id it was reached by.

    The builder's own relationship is moved aside rather than lost, so the
    package still reaches the document it held before.
    """
    writer = WordWriter()
    package = writer.write(WordDocument(parts=(_custom_properties_part("rId1"),)))

    root = _relationships(_member(package, "_rels/.rels"))
    assert root["rId1"]["Type"] == _CUSTOM_PROPERTIES_RELATIONSHIP
    assert root["rId1"]["Target"] == _CUSTOM_PROPERTIES_PART

    document_rels = [
        rel
        for rel in root.values()
        if rel["Type"].endswith("/officeDocument")
    ]
    assert len(document_rels) == 1, (
        f"the package must still reach its document: {root}"
    )
    assert document_rels[0]["Target"] == "word/document.xml"


def test_the_package_root_is_untouched_by_the_parts_the_document_reached() -> None:
    """A document whose parts are all document-reached adds nothing to the root.

    The root is the package's own list of what it reaches; a part the
    document reached belongs to the document's list and not to that one.
    """
    writer = WordWriter()
    package = writer.write(_chart_document())

    root = _relationships(_member(package, "_rels/.rels"))
    assert root == _builder_root_relationships()


def test_a_part_the_package_root_reaches_externally_does_not_travel() -> None:
    """An external package relationship reaches a URL, not a part of the package.

    The relationship is kept, because the package did hold it, but the bytes
    the model carried have no place in the package and are named as lost.
    """
    part = WordPart(
        part_name="docProps/linked.xml",
        content_type="application/xml",
        data=b"<linked/>",
        package_relationship=WordPartRelationship(
            relationship_id="rId6",
            relationship_type=_CUSTOM_PROPERTIES_RELATIONSHIP,
            target="https://example.org/linked.xml",
            is_external=True,
        ),
    )
    writer = WordWriter()
    package = writer.write(WordDocument(parts=(part,)))

    root = _relationships(_member(package, "_rels/.rels"))
    assert root["rId6"]["Target"] == "https://example.org/linked.xml"
    assert root["rId6"]["TargetMode"] == "External"
    assert "docProps/linked.xml" not in _members(package)
    assert any(
        "docProps/linked.xml" in omission for omission in writer.last_omissions
    ), writer.last_omissions


# ============================================================================
# The parts that cannot travel are named
# ============================================================================


def test_a_part_nothing_reaches_does_not_travel_and_is_named() -> None:
    """A part no relationship reaches cannot travel, and the loss is named.

    A package cannot hold a part that nothing points at, and inventing a
    relationship for it is exactly what Word rejects.
    """
    document = WordDocument(
        parts=(_carried_part("word/orphan.xml", "application/xml", b"<orphan/>"),)
    )
    writer = WordWriter()
    package = writer.write(document)

    assert "word/orphan.xml" not in _members(package)
    assert any(
        "word/orphan.xml" in omission for omission in writer.last_omissions
    ), writer.last_omissions
    root = _relationships(_member(package, "_rels/.rels"))
    assert not any(
        rel.get("Target", "").endswith("orphan.xml") for rel in root.values()
    ), f"the package root must not reach a part nothing reached: {root}"


def test_a_relationship_part_is_refused_and_named() -> None:
    """A carried part that is a relationship part is refused, and named."""
    document = WordDocument(
        parts=(
            _carried_part(
                "word/charts/_rels/chart1.xml.rels",
                "application/vnd.openxmlformats-package.relationships+xml",
                b"<Relationships/>",
            ),
        )
    )
    writer = WordWriter()
    package = writer.write(document)

    assert "word/charts/_rels/chart1.xml.rels" not in _members(package)
    assert any(
        "word/charts/_rels/chart1.xml.rels" in omission
        for omission in writer.last_omissions
    ), writer.last_omissions


def test_a_relationship_that_points_at_nothing_is_named() -> None:
    """A relationship to a part the package does not hold is a loss, and is named."""
    chart = WordPart(
        part_name=_CHART_PART,
        content_type=(
            "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
        ),
        data=b"<c:chartSpace/>",
        relationship_id="rId4",
        relationship_type=_CHART_RELATIONSHIP,
        relationships=(
            WordPartRelationship(
                relationship_id="rId1",
                relationship_type=_CHART_COLORS_RELATIONSHIP,
                target="word/charts/missing.xml",
            ),
        ),
    )
    writer = WordWriter()
    package = writer.write(WordDocument(parts=(chart,)))

    assert "word/charts/missing.xml" not in _members(package)
    assert any(
        "missing.xml" in omission for omission in writer.last_omissions
    ), writer.last_omissions


# ============================================================================
# The regression guard
# ============================================================================


def test_a_document_without_carried_parts_still_writes_a_package() -> None:
    """A document that carries no part at all is written as it always was.

    The rules above only concern the parts a document carries, so a document
    that carries none must not be changed by them.
    """
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="b1",
                kind=WordBlockKind.PARAGRAPH,
                text="Evidence is in the figure.",
            ),
        )
    )
    writer = WordWriter()
    package = writer.write(document)
    assert writer.last_omissions == [], writer.last_omissions

    names = _members(package)
    assert "word/document.xml" in names
    assert "word/_rels/document.xml.rels" in names
    assert all(
        name.split("/")[-2] == "_rels" for name in names if name.endswith(".rels")
    ), f"the package holds a relationship part as content: {sorted(names)}"
    assert "Evidence is in the figure." in _member(package, "word/document.xml")
