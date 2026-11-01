"""Tests for the package parts the Word reader carries through.

A ``.docx`` is a package of parts, and most of them are not text: a chart is
an XML part of its own, an embedded object is a binary part, a glossary
document holds the building blocks a template is made of.  The reader used to
record each of them in its omission register and throw the bytes away, so a
document whose evidence was a chart opened without that chart.

These tests pin the behaviour that replaced that: the part travels in
``WordDocument.parts`` with its bytes, the body reference that anchored it
travels on the run, and the writer puts both back into the package under the
same names and the same relationship ids.

The tests are written so that they **fail on the code that existed before the
parts were carried through**: each one asserts on the bytes that used to be
thrown away, not on the mere absence of an exception.
"""

from __future__ import annotations

import io
import zipfile
from typing import Final

from lmeditor.word.model import WordDocument, WordPart, WordRevisionKind, WordRun
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
)

# ============================================================================
# The parts the fixture carries
# ============================================================================

#: The bytes of the chart part.  A chart is XML, but it is XML the model
#: cannot read, so the bytes are what must survive.
CHART_XML: Final[bytes] = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
    b"<c:chart><c:plotArea><c:layout/></c:plotArea></c:chart></c:chartSpace>"
)

#: The bytes of the embedded object: the compound-file signature Word writes
#: at the head of an OLE object, followed by a payload.
OLE_OBJECT_BYTES: Final[bytes] = (
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"embedded-object-payload"
)

#: The bytes of the glossary document.
GLOSSARY_XML: Final[bytes] = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<w:glossaryDocument xmlns:w="http://schemas.openxmlformats.org/'
    b'wordprocessingml/2006/main"><w:docParts/></w:glossaryDocument>'
)

#: The bytes of a part of a kind the model does not know at all.
WEBEXTENSION_XML: Final[bytes] = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<wne:webextension xmlns:wne="http://schemas.microsoft.com/office/word/2006/'
    b'wordml"><wne:appId>00000000-0000-0000-0000-000000000000</wne:appId>'
    b"</wne:webextension>"
)

#: The bytes of the alternative-format chunk.
ALT_CHUNK_HTML: Final[bytes] = b"<html><body><p>Imported content</p></body></html>"

#: The markup of a tracked formatting change: the run properties the run
#: had *before* the change, as Word writes them.
_FORMAT_CHANGE: Final[str] = (
    '<w:rPrChange w:id="900" w:author="Reviewer" w:date="2026-01-02T03:04:05Z">'
    "<w:rPr><w:b/></w:rPr>"
    "</w:rPrChange>"
)

#: The content types of the fixture.  Every carried part is named by an
#: override, which is what a real package does: the extension of a part
#: says nothing about what the part holds.
_CONTENT_TYPES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
<Override PartName="/word/charts/chart1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>
<Override PartName="/word/embeddings/oleObject1.bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>
<Override PartName="/word/glossary/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.glossary+xml"/>
<Override PartName="/word/webextensions/webextension1.xml" ContentType="application/vnd.ms-word.webextension+xml"/>
<Override PartName="/word/afchunk1.html" ContentType="text/html"/>
</Types>"""

#: The relationships of the fixture.  The ids are the ones the body refers
#: to, and they are deliberately not the ids the package builder hands out.
_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId20" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/oleObject1.bin"/>
<Relationship Id="rId21" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="charts/chart1.xml"/>
<Relationship Id="rId22" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/glossaryDocument" Target="glossary/document.xml"/>
<Relationship Id="rId23" Type="http://schemas.microsoft.com/office/2011/relationships/webextension" Target="webextensions/webextension1.xml"/>
<Relationship Id="rId24" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/aFChunk" Target="afchunk1.html"/>
</Relationships>"""

