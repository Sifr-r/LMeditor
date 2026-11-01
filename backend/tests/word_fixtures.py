"""Word-365-shaped OOXML fixtures for the LMeditor word-processing tests.

The point of this module is to give the reader and the writer a document that
looks like the ones Word 365 actually produces, rather than a document produced
by the same library that reads it. A test that round-trips ``python-docx``'s own
output proves only that ``python-docx`` is self-consistent.

So these fixtures are assembled as raw OPC packages with the part layout Word
itself writes: ``word/document.xml`` plus ``styles.xml``, ``numbering.xml``,
``footnotes.xml``, a header and a footer, a hyperlink relationship, and
``docProps/core.xml``. The document body deliberately exercises the structures
that the old reader dropped on the floor:

* a content control (``w:sdt``) wrapping a paragraph
* a tracked insertion (``w:ins``) and a tracked deletion (``w:del``)
* a text box (``w:txbxContent``)
* a field code (``w:fldSimple``) that must contribute no text
* a page break (``w:br w:type="page"``)
* a table with a repeating header row
* nested bullet levels and an ordered list
* an external hyperlink and a footnote reference

Every expectation in the test suite is written against the *literal* strings in
this file, so the fixture and the assertions cannot drift apart silently.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from typing import Final

#: Every part is written with this timestamp so the fixture bytes are stable.
_FIXED_TIMESTAMP: Final[tuple[int, int, int, int, int, int]] = (1980, 1, 1, 0, 0, 0)

#: Text the fixture asserts on, by name.
FIXTURE_TITLE: Final[str] = "Word 365 Reference Document"
FIXTURE_HEADING_1: Final[str] = "Grounding Section"
FIXTURE_HEADING_2: Final[str] = "Nested Subsection"
FIXTURE_PARAGRAPH: Final[str] = (
    "A grounded claim with emphasis and a citation marker."
)
FIXTURE_BULLET: Final[str] = "First bullet"
FIXTURE_NESTED_BULLET: Final[str] = "Nested bullet"
FIXTURE_NUMBERED_1: Final[str] = "Numbered one"
FIXTURE_NUMBERED_2: Final[str] = "Numbered two"
FIXTURE_LINK_TEXT: Final[str] = "External reference"
FIXTURE_LINK_TARGET: Final[str] = "https://example.org/source"
FIXTURE_SDT_PARAGRAPH: Final[str] = "Content control paragraph"
FIXTURE_INSERTED_TEXT: Final[str] = "Inserted by review"
FIXTURE_DELETED_TEXT: Final[str] = "Deleted by review"
FIXTURE_TEXTBOX_TEXT: Final[str] = "Text box content"
FIXTURE_FOOTNOTE_TEXT: Final[str] = "the cited passage"
FIXTURE_FINAL_PARAGRAPH: Final[str] = "Final paragraph."
FIXTURE_HEADER_TEXT: Final[str] = "LMeditor Word 365 Fixture"
FIXTURE_FOOTER_TEXT: Final[str] = "Page footer"
FIXTURE_TABLE_HEADER: Final[str] = "Evidence | Verdict"
FIXTURE_TABLE_ROW: Final[str] = "chunk-abc | entailed"

#: The exact text the reader is expected to recover, in order.
EXPECTED_PLAIN_TEXT: Final[str] = "\n\n".join(
    [
        FIXTURE_TITLE,
        FIXTURE_HEADING_1,
        FIXTURE_PARAGRAPH,
        FIXTURE_HEADING_2,
        FIXTURE_BULLET,
        FIXTURE_NESTED_BULLET,
        FIXTURE_NUMBERED_1,
        FIXTURE_NUMBERED_2,
        FIXTURE_LINK_TEXT,
        FIXTURE_TABLE_HEADER + "\n" + FIXTURE_TABLE_ROW,
        FIXTURE_SDT_PARAGRAPH,
        FIXTURE_INSERTED_TEXT,
        FIXTURE_TEXTBOX_TEXT,
        FIXTURE_FINAL_PARAGRAPH,
    ]
)

_CONTENT_TYPES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_ROOT_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_DOCUMENT_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/source" TargetMode="External"/>
</Relationships>"""

_STYLES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr/></w:rPrDefault><w:pPrDefault><w:pPr/></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/></w:style>
</w:styles>"""

_NUMBERING: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0">
<w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="◦"/></w:lvl>
<w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="▪"/></w:lvl>
</w:abstractNum>
<w:abstractNum w:abstractNumId="1">
<w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2."/></w:lvl>
<w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="lowerRoman"/><w:lvlText w:val="%3."/></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>"""

