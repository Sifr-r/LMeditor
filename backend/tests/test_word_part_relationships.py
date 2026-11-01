"""Tests for the relationships a carried package part holds itself.

A chart is not one part: it is a part of its own plus the parts behind it --
a colour style, a quick style, the workbook the data lives in -- and it reaches
them through a relationship part of its own, ``word/charts/_rels/chart1.xml.rels``.
Those relationships are not part of the document's relationship list, so a reader
that only looks at ``word/_rels/document.xml.rels`` loses them.

Losing them is not the worst of it.  A relationship part is the relationship
*list* of a sibling part, never content, and a reader that carries one as
content hands the writer a part to point a relationship at.  Word answers such a
package with "the file appears to be corrupted" -- and a package Word silently
repairs is a package whose content the operator cannot trust.

These tests therefore pin three things: a relationship part is never carried as
content, a carried part keeps the relationships it holds itself, and the size a
drawing is drawn at travels with the run that anchors it.
"""

from __future__ import annotations

import io
import zipfile
from typing import Final

from lmeditor.word.model import WordDocument, WordPart, WordRun
from lmeditor.word.reader import WordReader

from tests.word_fixtures import (
    _CORE_PROPERTIES,
    _DOCUMENT_SHELL,
    _FIXED_TIMESTAMP,
    _FOOTNOTES,
    _NUMBERING,
    _ROOT_RELS,
    _STYLES,
)

# ============================================================================
# The fixture package
# ============================================================================

#: The content types of the fixture.  The parts behind the chart are named by
#: overrides, which is what a real package does: the extension of a part says
#: nothing about what the part holds.
_CONTENT_TYPES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
<Override PartName="/word/charts/chart1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>
<Override PartName="/word/embeddings/oleObject1.bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

#: The relationships of the package root.  The root reaches the document
#: itself and the parts a producer puts beside it; the custom document
#: properties are the ones only the root reaches.
_ROOT_RELS_WITH_CUSTOM: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" Target="docProps/custom.xml"/>
</Relationships>"""

#: The relationships of the package root when one of them points outside the
#: package.
_ROOT_RELS_WITH_EXTERNAL: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/package" TargetMode="External"/>
</Relationships>"""

#: The bytes of the custom document properties: a part only the package root
#: reaches.
CUSTOM_PROPERTIES_XML: Final[bytes] = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    b'custom-properties"><property name="Reviewer"/></Properties>'
)

#: The relationships of the document.  ``rId4`` is the one that reaches the
#: chart; the rest are the ones every document has.
_DOCUMENT_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="charts/chart1.xml"/>
</Relationships>"""

#: The relationships of the document of a package that also carries an
#: embedded object and a picture.
_VML_DOCUMENT_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/oleObject1.bin"/>
<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>"""

#: The relationships of the chart itself.  They are written out of order on
#: purpose: the reader sorts them, so that the same package always reads to
#: the same model.
_CHART_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/package" Target="../embeddings/Microsoft_Excel_Worksheet.xlsx"/>
<Relationship Id="rId2" Type="http://schemas.microsoft.com/office/2011/relationships/chartColorStyle" Target="colors1.xml"/>
<Relationship Id="rId1" Type="http://schemas.microsoft.com/office/2011/relationships/chartStyle" Target="style1.xml"/>
</Relationships>"""

#: The relationships of the chart when one of them points outside the package.
_EXTERNAL_CHART_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.microsoft.com/office/2011/relationships/chartStyle" Target="style1.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/data.csv" TargetMode="External"/>
</Relationships>"""