#: A chart drawn in the body, the way Word draws one.
_CHART_DRAWING: Final[str] = (
    "<w:p><w:r><w:drawing>"
    '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
    'wordprocessingDrawing">'
    '<wp:extent cx="1905000" cy="1905000"/>'
    '<wp:docPr id="1" name="Chart 1"/>'
    '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
    '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'r:id="rId21"/>'
    "</a:graphicData></a:graphic></wp:inline>"
    "</w:drawing></w:r></w:p>"
)

#: An embedded object in the body, the way Word writes one.
_EMBEDDED_OBJECT: Final[str] = (
    "<w:p><w:r><w:object "
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office">'
    '<v:shape id="_x0000_i1025" type="#_x0000_t75" '
    'style="width:100pt;height:50pt" o:ole=""/>'
    '<o:OLEObject Type="Embed" ProgID="Package" ShapeID="_x0000_i1025" '
    'DrawAspect="Content" ObjectID="_1" r:id="rId20"/>'
    "</w:object></w:r></w:p>"
)

#: The prose around the carried parts.
_BEFORE: Final[str] = "Before the chart."
_AFTER: Final[str] = "After the chart."

#: The body of the fixture: a chart, an embedded object and the prose.
_BODY: Final[str] = (
    f"<w:p><w:r><w:t>{_BEFORE}</w:t></w:r></w:p>"
    + _CHART_DRAWING
    + _EMBEDDED_OBJECT
    + f"<w:p><w:r><w:t>{_AFTER}</w:t></w:r></w:p>"
)


# ============================================================================
# The packages
# ============================================================================


def _package_with_parts(
    body: str,
    *,
    rels: str = _RELS,
    content_types: str = _CONTENT_TYPES,
    parts: dict[str, bytes] | None = None,
) -> bytes:
    """Assemble a package with the given body, relationships and extra parts.

    The fixture module builds its packages out of text parts; a package that
    carries a chart or an embedded object carries binary parts as well, so
    the members are written from bytes here.
    """
    members: list[tuple[str, bytes]] = [
        ("[Content_Types].xml", content_types.encode("utf-8")),
        ("_rels/.rels", _ROOT_RELS.encode("utf-8")),
        ("word/document.xml", _DOCUMENT_SHELL.format(body=body).encode("utf-8")),
        ("word/_rels/document.xml.rels", rels.encode("utf-8")),
        ("word/styles.xml", _STYLES.encode("utf-8")),
        ("word/numbering.xml", _NUMBERING.encode("utf-8")),
        ("word/footnotes.xml", _FOOTNOTES.encode("utf-8")),
        ("docProps/core.xml", _CORE_PROPERTIES.encode("utf-8")),
    ]
    members.extend((name, payload) for name, payload in (parts or {}).items())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def _carried_package() -> bytes:
    """A package that carries a chart, an object, a glossary and a stranger."""
    return _package_with_parts(
        _BODY,
        parts={
            "word/charts/chart1.xml": CHART_XML,
            "word/embeddings/oleObject1.bin": OLE_OBJECT_BYTES,
            "word/glossary/document.xml": GLOSSARY_XML,
            "word/webextensions/webextension1.xml": WEBEXTENSION_XML,
        },
    )


def _missing_part_package() -> bytes:
    """A package whose embedded object points at a part it does not hold."""
    return _package_with_parts(_EMBEDDED_OBJECT)


def _alt_chunk_package() -> bytes:
    """A package that carries an alternative-format chunk."""
    body = (
        f"<w:p><w:r><w:t>{_BEFORE}</w:t></w:r></w:p>"
        '<w:altChunk r:id="rId24"/>'
        f"<w:p><w:r><w:t>{_AFTER}</w:t></w:r></w:p>"
    )
    return _package_with_parts(body, parts={"word/afchunk1.html": ALT_CHUNK_HTML})


def _plain_package() -> bytes:
    """A package that carries none of the constructs."""
    body = (
        f"<w:p><w:r><w:t>{_BEFORE}</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t>{_AFTER}</w:t></w:r></w:p>"
    )
    return _package_with_parts(body, content_types=_PLAIN_CONTENT_TYPES)