_FOOTNOTES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
<w:footnote w:type="continuationSeparator" w:id="1"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
<w:footnote w:id="2"><w:p><w:r><w:t>the cited passage</w:t></w:r></w:p></w:footnote>
</w:footnotes>"""

_HEADER: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:r><w:t>LMeditor Word 365 Fixture</w:t></w:r></w:p>
</w:hdr>"""

_FOOTER: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:r><w:t>Page footer</w:t></w:r></w:p>
</w:ftr>"""

_CORE_PROPERTIES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>Word 365 Reference Document</dc:title>
<dc:subject>Fixture subject</dc:subject>
<dc:creator>LMeditor Fixtures</dc:creator>
<cp:keywords>fixture,word,365</cp:keywords>
<dc:description>Fixture comments</dc:description>
<cp:revision>3</cp:revision>
<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-02T03:04:05Z</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">2026-01-02T03:04:05Z</dcterms:modified>
</cp:coreProperties>"""


def _document_xml() -> str:
    """Assemble the fixture body in document order."""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:v="urn:schemas-microsoft-com:vml">
<w:body>
<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>{FIXTURE_TITLE}</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{FIXTURE_HEADING_1}</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">A grounded claim </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>with emphasis</w:t></w:r><w:r><w:t xml:space="preserve"> and a citation marker.</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>{FIXTURE_HEADING_2}</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>{FIXTURE_BULLET}</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>{FIXTURE_NESTED_BULLET}</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>{FIXTURE_NUMBERED_1}</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>{FIXTURE_NUMBERED_2}</w:t></w:r></w:p>
<w:p><w:hyperlink r:id="rId6"><w:r><w:t>{FIXTURE_LINK_TEXT}</w:t></w:r></w:hyperlink></w:p>
<w:p><w:fldSimple w:instr=" TOC \\o &quot;1-3&quot; "><w:r><w:t>Cached table of contents</w:t></w:r></w:fldSimple></w:p>
<w:tbl>
<w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="4675"/><w:gridCol w:w="4675"/></w:tblGrid>
<w:tr><w:trPr><w:tblHeader/></w:trPr>
<w:tc><w:tcPr><w:tcW w:w="4675" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>Evidence</w:t></w:r></w:p></w:tc>
<w:tc><w:tcPr><w:tcW w:w="4675" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>Verdict</w:t></w:r></w:p></w:tc>
</w:tr>
<w:tr>
<w:tc><w:tcPr><w:tcW w:w="4675" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>chunk-abc</w:t></w:r></w:p></w:tc>
<w:tc><w:tcPr><w:tcW w:w="4675" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>entailed</w:t></w:r></w:p></w:tc>
</w:tr>
</w:tbl>
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
<w:sdt><w:sdtPr><w:alias w:val="Fixture control"/></w:sdtPr><w:sdtContent>
<w:p><w:r><w:t>{FIXTURE_SDT_PARAGRAPH}</w:t></w:r></w:p>
</w:sdtContent></w:sdt>
<w:p><w:ins w:id="101" w:author="Reviewer" w:date="2026-01-02T03:04:05Z"><w:r><w:t>{FIXTURE_INSERTED_TEXT}</w:t></w:r></w:ins><w:del w:id="102" w:author="Reviewer" w:date="2026-01-02T03:04:05Z"><w:r><w:delText>{FIXTURE_DELETED_TEXT}</w:delText></w:r></w:del></w:p>
<w:p><w:r><w:pict><v:shape type="#_x0000_t202" style="width:200pt;height:50pt"><v:textbox><w:txbxContent><w:p><w:r><w:t>{FIXTURE_TEXTBOX_TEXT}</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
<w:p><w:r><w:footnoteReference w:id="2"/></w:r></w:p>
<w:p><w:r><w:t>{FIXTURE_FINAL_PARAGRAPH}</w:t></w:r></w:p>
<w:sectPr><w:headerReference w:type="default" r:id="rId4"/><w:footerReference w:type="default" r:id="rId5"/></w:sectPr>
</w:body>
</w:document>"""


def build_word365_fixture() -> bytes:
    """Return the bytes of the Word-365-shaped reference document.

    The package is written with a fixed member order and fixed member
    timestamps, so the fixture is byte-stable and can be compared directly.
    """
    parts: list[tuple[str, str]] = [
        ("[Content_Types].xml", _CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _document_xml()),
        ("word/_rels/document.xml.rels", _DOCUMENT_RELS),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("word/footnotes.xml", _FOOTNOTES),
        ("word/header1.xml", _HEADER),
        ("word/footer1.xml", _FOOTER),
        ("docProps/core.xml", _CORE_PROPERTIES),
    ]
    return _zip(parts)


def _zip(parts: list[tuple[str, str]]) -> bytes:
    """Zip the given parts with a fixed member order and fixed timestamps."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload.encode("utf-8"))
    return buffer.getvalue()