#: A relationship part that is not well-formed XML.
_BROKEN_CHART_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.microsoft.com/office/2011/relationships/chartStyle" Target="style1.xml"
"""

#: The bytes of the chart part.  A chart is XML the model cannot read, so the
#: bytes are what must survive the read.
CHART_XML: Final[bytes] = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
    b"<c:chart><c:plotArea><c:layout/></c:plotArea></c:chart></c:chartSpace>"
)

#: The bytes of the parts behind the chart: a colour style, a quick style and
#: the workbook the chart's data lives in.
COLORS_XML: Final[bytes] = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cs:colorStyle xmlns:cs="http://schemas.microsoft.com/office/drawing/2012/chartStyle"/>'
STYLE_XML: Final[bytes] = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cs:chartStyle xmlns:cs="http://schemas.microsoft.com/office/drawing/2012/chartStyle"/>'
WORKBOOK_BYTES: Final[bytes] = b"PK\x03\x04embedded-workbook-payload"

#: The bytes of an embedded object: the compound-file signature Word writes
#: at the head of an OLE object, followed by a payload.
OLE_OBJECT_BYTES: Final[bytes] = (
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"embedded-object-payload"
)

#: The bytes of a picture.  The reader does not decode the image, so the
#: bytes only have to be there.
IMAGE_BYTES: Final[bytes] = b"\x89PNG\r\n\x1a\nfake-image-payload"

#: The size Word wrote the chart at: 432 x 252 points, in EMU.
CHART_EXTENT: Final[tuple[int, int]] = (5486400, 3200400)

#: The size a VML shape declares: 100 x 50 points, in EMU.
VML_EXTENT: Final[tuple[int, int]] = (1270000, 635000)

#: A chart drawn in the body, the way Word draws one.
_CHART_DRAWING: Final[str] = (
    "<w:p><w:r><w:drawing>"
    '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
    'wordprocessingDrawing">'
    f'<wp:extent cx="{CHART_EXTENT[0]}" cy="{CHART_EXTENT[1]}"/>'
    '<wp:docPr id="1" name="Chart 1"/>'
    '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
    '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'r:id="rId4"/>'
    "</a:graphicData></a:graphic></wp:inline>"
    "</w:drawing></w:r></w:p>"
)

#: The same chart, drawn without an extent: the drawing states no size.
_CHART_DRAWING_WITHOUT_EXTENT: Final[str] = (
    "<w:p><w:r><w:drawing>"
    '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
    'wordprocessingDrawing">'
    '<wp:docPr id="1" name="Chart 1"/>'
    '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
    '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'r:id="rId4"/>'
    "</a:graphicData></a:graphic></wp:inline>"
    "</w:drawing></w:r></w:p>"
)

#: An embedded object, drawn by a VML shape that states its size.
_VML_OBJECT: Final[str] = (
    "<w:p><w:r><w:object "
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office">'
    '<v:shape id="_x0000_i1025" type="#_x0000_t75" '
    'style="width:100pt;height:50pt" o:ole=""/>'
    '<o:OLEObject Type="Embed" ProgID="Package" ShapeID="_x0000_i1025" '
    'DrawAspect="Content" ObjectID="_1" r:id="rId5"/>'
    "</w:object></w:r></w:p>"
)

#: A picture, drawn by a VML shape that states its size.
_VML_PICT: Final[str] = (
    "<w:p><w:r><w:pict>"
    '<v:shape xmlns:v="urn:schemas-microsoft-com:vml" '
    'id="_x0000_i1026" type="#_x0000_t75" style="width:100pt;height:50pt">'
    '<v:imagedata r:id="rId6"/>'
    "</v:shape></w:pict></w:r></w:p>"
)


# ============================================================================
# The packages
# ============================================================================


def _package(
    body: str,
    *,
    rels: str = _DOCUMENT_RELS,
    chart_rels: str = _CHART_RELS,
    root_rels: str | None = _ROOT_RELS,
    members: dict[str, bytes] | None = None,
) -> bytes:
    """Assemble a package with the given body, relationships and extra parts.

    The fixture module builds its packages out of text parts; a package that
    carries a chart carries a workbook and an image as well, so the members
    are written from bytes here.

    ``root_rels`` is the relationship part of the package root.  ``None``
    leaves it out of the package altogether, which is how a package without
    a root relationship part is built.
    """
    chart_members: list[tuple[str, bytes]] = [
        ("word/charts/chart1.xml", CHART_XML),
        ("word/charts/_rels/chart1.xml.rels", chart_rels.encode("utf-8")),
        ("word/charts/colors1.xml", COLORS_XML),
        ("word/charts/style1.xml", STYLE_XML),
        ("word/embeddings/Microsoft_Excel_Worksheet.xlsx", WORKBOOK_BYTES),
    ]
    members_to_write: list[tuple[str, bytes]] = [
        ("[Content_Types].xml", _CONTENT_TYPES.encode("utf-8")),
        ("word/document.xml", _DOCUMENT_SHELL.format(body=body).encode("utf-8")),
        ("word/_rels/document.xml.rels", rels.encode("utf-8")),
        ("word/styles.xml", _STYLES.encode("utf-8")),
        ("word/numbering.xml", _NUMBERING.encode("utf-8")),
        ("word/footnotes.xml", _FOOTNOTES.encode("utf-8")),
        ("docProps/core.xml", _CORE_PROPERTIES.encode("utf-8")),
    ]
    if root_rels is not None:
        members_to_write.insert(1, ("_rels/.rels", root_rels.encode("utf-8")))
    members_to_write.extend(chart_members)
    members_to_write.extend((name, payload) for name, payload in (members or {}).items())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members_to_write:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def _chart_package() -> bytes:
    """A package whose document anchors a chart that owns parts of its own."""
    return _package(_CHART_DRAWING)


def _external_relationship_package() -> bytes:
    """A package whose chart holds a relationship pointing outside it."""
    return _package(_CHART_DRAWING, chart_rels=_EXTERNAL_CHART_RELS)


def _broken_relationship_package() -> bytes:
    """A package whose chart holds a relationship part that cannot be parsed."""
    return _package(_CHART_DRAWING, chart_rels=_BROKEN_CHART_RELS)


def _drawing_without_extent_package() -> bytes:
    """A package whose chart is drawn without an extent."""
    return _package(_CHART_DRAWING_WITHOUT_EXTENT)


def _vml_package() -> bytes:
    """A package whose object and picture are drawn by VML shapes."""
    return _package(
        _VML_OBJECT + _VML_PICT,
        rels=_VML_DOCUMENT_RELS,
        members={
            "word/embeddings/oleObject1.bin": OLE_OBJECT_BYTES,
            "word/media/image1.png": IMAGE_BYTES,
        },
    )


def _package_root_package() -> bytes:
    """A package whose root reaches a part the document does not."""
    return _package(
        _CHART_DRAWING,
        root_rels=_ROOT_RELS_WITH_CUSTOM,
        members={"docProps/custom.xml": CUSTOM_PROPERTIES_XML},
    )


def _package_without_root_rels() -> bytes:
    """A package that carries no relationship part of its own root."""
    return _package(_CHART_DRAWING, root_rels=None)


def _package_with_external_root_rels() -> bytes:
    """A package whose root holds a relationship pointing outside it."""
    return _package(_CHART_DRAWING, root_rels=_ROOT_RELS_WITH_EXTERNAL)


# ============================================================================
# Helpers
# ============================================================================


def _part(document: WordDocument, part_name: str) -> WordPart:
    """Return the carried part of a document, failing when it is not there."""
    for part in document.parts:
        if part.part_name == part_name:
            return part
    raise AssertionError(
        f"the package part {part_name!r} was not carried; "
        f"the document carries {[p.part_name for p in document.parts]}"
    )


def _run_with_part(document: WordDocument, part_name: str) -> WordRun:
    """Return the run that anchors a carried part, failing when there is none."""
    for block in document.blocks:
        for run in block.runs:
            if run.part_ref == part_name:
                return run
    raise AssertionError(f"no run anchors the package part {part_name!r}")


def _run_with_media(document: WordDocument, part_name: str) -> WordRun:
    """Return the run that embeds a media part, failing when there is none."""
    for block in document.blocks:
        for run in block.runs:
            if run.image_ref == part_name:
                return run
    raise AssertionError(f"no run embeds the media part {part_name!r}")


def _reader() -> WordReader:
    """A fresh reader, so the omission register is not shared."""
    return WordReader()


# ============================================================================
# A relationship part is never content
# ============================================================================


def test_no_relationship_part_is_carried_as_content() -> None:
    """A ``.rels`` file is the relationship list of a sibling, never content.

    Carrying one as content is what made the writer point a relationship at a
    relationship file, and Word reject the package as corrupt.
    """
    document = _reader().read(_chart_package())
    carried = [part.part_name for part in document.parts]
    assert not [name for name in carried if "/_rels/" in name], (
        f"a relationship part was carried as content: {carried}"
    )
    assert "word/charts/_rels/chart1.xml.rels" not in carried


def test_the_parts_behind_the_chart_are_still_carried() -> None:
    """The parts the chart owns travel with their bytes.

    A chart whose workbook is missing is a chart Word cannot edit, and a chart
    whose colour style is missing is a chart Word re-styles on open.
    """
    document = _reader().read(_chart_package())
    assert _part(document, "word/charts/colors1.xml").data == COLORS_XML
    assert _part(document, "word/charts/style1.xml").data == STYLE_XML
    assert (
        _part(document, "word/embeddings/Microsoft_Excel_Worksheet.xlsx").data
        == WORKBOOK_BYTES
    )


def test_a_part_the_document_does_not_reach_has_no_relationship_id() -> None:
    """A part reached from another part's relationship has no document id.

    The document does not reach the chart's own parts, so the writer must not
    invent a document relationship for them: they are reached from the
    relationship list of the part that owns them.
    """
    document = _reader().read(_chart_package())
    for part_name in (
        "word/charts/colors1.xml",
        "word/charts/style1.xml",
        "word/embeddings/Microsoft_Excel_Worksheet.xlsx",
    ):
        part = _part(document, part_name)
        assert part.relationship_id == "", (
            f"{part_name} must not claim a document relationship"
        )
        assert part.relationship_type == ""


# ============================================================================
# A carried part keeps its own relationships
# ============================================================================


def test_the_chart_carries_the_relationship_the_document_reached_it_by() -> None:
    """The chart keeps the relationship id and type the document used."""
    document = _reader().read(_chart_package())
    chart = _part(document, "word/charts/chart1.xml")
    assert chart.relationship_id == "rId4"
    assert chart.relationship_type == (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
    )


def test_the_chart_keeps_the_relationships_it_holds_itself() -> None:
    """The chart's own relationships travel with the chart.

    Without them the colour style, the quick style and the workbook are
    unreachable, and the writer cannot put the package back together.
    """
    document = _reader().read(_chart_package())
    chart = _part(document, "word/charts/chart1.xml")
    targets = {rel.relationship_id: rel.target for rel in chart.relationships}
    assert targets == {
        "rId1": "word/charts/style1.xml",
        "rId2": "word/charts/colors1.xml",
        "rId3": "word/embeddings/Microsoft_Excel_Worksheet.xlsx",
    }


def test_a_relationship_target_is_resolved_against_the_part_that_holds_it() -> None:
    """A target is relative to the owning part's own directory.

    The chart's ``colors1.xml`` is a sibling of the chart, not a child of
    ``word/``, and the workbook climbs out of ``word/charts/`` to reach
    ``word/embeddings/``.
    """
    document = _reader().read(_chart_package())
    chart = _part(document, "word/charts/chart1.xml")
    resolved = {rel.relationship_id: rel.target for rel in chart.relationships}
    assert resolved["rId2"] == "word/charts/colors1.xml"
    assert resolved["rId3"] == "word/embeddings/Microsoft_Excel_Worksheet.xlsx"


def test_a_relationship_the_chart_holds_is_not_external() -> None:
    """A relationship that names a part of the package is not external."""
    document = _reader().read(_chart_package())
    chart = _part(document, "word/charts/chart1.xml")
    assert len(chart.relationships) == 3
    assert all(rel.is_external is False for rel in chart.relationships)


def test_the_relationships_of_a_part_are_read_in_a_stable_order() -> None:
    """The same package always reads to the same model.

    The relationships are sorted by their id, so the order the package
    happens to write them in cannot make the model differ.
    """
    document = _reader().read(_chart_package())
    chart = _part(document, "word/charts/chart1.xml")
    assert [rel.relationship_id for rel in chart.relationships] == [
        "rId1",
        "rId2",
        "rId3",
    ]


def test_a_relationship_that_points_outside_the_package_keeps_its_url() -> None:
    """An external target is the URL as written, not a package part name."""
    document = _reader().read(_external_relationship_package())
    chart = _part(document, "word/charts/chart1.xml")
    external = [rel for rel in chart.relationships if rel.is_external]
    assert len(external) == 1
    assert external[0].target == "https://example.org/data.csv"
    assert external[0].relationship_id == "rId4"


def test_nothing_is_lost_when_a_chart_is_read() -> None:
    """A package that carries a chart is read without a single omission."""
    reader = _reader()
    reader.read(_chart_package())
    assert reader.last_omissions == [], (
        "nothing was lost, so the register must be silent: "
        f"{reader.last_omissions}"
    )


def test_a_relationship_part_that_cannot_be_parsed_is_reported() -> None:
    """A relationship part the reader cannot read is named in the register.

    The part itself still travels -- it is the relationships it holds that
    are lost -- and the register says so rather than passing over it in
    silence.
    """
    reader = _reader()
    document = reader.read(_broken_relationship_package())
    assert _part(document, "word/charts/chart1.xml").data == CHART_XML, (
        "the chart must travel even when its relationship part cannot be read"
    )
    assert any(
        "relationship part" in omission for omission in reader.last_omissions
    ), reader.last_omissions


# ============================================================================
# The relationships of the package root
# ============================================================================


def test_a_part_the_package_root_reaches_keeps_that_relationship() -> None:
    """The custom document properties travel with the relationship that reaches them.

    The document does not reach them: the package root does.  Without that
    relationship the part has no path into the package at all, and the
    writer -- which does not fabricate relationships -- drops it.
    """
    document = _reader().read(_package_root_package())
    part = _part(document, "docProps/custom.xml")
    relationship = part.package_relationship
    assert relationship is not None, (
        "a part the package root reaches must keep the relationship that reaches it"
    )
    assert relationship.relationship_id == "rId4"
    assert relationship.relationship_type == (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
        "custom-properties"
    )
    assert relationship.target == "docProps/custom.xml"
    assert relationship.is_external is False


def test_the_document_relationships_of_a_part_the_root_reaches_are_untouched() -> None:
    """The package root and the document are two namespaces of relationship ids.

    A part the document does not reach has no document relationship, and one
    that both reach keeps the document's own.
    """
    document = _reader().read(_package_root_package())
    part = _part(document, "docProps/custom.xml")
    assert part.relationship_id == ""
    assert part.relationship_type == ""


def test_a_part_reached_by_both_keeps_both_relationships() -> None:
    """A part the document and the package root both reach keeps both.

    The document relationship says how the body points at the part; the
    package-root one says how the part gets into the package at all.
    """
    document = _reader().read(_package_root_package())
    chart = _part(document, "word/charts/chart1.xml")
    assert chart.relationship_id == "rId4"
    assert chart.package_relationship is None, (
        "the package root does not reach the chart, so it has no root relationship"
    )


def test_the_document_is_not_a_carried_part() -> None:
    """The package root reaches the document, and the document is not carried.

    The document is read into the model, not carried through, so the root
    relationship that reaches it has no part to travel on -- and nothing is
    reported.
    """
    reader = _reader()
    document = reader.read(_package_root_package())
    carried = [part.part_name for part in document.parts]
    assert "word/document.xml" not in carried
    assert reader.last_omissions == [], reader.last_omissions


def test_a_package_without_root_relationships_has_no_package_relationship() -> None:
    """A package that carries no root relationship part has no root relationships.

    Nothing is lost and nothing is reported: a package root that reaches
    nothing is an ordinary package.
    """
    reader = _reader()
    document = reader.read(_package_without_root_rels())
    assert document.parts, "the package must still carry its parts"
    assert all(part.package_relationship is None for part in document.parts)
    assert reader.last_omissions == [], reader.last_omissions


def test_an_external_package_root_relationship_keeps_its_url() -> None:
    """An external root target is the URL as written, not a package part name.

    It reaches no part of the package, so it has no place on a part; it is
    kept as the reader read it rather than being dropped.
    """
    reader = _reader()
    reader.read(_package_with_external_root_rels())
    external = [
        relationship
        for relationship in reader.last_package_relationships
        if relationship.is_external
    ]
    assert len(external) == 1
    assert external[0].target == "https://example.org/package"
    assert external[0].relationship_id == "rId5"


def test_the_root_relationships_are_read_in_a_stable_order() -> None:
    """The same package always reads to the same model."""
    reader = _reader()
    reader.read(_package_root_package())
    assert [
        relationship.relationship_id for relationship in reader.last_package_relationships
    ] == ["rId1", "rId4"]


def test_the_package_root_relationship_part_is_not_carried() -> None:
    """``_rels/.rels`` is a relationship part like any other, never content."""
    document = _reader().read(_package_root_package())
    carried = [part.part_name for part in document.parts]
    assert "_rels/.rels" not in carried
    assert not [name for name in carried if "/_rels/" in name], carried


# ============================================================================
# The size a drawing is drawn at
# ============================================================================


def test_a_chart_keeps_the_size_it_was_drawn_at() -> None:
    """The extent of the drawing travels on the run that anchors the chart.

    Word scales a chart by rewriting the extent of the drawing that holds it.
    A reader that does not keep the extent hands the chart back at a default
    size, and the operator sees a chart that no longer says what it said.
    """
    document = _reader().read(_chart_package())
    run = _run_with_part(document, "word/charts/chart1.xml")
    assert run.anchor_extent == CHART_EXTENT


def test_a_vml_shape_keeps_the_size_it_was_drawn_at() -> None:
    """A VML shape states its size in points, and the model keeps EMU."""
    document = _reader().read(_vml_package())
    run = _run_with_part(document, "word/embeddings/oleObject1.bin")
    assert run.anchor_extent == VML_EXTENT


def test_a_picture_keeps_the_size_it_was_drawn_at() -> None:
    """The size of a drawing travels on the run that carries the picture too."""
    document = _reader().read(_vml_package())
    run = _run_with_media(document, "word/media/image1.png")
    assert run.anchor_extent == VML_EXTENT


def test_a_drawing_without_an_extent_has_no_size() -> None:
    """A drawing that states no size is no size, not a guessed one."""
    document = _reader().read(_drawing_without_extent_package())
    run = _run_with_part(document, "word/charts/chart1.xml")
    assert run.anchor_extent is None