def _format_change_package() -> bytes:
    """A package whose run carries a tracked formatting change."""
    body = (
        f"<w:p><w:r><w:t>{_BEFORE}</w:t></w:r></w:p>"
        f"<w:p><w:r><w:rPr>{_FORMAT_CHANGE}</w:rPr>"
        f"<w:t>Reformatted</w:t></w:r></w:p>"
    )
    return _package_with_parts(body, content_types=_PLAIN_CONTENT_TYPES)


def _external_subdocument_package() -> bytes:
    """A package whose subdocument reference points outside the package."""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId40" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/subDocument" Target="https://example.org/other.docx" TargetMode="External"/>
</Relationships>"""
    body = '<w:p><w:r><w:t>Before</w:t></w:r></w:p><w:subDoc r:id="rId40"/>'
    return _package_with_parts(
        body, rels=rels, content_types=_PLAIN_CONTENT_TYPES
    )


#: The content types of a package that carries no part of its own.
_PLAIN_CONTENT_TYPES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
</Types>"""


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


def _run_with_part(document: WordDocument, part_name: str) -> WordRun | None:
    """Return the run that anchors a carried part, if there is one."""
    for block in document.blocks:
        for run in block.runs:
            if run.part_ref == part_name:
                return run
    return None


def _reader() -> WordReader:
    """A fresh reader, so the omission register is not shared."""
    return WordReader()


# ============================================================================
# The reader carries the parts
# ============================================================================


def test_a_chart_part_is_carried_with_its_bytes() -> None:
    """A chart the model cannot read is carried through, not dropped."""
    reader = _reader()
    document = reader.read(_carried_package())
    part = _part(document, "word/charts/chart1.xml")
    assert part.data == CHART_XML, "the bytes of the chart must survive the read"
    assert part.content_type == (
        "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
    )
    assert part.relationship_id == "rId21"
    assert reader.last_omissions == [], (
        "nothing was lost, so the register must be silent: "
        f"{reader.last_omissions}"
    )


def test_a_chart_is_anchored_on_the_run_that_draws_it() -> None:
    """The body reference that anchors a chart travels on the run."""
    document = _reader().read(_carried_package())
    run = _run_with_part(document, "word/charts/chart1.xml")
    assert run is not None, "the run that draws the chart must anchor it"


def test_an_embedded_object_is_carried_and_anchored() -> None:
    """An embedded object is carried, and the run that holds it anchors it."""
    reader = _reader()
    document = reader.read(_carried_package())
    part = _part(document, "word/embeddings/oleObject1.bin")
    assert part.data == OLE_OBJECT_BYTES
    assert part.relationship_id == "rId20"
    run = _run_with_part(document, "word/embeddings/oleObject1.bin")
    assert run is not None, "the run that holds the object must anchor it"
    assert reader.last_omissions == []


def test_a_glossary_part_is_carried_with_its_bytes() -> None:
    """The glossary document is carried with the bytes it holds."""
    document = _reader().read(_carried_package())
    part = _part(document, "word/glossary/document.xml")
    assert part.data == GLOSSARY_XML
    assert part.relationship_id == "rId22"


def test_an_unmodelled_part_is_carried_and_not_called_lost() -> None:
    """A part of a kind the model does not know is still carried."""
    reader = _reader()
    document = reader.read(_carried_package())
    part = _part(document, "word/webextensions/webextension1.xml")
    assert part.data == WEBEXTENSION_XML
    assert not any(
        "webextension" in omission for omission in reader.last_omissions
    ), reader.last_omissions


def test_an_alternative_format_chunk_is_carried_and_anchored() -> None:
    """An alternative-format chunk is carried, and the body anchors it."""
    reader = _reader()
    document = reader.read(_alt_chunk_package())
    part = _part(document, "word/afchunk1.html")
    assert part.data == ALT_CHUNK_HTML
    assert _run_with_part(document, "word/afchunk1.html") is not None
    assert reader.last_omissions == []