def package_members(package: bytes) -> list[tuple[str, tuple[object, ...], bytes]]:
    """Return a package's members: their names, their metadata and their contents.

    The *compressed* bytes are deliberately not part of this. A DEFLATE
    stream is the output of the compressor that produced it, and two zlib
    implementations -- the stock zlib and the zlib-ng a Linux runner links
    against -- do not produce the same stream for the same input. What a
    package holds is its members, their order, their metadata and their
    contents; those are the same on every platform, and they are what two
    packages are compared on.

    The parity harness already refuses to compare two written packages byte
    for byte for the same reason, and for one more: two different writers
    lay the same document out differently. It reads each package back and
    compares the documents.
    """
    with zipfile.ZipFile(BytesIO(package)) as archive:
        return [
            (
                info.filename,
                (info.date_time, info.compress_type, info.external_attr),
                archive.read(info.filename),
            )
            for info in archive.infolist()
        ]


# ============================================================================
# Targeted packages for the contract clauses the main fixture does not reach
# ============================================================================

_DOCUMENT_SHELL = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:document '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    "<w:body>{body}"
    "<w:sectPr/></w:body></w:document>"
)


def _package(
    body: str,
    *,
    styles: str = _STYLES,
    numbering: str = _NUMBERING,
    rels: str = _DOCUMENT_RELS,
    footnotes: str = _FOOTNOTES,
    core: str = _CORE_PROPERTIES,
    extra: dict[str, str] | None = None,
    document_xml: str | None = None,
) -> bytes:
    """Assemble a package around the given body XML.

    ``document_xml`` replaces ``word/document.xml`` outright, which is how the
    malformed-XML cases are built.
    """
    parts: list[tuple[str, str]] = [
        ("[Content_Types].xml", _CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        (
            "word/document.xml",
            document_xml if document_xml is not None else _DOCUMENT_SHELL.format(body=body),
        ),
        ("word/_rels/document.xml.rels", rels),
        ("word/styles.xml", styles),
        ("word/numbering.xml", numbering),
        ("word/footnotes.xml", footnotes),
        ("docProps/core.xml", core),
    ]
    for name, payload in (extra or {}).items():
        parts.append((name, payload))
    return _zip(parts)


def build_paragraph_package(text: str) -> bytes:
    """Return a package whose body is one paragraph carrying ``text``.

    The text is written into the document part as it is, so a caller can hand
    it a character the reader has to deal with -- the next-line control, say --
    and watch what comes out the other side.
    """
    body = f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    return _package(body)


def build_merged_table_package() -> bytes:
    """A table with a two-row merge, a three-row merge and a column span.

    Also carries a cell with two paragraphs, which must join with a single
    space, and a cell whose own text must survive the merge.
    """
    body = """
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr>
<w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Two row origin</w:t></w:r></w:p></w:tc>
<w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>Spans two columns</w:t></w:r></w:p></w:tc>
</w:tr>
<w:tr>
<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
<w:tc><w:p><w:r><w:t>Row two column two</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>Row two column three</w:t></w:r></w:p></w:tc>
</w:tr>
</w:tbl>
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr>
<w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Three row origin</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>Middle</w:t></w:r></w:p></w:tc>
</w:tr>
<w:tr>
<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
<w:tc><w:p><w:r><w:t>Middle row right</w:t></w:r></w:p></w:tc>
</w:tr>
<w:tr>
<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
<w:tc><w:p><w:r><w:t>Last row right</w:t></w:r></w:p></w:tc>
</w:tr>
</w:tbl>
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr>
<w:tc><w:p><w:r><w:t>First paragraph of the cell</w:t></w:r></w:p><w:p><w:r><w:t>Second paragraph of the cell</w:t></w:r></w:p></w:tc>
</w:tr>
</w:tbl>
"""
    return _package(body)


def build_link_target_package() -> bytes:
    """An internal anchor, a relative external target and an absolute one."""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rIdRel" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="docs/page.html" TargetMode="External"/>
<Relationship Id="rIdAbs" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/absolute" TargetMode="External"/>
</Relationships>"""
    body = """
<w:p><w:hyperlink w:anchor="section-two"><w:r><w:t>Internal anchor</w:t></w:r></w:hyperlink></w:p>
<w:p><w:hyperlink r:id="rIdRel"><w:r><w:t>Relative target</w:t></w:r></w:hyperlink></w:p>
<w:p><w:hyperlink r:id="rIdAbs"><w:r><w:t>Absolute target</w:t></w:r></w:hyperlink></w:p>
"""
    return _package(body, rels=rels)


def build_style_chain_package() -> bytes:
    """A heading style that inherits its name through a ``w:basedOn`` chain."""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="DerivedHeading"><w:basedOn w:val="Heading1"/></w:style>
</w:styles>"""
    body = (
        '<w:p><w:pPr><w:pStyle w:val="DerivedHeading"/></w:pPr>'
        "<w:r><w:t>Inherited heading</w:t></w:r></w:p>"
    )
    return _package(body, styles=styles)


def build_style_name_and_id_package() -> bytes:
    """Two headings, one recognised by its style id, one by its style name.

    R2 says a style whose *id or name* matches ``Heading N`` becomes a
    heading.  In the main fixture the id and the name both match, so
    neither branch is exercised on its own; here one heading matches only
    by id and the other only by name (and its name is lower case, which is
    how Word writes them).
    """
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="Custom Style"/></w:style>
<w:style w:type="paragraph" w:styleId="MyOwnStyle"><w:name w:val="heading 4"/></w:style>
</w:styles>"""
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading3"/></w:pPr>'
        "<w:r><w:t>Heading by style id</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:pStyle w:val="MyOwnStyle"/></w:pPr>'
        "<w:r><w:t>Heading by style name</w:t></w:r></w:p>"
    )
    return _package(body, styles=styles)


def build_style_numbering_package() -> bytes:
    """A paragraph that inherits its list numbering from its style."""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ListBulletStyle"><w:name w:val="List Bullet"/><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr></w:style>
</w:styles>"""
    body = (
        '<w:p><w:pPr><w:pStyle w:val="ListBulletStyle"/></w:pPr>'
        "<w:r><w:t>Style numbered item</w:t></w:r></w:p>"
    )
    return _package(body, styles=styles)


def build_breaks_and_fields_package() -> bytes:
    """Soft breaks, tabs, carriage returns and a field code."""
    body = """
<w:p><w:r><w:t>Before</w:t><w:br/><w:t>After</w:t></w:r></w:p>
<w:p><w:r><w:t>Tabbed</w:t><w:tab/><w:t>Columns</w:t></w:r></w:p>
<w:p><w:r><w:t>Carriage</w:t><w:cr/><w:t>Return</w:t></w:r></w:p>
<w:p><w:r><w:instrText> PAGE \\* MERGEFORMAT </w:instrText></w:r><w:r><w:t>Visible</w:t></w:r></w:p>
"""
    return _package(body)


def build_multi_header_footer_package() -> bytes:
    """Three header parts and two footer parts, to pin the part ordering."""

    def header(text: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:hdr>"
        )

    def footer(text: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:ftr>"
        )

    extra = {
        "word/header1.xml": header("Header one"),
        "word/header2.xml": header("Header two"),
        "word/header3.xml": header("Header three"),
        "word/footer1.xml": footer("Footer one"),
        "word/footer2.xml": footer("Footer two"),
    }
    return _package("<w:p/>", extra=extra)


def build_malformed_document_package() -> bytes:
    """A package whose ``word/document.xml`` is not well-formed XML."""
    return _package("", document_xml="<w:document><w:body><w:p></w:body></w:document>")


def build_malformed_styles_package() -> bytes:
    """A package whose ``word/styles.xml`` is not well-formed XML."""
    return _package("<w:p/>", styles="<w:styles><w:style></w:styles>")


def build_precise_core_properties_package() -> bytes:
    """A package whose core properties carry exact, known timestamps."""
    core = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>Precise Title</dc:title>
<dc:creator>Precise Author</dc:creator>
<dc:subject>Precise Subject</dc:subject>
<cp:keywords>one two</cp:keywords>
<dc:description>Precise description</dc:description>
<cp:revision>11</cp:revision>
<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-02T03:04:05Z</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">2026-06-07T08:09:10Z</dcterms:modified>
</cp:coreProperties>"""
    return _package("<w:p/>", core=core)


def build_wide_merge_package() -> bytes:
    """Merges that span more than one grid column.

    The first table is the canonical Word 2x2 merged block: one cell with
    ``w:gridSpan="2"`` and ``w:vMerge w:val="restart"`` above one cell with
    ``w:gridSpan="2"`` and a bare ``w:vMerge``.

    The second table is the ragged variant: the origin spans two columns, and
    the row below holds a continuing cell in the first covered column and a
    plain cell in the second. The plain cell sits inside the merged region, so
    Word would not display its text.
    """
    body = """
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr>
<w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Wide origin</w:t></w:r></w:p></w:tc>
</w:tr>
<w:tr>
<w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc>
</w:tr>
</w:tbl>
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr>
<w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Ragged origin</w:t></w:r></w:p></w:tc>
</w:tr>
<w:tr>
<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
<w:tc><w:p><w:r><w:t>Covered by the merge</w:t></w:r></w:p></w:tc>
</w:tr>
</w:tbl>
"""
    return _package(body)


def build_merge_region_package() -> bytes:
    """A merge that covers two of the table's three columns.

    The origin spans two columns and starts the merge; the row below holds a
    bare ``w:vMerge`` that continues it and a plain cell that has the third
    column of its own. The plain cell is outside the merged region, so its
    text belongs to the table and must survive a round trip.
    """
    body = """
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr>
<w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>MERGED-ORIGIN</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>Third column</w:t></w:r></w:p></w:tc>
</w:tr>
<w:tr>
<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
<w:tc><w:p><w:r><w:t>MERGE-REGION-PAYLOAD</w:t></w:r></w:p></w:tc>
</w:tr>
</w:tbl>
"""
    return _package(body)


def build_blank_row_table_package() -> bytes:
    """A one-column table whose middle row is blank, for the row-count rule."""
    body = """
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr><w:tc><w:p><w:r><w:t>alpha</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p/></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>omega</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
"""
    return _package(body)


#: The prose around the figure in :func:`build_image_document_package`.
FIXTURE_BEFORE_FIGURE: Final[str] = "Before the figure."
FIXTURE_AFTER_FIGURE: Final[str] = "After the figure."


def build_image_document_package() -> bytes:
    """A document with an inline picture between two paragraphs.

    The model has no representation for an image, so the picture contributes
    no text — but it must be *recorded*, not silently dropped, and the prose
    around it must survive untouched.
    """
    return _image_package(1)


def build_two_image_document_package() -> bytes:
    """A document with **two** identical pictures.

    The omission register counts how many times it reported the same thing,
    so a document with two pictures is the only way to see that counter
    render — one picture would report the omission exactly once.
    """
    return _image_package(2)


def _image_package(count: int) -> bytes:
    """A package with ``count`` inline pictures between two paragraphs."""
    picture = (
        "<w:p><w:r><w:drawing>"
        '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
        'wordprocessingDrawing">'
        '<wp:extent cx="1905000" cy="1905000"/>'
        '<wp:docPr id="1" name="Picture 1"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:nvPicPr><pic:cNvPr id="1" name="figure.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="rId9"/>'
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="1905000"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline>"
        "</w:drawing></w:r></w:p>"
    )
    body = (
        "<w:p><w:r><w:t>"
        + FIXTURE_BEFORE_FIGURE
        + "</w:t></w:r></w:p>"
        + picture * count
        + "<w:p><w:r><w:t>"
        + FIXTURE_AFTER_FIGURE
        + "</w:t></w:r></w:p>"
    )
    return _package(body)


def build_nested_table_package() -> bytes:
    """A table whose cell contains another table.

    Word allows a table inside a cell.  The inner table's content is
    content like any other, so it must be recovered rather than dropped
    with the cell's other children.
    """
    body = """
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr><w:tc>
<w:p><w:r><w:t>Outer cell</w:t></w:r></w:p>
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr><w:tc><w:p><w:r><w:t>Inner cell</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
</w:tc></w:tr>
</w:tbl>
"""
    return _package(body)



# ============================================================================
# Packages for the audit gaps G1-G13
# ============================================================================

#: Text the comment fixtures assert on, by name.
FIXTURE_COMMENT_TEXT: Final[str] = "Check the citation of this claim"
FIXTURE_COMMENT_AUTHOR: Final[str] = "Reviewer"
FIXTURE_COMMENT_INITIALS: Final[str] = "RV"
FIXTURE_COMMENT_DATE: Final[str] = "2026-01-02T03:04:05Z"

#: Text the endnote fixtures assert on, by name.
FIXTURE_ENDNOTE_TEXT: Final[str] = "the closing note"

#: Text the run-formatting fixture asserts on, by name.
FIXTURE_COLOURED_TEXT: Final[str] = "Coloured run"
FIXTURE_HIGHLIGHTED_TEXT: Final[str] = "Highlighted run"
FIXTURE_STRUCK_TEXT: Final[str] = "Struck run"
FIXTURE_FONTED_TEXT: Final[str] = "Fonted run"
FIXTURE_SUBSCRIPT_TEXT: Final[str] = "Subscript run"

#: The colour the run-formatting fixture uses.
FIXTURE_COLOUR: Final[str] = "FF0000"

#: The highlight the run-formatting fixture uses.
FIXTURE_HIGHLIGHT: Final[str] = "yellow"

#: The font the run-formatting fixture uses.
FIXTURE_FONT: Final[str] = "Courier New"

#: The header texts the section fixtures assert on, by name.
FIXTURE_DEFAULT_HEADER: Final[str] = "Default page header"
FIXTURE_EVEN_HEADER: Final[str] = "Even page header"
FIXTURE_FIRST_HEADER: Final[str] = "First page header"

#: The custom style the custom-style fixture defines.
FIXTURE_CUSTOM_STYLE_NAME: Final[str] = "Research Note"

#: The magic number of a legacy binary ``.doc`` document.
LEGACY_DOC_MAGIC: Final[bytes] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def build_legacy_doc_bytes() -> bytes:
    """Return bytes that carry the OLE2 magic number of a legacy ``.doc``.

    A legacy document is an OLE2 compound file; the reader must recognise it
    and refuse it with a reason the operator can act on, rather than failing
    with a confusing "not a valid zip container".
    """
    return LEGACY_DOC_MAGIC + b"\x00" * 512


_COMMENTS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:comment w:id="1" w:author="Reviewer" w:initials="RV" w:date="2026-01-02T03:04:05Z">
<w:p><w:r><w:t>Check the citation of this claim</w:t></w:r></w:p>
</w:comment>
</w:comments>"""

_COMMENTS_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>
</Relationships>"""

_COMMENTS_CONTENT_TYPES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
</Types>"""


def build_comments_package() -> bytes:
    """A document with a comment anchored on a range of its text."""
    body = (
        "<w:p>"
        '<w:commentRangeStart w:id="1"/>'
        "<w:r><w:t>Anchored text</w:t></w:r>"
        '<w:commentRangeEnd w:id="1"/>'
        '<w:r><w:commentReference w:id="1"/></w:r>'
        "</w:p>"
    )
    parts: list[tuple[str, str]] = [
        ("[Content_Types].xml", _COMMENTS_CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _DOCUMENT_SHELL.format(body=body)),
        ("word/_rels/document.xml.rels", _COMMENTS_RELS),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("word/comments.xml", _COMMENTS),
        ("docProps/core.xml", _CORE_PROPERTIES),
    ]
    return _zip(parts)


_ENDNOTES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:endnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:endnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>
<w:endnote w:type="continuationSeparator" w:id="1"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>
<w:endnote w:id="2"><w:p><w:r><w:t>the closing note</w:t></w:r></w:p></w:endnote>
</w:endnotes>"""

_ENDNOTES_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes" Target="endnotes.xml"/>
</Relationships>"""

_ENDNOTES_CONTENT_TYPES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/endnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"/>
</Types>"""


def build_endnotes_package() -> bytes:
    """A document with an endnote referenced from a run."""
    body = (
        "<w:p><w:r><w:t>Body text</w:t></w:r>"
        '<w:r><w:endnoteReference w:id="2"/></w:r></w:p>'
    )
    parts: list[tuple[str, str]] = [
        ("[Content_Types].xml", _ENDNOTES_CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _DOCUMENT_SHELL.format(body=body)),
        ("word/_rels/document.xml.rels", _ENDNOTES_RELS),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("word/endnotes.xml", _ENDNOTES),
        ("docProps/core.xml", _CORE_PROPERTIES),
    ]
    return _zip(parts)


_SECTION_HEADERS: Final[dict[str, str]] = {
    "header1.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:r><w:t>Even page header</w:t></w:r></w:p>
</w:hdr>""",
    "header2.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:r><w:t>Default page header</w:t></w:r></w:p>
</w:hdr>""",
    "header3.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:r><w:t>First page header</w:t></w:r></w:p>
</w:hdr>""",
}