def test_the_prose_around_the_carried_parts_survives() -> None:
    """Carrying a part must not cost the document its text."""
    document = _reader().read(_carried_package())
    assert _BEFORE in document.plain_text()
    assert _AFTER in document.plain_text()


def test_a_part_that_is_genuinely_missing_is_still_reported() -> None:
    """A relationship that points at nothing is a loss, and is named."""
    reader = _reader()
    reader.read(_missing_part_package())
    assert reader.last_omissions, "a missing part must be reported"
    assert any(
        "missing" in omission for omission in reader.last_omissions
    ), reader.last_omissions


def test_a_subdocument_that_points_outside_the_package_is_reported() -> None:
    """An external subdocument has no bytes to carry, so it is a loss."""
    reader = _reader()
    reader.read(_external_subdocument_package())
    assert reader.last_omissions, "an external subdocument must be reported"
    assert any(
        "external" in omission for omission in reader.last_omissions
    ), reader.last_omissions


def test_a_formatting_change_is_carried_rather_than_dropped() -> None:
    """The properties a run had before the change are carried with it."""
    reader = _reader()
    document = reader.read(_format_change_package())
    runs = [run for block in document.blocks for run in block.runs]
    changed = [run for run in runs if run.text == "Reformatted"]
    assert changed, "the reformatted run must be read"
    run = changed[0]
    assert run.format_change_xml is not None, "the revision must be carried"
    assert "w:rPrChange" in run.format_change_xml
    assert "<w:b/>" in run.format_change_xml, (
        "the properties from before the change must survive"
    )
    assert run.revision is not None
    assert run.revision.kind is WordRevisionKind.FORMAT_CHANGE
    assert not any(
        "rPrChange" in omission for omission in reader.last_omissions
    ), reader.last_omissions


def test_a_formatting_change_survives_the_round_trip() -> None:
    """The revision is written back into the run it belongs to."""
    document = _reader().read(_format_change_package())
    writer = WordWriter()
    package = writer.write(document)
    assert writer.last_omissions == [], writer.last_omissions
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    assert "w:rPrChange" in body
    assert 'w:author="Reviewer"' in body


# ============================================================================
# The writer puts the parts back
# ============================================================================


def test_the_parts_survive_the_round_trip() -> None:
    """A document read and written keeps every part it carried.

    This is the test that matters most: the bytes, the content types and the
    relationships all have to come back, or the package Word receives is not
    the package the document was read from.
    """
    document = _reader().read(_carried_package())
    writer = WordWriter()
    package = writer.write(document)
    assert writer.last_omissions == [], writer.last_omissions

    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert archive.read("word/charts/chart1.xml") == CHART_XML
        assert archive.read("word/embeddings/oleObject1.bin") == OLE_OBJECT_BYTES
        assert archive.read("word/glossary/document.xml") == GLOSSARY_XML
        assert (
            archive.read("word/webextensions/webextension1.xml") == WEBEXTENSION_XML
        )
        content_types = archive.read("[Content_Types].xml").decode("utf-8")
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
        body = archive.read("word/document.xml").decode("utf-8")

    assert 'PartName="/word/charts/chart1.xml"' in content_types
    assert (
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.'
        'chart+xml"' in content_types
    )
    assert 'PartName="/word/embeddings/oleObject1.bin"' in content_types
    assert 'PartName="/word/glossary/document.xml"' in content_types
    assert 'PartName="/word/webextensions/webextension1.xml"' in content_types

    assert 'Id="rId20"' in rels and "embeddings/oleObject1.bin" in rels
    assert 'Id="rId21"' in rels and "charts/chart1.xml" in rels
    assert 'Id="rId22"' in rels and "glossary/document.xml" in rels

    assert 'r:id="rId20"' in body, "the embedded object must be anchored again"
    assert 'r:id="rId21"' in body, "the chart must be anchored again"

    reread = _reader()
    document_again = reread.read(package)
    assert _part(document_again, "word/charts/chart1.xml").data == CHART_XML
    assert _part(document_again, "word/embeddings/oleObject1.bin").data == OLE_OBJECT_BYTES
    assert _part(document_again, "word/glossary/document.xml").data == GLOSSARY_XML
    assert (
        _part(document_again, "word/webextensions/webextension1.xml").data
        == WEBEXTENSION_XML
    )
    assert reread.last_omissions == [], reread.last_omissions