_SECTION_RELS: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header2.xml"/>
<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header3.xml"/>
</Relationships>"""

_SECTION_CONTENT_TYPES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
<Override PartName="/word/header2.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
<Override PartName="/word/header3.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
</Types>"""


def build_section_package() -> bytes:
    """A document whose headers are named by reference type, not by part order.

    ``header1.xml`` holds the *even* header and ``header2.xml`` the default
    one, so a reader that matched headers by part order would swap them.
    """
    body = (
        "<w:p><w:r><w:t>Body text</w:t></w:r></w:p>"
        '<w:sectPr>'
        '<w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/>'
        '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720"/>'
        '<w:headerReference w:type="even" r:id="rId4"/>'
        '<w:headerReference w:type="default" r:id="rId5"/>'
        '<w:headerReference w:type="first" r:id="rId6"/>'
        "</w:sectPr>"
    )
    parts: list[tuple[str, str]] = [
        ("[Content_Types].xml", _SECTION_CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _DOCUMENT_SHELL.format(body=body)),
        ("word/_rels/document.xml.rels", _SECTION_RELS),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("docProps/core.xml", _CORE_PROPERTIES),
    ]
    for name, payload in _SECTION_HEADERS.items():
        parts.append((f"word/{name}", payload))
    return _zip(parts)


_CUSTOM_STYLES: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ResearchNote"><w:name w:val="Research Note"/><w:basedOn w:val="Normal"/></w:style>
</w:styles>"""


def build_custom_style_package() -> bytes:
    """A document whose paragraphs use a style the template does not carry."""
    body = (
        '<w:p><w:pPr><w:pStyle w:val="ResearchNote"/></w:pPr>'
        "<w:r><w:t>Styled paragraph</w:t></w:r></w:p>"
    )
    return _package(body, styles=_CUSTOM_STYLES)


_ROMAN_NUMBERING: Final[str] = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0">
<w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="lowerRoman"/><w:lvlText w:val="%1."/></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2)"/></w:lvl>
</w:abstractNum>
<w:num w:numId="7"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>"""


def build_roman_numbering_package() -> bytes:
    """A document whose list levels are roman and alpha, not arabic."""
    body = (
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="7"/></w:numPr></w:pPr>'
        "<w:r><w:t>Roman one</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="7"/></w:numPr></w:pPr>'
        "<w:r><w:t>Alpha one</w:t></w:r></w:p>"
    )
    return _package(body, numbering=_ROMAN_NUMBERING)


def build_run_formatting_package() -> bytes:
    """A document whose runs carry colour, highlight, strike, font and size."""
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:t>Coloured run</w:t></w:r>'
        "</w:p>"
        "<w:p>"
        '<w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr>'
        "<w:t>Highlighted run</w:t></w:r>"
        "</w:p>"
        "<w:p>"
        "<w:r><w:rPr><w:strike/></w:rPr><w:t>Struck run</w:t></w:r>"
        "</w:p>"
        "<w:p>"
        '<w:r><w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
        '<w:sz w:val="28"/></w:rPr><w:t>Fonted run</w:t></w:r>'
        "</w:p>"
        "<w:p>"
        '<w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr>'
        "<w:t>Subscript run</w:t></w:r>"
        "</w:p>"
    )
    return _package(body)