def test_an_alternative_format_chunk_survives_the_round_trip() -> None:
    """The chunk is written back as the body reference Word expects."""
    document = _reader().read(_alt_chunk_package())
    writer = WordWriter()
    package = writer.write(document)
    assert writer.last_omissions == [], writer.last_omissions
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert archive.read("word/afchunk1.html") == ALT_CHUNK_HTML
        body = archive.read("word/document.xml").decode("utf-8")
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "w:altChunk" in body
    assert 'r:id="rId24"' in body
    assert 'Id="rId24"' in rels and "afchunk1.html" in rels


def test_a_document_without_these_constructs_is_unchanged() -> None:
    """A document that carries none of them reads and writes as it did.

    Nothing is carried, nothing is reported and nothing is added to the
    package: the change must not touch the documents that never needed it.
    """
    reader = _reader()
    document = reader.read(_plain_package())
    assert document.parts == ()
    assert reader.last_omissions == []
    assert document.plain_text() == f"{_BEFORE}\n\n{_AFTER}"

    writer = WordWriter()
    package = writer.write(document)
    assert writer.last_omissions == [], writer.last_omissions
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        body = archive.read("word/document.xml").decode("utf-8")
    assert "word/charts/chart1.xml" not in names
    assert "word/embeddings/oleObject1.bin" not in names
    assert _BEFORE in body and _AFTER in body


def test_a_part_that_cannot_be_written_is_reported_and_the_write_carries_on() -> None:
    """A part Word would refuse must not cost the caller the document.

    A package holds a part only when a relationship reaches it.  A part with
    no relationship at all -- not from the document, not from the package
    root, not from the part that owns it -- has no path into the package, and
    inventing one is what makes Word report the package as corrupt.  Such a
    part does not travel, and the register names it so the loss is loud.
    """
    document = WordDocument(
        parts=(
            WordPart(part_name="../escape.xml", data=b"<x/>"),
            WordPart(part_name="word/kept.xml", data=b"<kept/>"),
        )
    )
    writer = WordWriter()
    package = writer.write(document)
    assert writer.last_omissions, "the part that could not be written is named"
    assert any("escape.xml" in omission for omission in writer.last_omissions)
    assert any("kept.xml" in omission for omission in writer.last_omissions), (
        "the part nothing reaches is not named as lost"
    )
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert "word/kept.xml" not in archive.namelist()
        # The document itself is still written: one part the package refuses
        # must not cost the caller the whole package.
        assert "word/document.xml" in archive.namelist()


def test_a_carried_part_never_lands_twice_in_the_package() -> None:
    """A part whose name the package already holds is not written again.

    Two members of the same name make Word report the package as corrupt,
    and the package builder holds parts of its own -- a custom XML item, for
    instance -- that a document may carry a part of the same name for.  A
    relationship part is never content either: it is the relationship list of
    a sibling part.
    """
    document = WordDocument(
        parts=(
            WordPart(part_name="customXml/item1.xml", data=b"<item/>"),
            WordPart(
                part_name="customXml/_rels/item1.xml.rels",
                data=b"<Relationships/>",
            ),
            WordPart(part_name="word/kept.xml", data=b"<kept/>"),
        )
    )
    writer = WordWriter()
    package = writer.write(document)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
    assert len(names) == len(set(names)), f"the package holds a name twice: {names}"
    assert names.count("customXml/item1.xml") == 1, (
        f"the part the package already holds was written again: {names}"
    )
    assert writer.last_omissions, "the parts that could not be written are named"