def build_nested_table_package() -> bytes:
    """A table with declared column widths and a table nested inside a cell."""
    body = """
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="1500"/><w:gridCol w:w="4500"/></w:tblGrid>
<w:tr>
<w:tc><w:tcPr><w:tcW w:w="1500" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>Outer cell</w:t></w:r></w:p>
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid>
<w:tr><w:tc><w:p><w:r><w:t>Inner cell</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
</w:tc>
<w:tc><w:tcPr><w:tcW w:w="4500" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>Second cell</w:t></w:r></w:p></w:tc>
</w:tr>
</w:tbl>
"""
    return _package(body)


#: A one-pixel PNG, small enough to keep in the source.
_TINY_PNG: Final[bytes] = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "0557bfabd40000000049454e44ae426082"
)


def build_media_package() -> bytes:
    """A document that embeds a real image part."""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
</Types>"""
    body = (
        "<w:p><w:r><w:t>Before the picture</w:t></w:r></w:p>"
        "<w:p><w:r><w:drawing>"
        '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<wp:extent cx="1905000" cy="1905000"/>'
        '<wp:docPr id="1" name="Picture 1"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:nvPicPr><pic:cNvPr id="1" name="image1.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="rId9"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        "<pic:spPr><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>"
        "</pic:pic></a:graphicData></a:graphic></wp:inline>"
        "</w:drawing></w:r></w:p>"
    )
    parts: list[tuple[str, str | bytes]] = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _DOCUMENT_SHELL.format(body=body)),
        ("word/_rels/document.xml.rels", rels),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("word/footnotes.xml", _FOOTNOTES),
        ("docProps/core.xml", _CORE_PROPERTIES),
        ("word/media/image1.png", _TINY_PNG),
    ]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            if isinstance(payload, bytes):
                archive.writestr(info, payload)
            else:
                archive.writestr(info, payload.encode("utf-8"))
    return buffer.getvalue()


def build_compression_bomb_package() -> bytes:
    """A package whose single member decompresses far beyond its own size.

    The member holds a run of zeroes, which deflates to almost nothing: the
    archive is a few kilobytes and expands to tens of megabytes, which is the
    shape of a decompression bomb.
    """
    payload = b"\x00" * (48 * 1024 * 1024)
    parts: list[tuple[str, str | bytes]] = [
        ("[Content_Types].xml", _CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _DOCUMENT_SHELL.format(body="")),
        ("word/_rels/document.xml.rels", _DOCUMENT_RELS),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("word/footnotes.xml", _FOOTNOTES),
        ("docProps/core.xml", _CORE_PROPERTIES),
        ("word/media/bomb.bin", payload),
    ]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, member in parts:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            if isinstance(member, bytes):
                archive.writestr(info, member)
            else:
                archive.writestr(info, member.encode("utf-8"))
    return buffer.getvalue()


def build_too_many_members_package(member_count: int = 4096) -> bytes:
    """A package holding more members than the reader accepts."""
    parts: list[tuple[str, str]] = [
        ("[Content_Types].xml", _CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _DOCUMENT_SHELL.format(body="")),
        ("word/_rels/document.xml.rels", _DOCUMENT_RELS),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("word/footnotes.xml", _FOOTNOTES),
        ("docProps/core.xml", _CORE_PROPERTIES),
    ]
    for index in range(member_count):
        parts.append((f"word/padding/part{index:05d}.xml", "<w:p/>"))
    return _zip(parts)


def write_word365_fixture(path: Path) -> Path:
    """Write the fixture to ``path`` and return the path.

    Kept as a small helper so the tests do not each re-implement the write.
    """
    path.write_bytes(build_word365_fixture())
    return path


__all__ = [
    "EXPECTED_PLAIN_TEXT",
    "FIXTURE_AFTER_FIGURE",
    "FIXTURE_BEFORE_FIGURE",
    "FIXTURE_BULLET",
    "FIXTURE_COLOUR",
    "FIXTURE_COLOURED_TEXT",
    "FIXTURE_COMMENT_AUTHOR",
    "FIXTURE_COMMENT_DATE",
    "FIXTURE_COMMENT_INITIALS",
    "FIXTURE_COMMENT_TEXT",
    "FIXTURE_CUSTOM_STYLE_NAME",
    "FIXTURE_DELETED_TEXT",
    "FIXTURE_DEFAULT_HEADER",
    "FIXTURE_ENDNOTE_TEXT",
    "FIXTURE_EVEN_HEADER",
    "FIXTURE_FINAL_PARAGRAPH",
    "FIXTURE_FIRST_HEADER",
    "FIXTURE_FONT",
    "FIXTURE_FONTED_TEXT",
    "FIXTURE_FOOTER_TEXT",
    "FIXTURE_FOOTNOTE_TEXT",
    "FIXTURE_HEADER_TEXT",
    "FIXTURE_HEADING_1",
    "FIXTURE_HEADING_2",
    "FIXTURE_HIGHLIGHT",
    "FIXTURE_HIGHLIGHTED_TEXT",
    "FIXTURE_INSERTED_TEXT",
    "FIXTURE_LINK_TARGET",
    "FIXTURE_LINK_TEXT",
    "FIXTURE_NESTED_BULLET",
    "FIXTURE_NUMBERED_1",
    "FIXTURE_NUMBERED_2",
    "FIXTURE_PARAGRAPH",
    "FIXTURE_SDT_PARAGRAPH",
    "FIXTURE_STRUCK_TEXT",
    "FIXTURE_SUBSCRIPT_TEXT",
    "FIXTURE_TABLE_HEADER",
    "FIXTURE_TABLE_ROW",
    "FIXTURE_TEXTBOX_TEXT",
    "FIXTURE_TITLE",
    "LEGACY_DOC_MAGIC",
    "build_blank_row_table_package",
    "build_breaks_and_fields_package",
    "build_comments_package",
    "build_compression_bomb_package",
    "build_custom_style_package",
    "build_endnotes_package",
    "build_image_document_package",
    "build_legacy_doc_bytes",
    "build_link_target_package",
    "build_malformed_document_package",
    "build_malformed_styles_package",
    "build_media_package",
    "build_merge_region_package",
    "build_merged_table_package",
    "build_multi_header_footer_package",
    "build_nested_table_package",
    "build_precise_core_properties_package",
    "build_roman_numbering_package",
    "build_run_formatting_package",
    "build_section_package",
    "build_style_chain_package",
    "build_style_name_and_id_package",
    "build_style_numbering_package",
    "build_too_many_members_package",
    "build_two_image_document_package",
    "build_wide_merge_package",
    "build_word365_fixture",
    "write_word365_fixture",
]
