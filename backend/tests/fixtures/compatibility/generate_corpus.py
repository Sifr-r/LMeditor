"""The compatibility corpus of LMeditor: the fixtures, and the manifest.

Roadmap requirement R.01 asks for a corpus of real DOCX files and vaults --
corrupt inputs, unsupported features, Unicode, large assets and difficult
layouts -- that the later compatibility, migration and recovery work is
measured against.  A corpus that drifts from its manifest is worse than no
corpus at all: it would let the M1 agents believe that a feature is covered
when the fixture that covers it was quietly deleted.

So the corpus is *generated*, and the generator writes the manifest as it
goes.  The digests in the manifest are computed from the bytes the generator
has just written, so the two cannot disagree: the generator is the only
thing that writes the directory, and it writes the manifest from the same
run that writes the fixtures.

Every byte is deterministic.  The zip members carry a fixed timestamp and a
fixed creator, the text files are written as UTF-8 with LF endings, and the
large image is assembled from *stored* deflate blocks rather than from a
compressor whose output may change between builds.  Running the generator
twice produces the same bytes, so the committed artifacts can be regenerated
and compared.

Nothing here is downloaded and nothing is derived from a file outside this
repository: every fixture is assembled from the parts below.

Run it with::

    python backend/tests/fixtures/compatibility/generate_corpus.py
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Final

#: The directory the corpus lives in: the folder this file sits in.
CORPUS_DIR: Final[Path] = Path(__file__).resolve().parent

#: The manifest the generator writes beside the fixtures.
MANIFEST_NAME: Final[str] = "MANIFEST.json"

#: The name of this file, which the generator never deletes.
GENERATOR_NAME: Final[str] = "generate_corpus.py"

#: Every zip member is written with this timestamp, so the bytes are stable.
FIXED_TIMESTAMP: Final[tuple[int, int, int, int, int, int]] = (1980, 1, 1, 0, 0, 0)

#: The kinds a fixture may carry. The test enforces the same set.
FIXTURE_KINDS: Final[tuple[str, ...]] = (
    "docx",
    "docx-corrupt",
    "not-docx",
    "vault",
    "vault-note",
    "notebook",
)

#: The compatibility statuses of blueprint section 6, plus ``not-applicable``
#: for the dimensions the corpus does not measure.
COMPATIBILITY_STATUSES: Final[tuple[str, ...]] = (
    "read",
    "display",
    "edit",
    "preserve",
    "export",
    "limited",
    "unsupported",
    "unverified",
    "not-applicable",
)

#: The dimensions every fixture's ``expected`` block carries.
EXPECTED_DIMENSIONS: Final[tuple[str, ...]] = (
    "read",
    "display",
    "edit",
    "preserve",
    "export",
    "limitation",
)


# ============================================================================
# The parts every Word package is assembled from
# ============================================================================

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr/></w:rPrDefault><w:pPrDefault><w:pPr/></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="CommentText"><w:name w:val="comment text"/><w:basedOn w:val="Normal"/></w:style>
</w:styles>"""

_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0">
<w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#9702;"/></w:lvl>
</w:abstractNum>
<w:abstractNum w:abstractNumId="1">
<w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2."/></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>"""

_FOOTNOTES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
<w:footnote w:type="continuationSeparator" w:id="1"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
</w:footnotes>"""


def _core_properties(title: str) -> str:
    """The core properties of a fixture package."""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{title}</dc:title>"
        "<dc:subject>Compatibility corpus</dc:subject>"
        "<dc:creator>LMeditor compatibility corpus</dc:creator>"
        "<cp:keywords>compatibility,corpus,docx</cp:keywords>"
        "<dc:description>Generated fixture for the LMeditor compatibility corpus</dc:description>"
        "<cp:revision>1</cp:revision>"
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-02T03:04:05Z</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-01-02T03:04:05Z</dcterms:modified>'
        "</cp:coreProperties>"
    )


def _document(body: str) -> str:
    """A ``word/document.xml`` around the given body XML."""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )


# ============================================================================
# The package writer
# ============================================================================


def _zip(parts: list[tuple[str, str | bytes]]) -> bytes:
    """Zip the given parts into a package whose bytes never vary.

    The member order is the order of the list, the timestamp is fixed and the
    creator is pinned to MS-DOS, so the same parts always produce the same
    archive -- on any platform, with any build of the interpreter.
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o600 << 16
            data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
            archive.writestr(info, data)
    return buffer.getvalue()


def _package(
    body: str,
    *,
    title: str,
    rels: str = _DOCUMENT_RELS,
    content_types: str = _CONTENT_TYPES,
    footnotes: str = _FOOTNOTES,
    extra: list[tuple[str, str | bytes]] | None = None,
    document_xml: str | None = None,
) -> bytes:
    """Assemble a Word package around the given body XML.

    ``document_xml`` replaces ``word/document.xml`` outright, which is how the
    malformed-XML fixture is built.
    """
    parts: list[tuple[str, str | bytes]] = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", _ROOT_RELS),
        (
            "word/document.xml",
            document_xml if document_xml is not None else _document(body),
        ),
        ("word/_rels/document.xml.rels", rels),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("word/footnotes.xml", footnotes),
        ("docProps/core.xml", _core_properties(title)),
    ]
    parts.extend(extra or [])
    return _zip(parts)


# ============================================================================
# The DOCX fixtures
# ============================================================================


def build_docx_minimal() -> bytes:
    """A minimal valid package: headings, a paragraph, bold and italic runs."""
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
        "<w:r><w:t>Compatibility Corpus Minimal Document</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        "<w:r><w:t>Heading Level One</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
        "<w:r><w:t>Heading Level Two</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:pStyle w:val="Heading3"/></w:pPr>'
        "<w:r><w:t>Heading Level Three</w:t></w:r></w:p>"
        "<w:p>"
        '<w:r><w:t xml:space="preserve">A plain paragraph that carries </w:t></w:r>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t>bold text</w:t></w:r>'
        '<w:r><w:t xml:space="preserve"> and </w:t></w:r>'
        '<w:r><w:rPr><w:i/></w:rPr><w:t>italic text</w:t></w:r>'
        '<w:r><w:t xml:space="preserve"> and </w:t></w:r>'
        '<w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>bold italic text</w:t></w:r>'
        '<w:r><w:t xml:space="preserve"> in one paragraph.</w:t></w:r>'
        "</w:p>"
    )
    return _package(body, title="Compatibility Corpus Minimal Document")


def build_docx_table() -> bytes:
    """A table with merged cells and a repeating header row."""
    body = (
        "<w:p><w:r><w:t>The table below merges cells and repeats its header row.</w:t></w:r></w:p>"
        "<w:tbl>"
        "<w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/></w:tblPr>"
        '<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>'
        "<w:tr><w:trPr><w:tblHeader/></w:trPr>"
        "<w:tc><w:tcPr><w:tcW w:w=\"3000\" w:type=\"dxa\"/></w:tcPr>"
        "<w:p><w:r><w:t>Feature</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:tcPr><w:tcW w:w=\"3000\" w:type=\"dxa\"/></w:tcPr>"
        "<w:p><w:r><w:t>Status</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:tcPr><w:tcW w:w=\"3000\" w:type=\"dxa\"/></w:tcPr>"
        "<w:p><w:r><w:t>Note</w:t></w:r></w:p></w:tc>"
        "</w:tr>"
        "<w:tr>"
        "<w:tc><w:tcPr><w:gridSpan w:val=\"2\"/></w:tcPr>"
        "<w:p><w:r><w:t>Cell spanning two columns</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:tcPr><w:tcW w:w=\"3000\" w:type=\"dxa\"/></w:tcPr>"
        "<w:p><w:r><w:t>third column</w:t></w:r></w:p></w:tc>"
        "</w:tr>"
        "<w:tr>"
        "<w:tc><w:tcPr><w:vMerge w:val=\"restart\"/></w:tcPr>"
        "<w:p><w:r><w:t>Cell merged down two rows</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:tcPr><w:tcW w:w=\"3000\" w:type=\"dxa\"/></w:tcPr>"
        "<w:p><w:r><w:t>row three column two</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:tcPr><w:tcW w:w=\"3000\" w:type=\"dxa\"/></w:tcPr>"
        "<w:p><w:r><w:t>row three column three</w:t></w:r></w:p></w:tc>"
        "</w:tr>"
        "<w:tr>"
        "<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>"
        "<w:tc><w:tcPr><w:tcW w:w=\"3000\" w:type=\"dxa\"/></w:tcPr>"
        "<w:p><w:r><w:t>row four column two</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:tcPr><w:tcW w:w=\"3000\" w:type=\"dxa\"/></w:tcPr>"
        "<w:p><w:r><w:t>row four column three</w:t></w:r></w:p></w:tc>"
        "</w:tr>"
        "</w:tbl>"
        "<w:p><w:r><w:t>Text after the table.</w:t></w:r></w:p>"
    )
    return _package(body, title="Compatibility Corpus Table")


def build_docx_lists() -> bytes:
    """A numbered list and a bulleted list, the bulleted one nested."""
    body = (
        "<w:p><w:r><w:t>The two lists below are numbered and bulleted.</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        "<w:r><w:t>Numbered List</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr>'
        "<w:r><w:t>Numbered item one</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr>'
        "<w:r><w:t>Numbered item two</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="2"/></w:numPr></w:pPr>'
        "<w:r><w:t>Numbered item two, sub item</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr>'
        "<w:r><w:t>Numbered item three</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        "<w:r><w:t>Bulleted List</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        "<w:r><w:t>Bulleted item one</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        "<w:r><w:t>Bulleted item two</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        "<w:r><w:t>Bulleted item two, nested</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        "<w:r><w:t>Bulleted item three</w:t></w:r></w:p>"
    )
    return _package(body, title="Compatibility Corpus Lists")


def build_docx_link_footnote() -> bytes:
    """A hyperlink and a footnote."""
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
        '<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"'
        ' Target="https://example.org/compatibility-corpus" TargetMode="External"/>'
        "</Relationships>"
    )
    footnotes = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="1"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
        '<w:footnote w:id="2"><w:p><w:r><w:t>The cited compatibility passage.</w:t></w:r></w:p></w:footnote>'
        "</w:footnotes>"
    )
    body = (
        "<w:p><w:r><w:t xml:space=\"preserve\">The paragraph carries a </w:t></w:r>"
        '<w:hyperlink r:id="rId6"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>'
        "<w:t>link to the corpus</w:t></w:r></w:hyperlink>"
        '<w:r><w:t xml:space="preserve"> and a footnote reference</w:t></w:r>'
        '<w:r><w:footnoteReference w:id="2"/></w:r>'
        "<w:r><w:t>.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>A paragraph after the linked one.</w:t></w:r></w:p>"
    )
    return _package(
        body,
        title="Compatibility Corpus Hyperlink and Footnote",
        rels=rels,
        footnotes=footnotes,
    )


def _png(width: int, height: int) -> bytes:
    """A PNG of deterministic pseudo-random pixels.

    The image data is deflated with *stored* blocks, which the format pins
    down byte for byte, so the image is the same on every platform and with
    every build of the interpreter.
    """
    raw = bytearray()
    state = 0x0202_6002
    for _row in range(height):
        raw.append(0)  # the filter type: none
        for _column in range(width):
            state = (state * 1_103_515_245 + 12_345) & 0xFFFF_FFFF
            raw.append((state >> 16) & 0xFF)
            raw.append((state >> 8) & 0xFF)
            raw.append(state & 0xFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", _stored_zlib(bytes(raw))),
            _png_chunk(b"IEND", b""),
        )
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    """One PNG chunk: its length, its kind, its payload and its CRC."""
    return b"".join(
        (
            struct.pack(">I", len(payload)),
            kind,
            payload,
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFF_FFFF),
        )
    )


def _stored_zlib(data: bytes) -> bytes:
    """A zlib stream built from stored deflate blocks.

    The stored block format is fixed by the deflate specification, so this
    stream is byte-identical everywhere -- unlike a stream a compressor
    chooses to build itself.
    """
    out = bytearray(b"\x78\x01")
    for start in range(0, max(len(data), 1), 65_535):
        chunk = data[start : start + 65_535]
        final = 1 if start + 65_535 >= len(data) else 0
        out.append(final)
        out.extend(struct.pack("<HH", len(chunk), len(chunk) ^ 0xFFFF))
        out.extend(chunk)
    out.extend(struct.pack(">I", zlib.adler32(data) & 0xFFFF_FFFF))
    return bytes(out)


def _image_package(
    title: str, image: bytes, width_emu: int, height_emu: int, body_extra: str
) -> bytes:
    """A package that embeds one image."""
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
        '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    body = (
        body_extra
        + "<w:p><w:r><w:drawing>"
        f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width_emu}" cy="{height_emu}"/>'
        '<wp:docPr id="1" name="Picture 1"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="image1.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="rId9"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        '<pic:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline>"
        "</w:drawing></w:r></w:p>"
        "<w:p><w:r><w:t>The paragraph after the picture.</w:t></w:r></w:p>"
    )
    return _package(
        body,
        title=title,
        rels=rels,
        content_types=content_types,
        extra=[("word/media/image1.png", image)],
    )


def build_docx_image() -> bytes:
    """A document with an inline image."""
    return _image_package(
        "Compatibility Corpus Inline Image",
        _png(8, 8),
        190_500,
        190_500,
        "<w:p><w:r><w:t>The picture below is an inline image.</w:t></w:r></w:p>",
    )


def build_docx_large_image() -> bytes:
    """A document with a large image: the corpus's large asset."""
    return _image_package(
        "Compatibility Corpus Large Image",
        _png(256, 256),
        3_810_000,
        3_810_000,
        "<w:p><w:r><w:t>The picture below is a large asset.</w:t></w:r></w:p>",
    )


def build_docx_tracked_changes() -> bytes:
    """A document with a tracked insertion and a tracked deletion."""
    body = (
        "<w:p><w:r><w:t>The paragraph below carries a tracked change.</w:t></w:r></w:p>"
        "<w:p>"
        '<w:r><w:t xml:space="preserve">The sentence before the edit. </w:t></w:r>'
        '<w:ins w:id="101" w:author="Corpus Reviewer" w:date="2026-01-02T03:04:05Z">'
        "<w:r><w:t>Inserted by the review.</w:t></w:r></w:ins>"
        '<w:del w:id="102" w:author="Corpus Reviewer" w:date="2026-01-02T03:04:05Z">'
        "<w:r><w:delText>Deleted by the review.</w:delText></w:r></w:del>"
        '<w:r><w:t xml:space="preserve"> The sentence after the edit.</w:t></w:r>'
        "</w:p>"
        "<w:p><w:r><w:t>The final paragraph.</w:t></w:r></w:p>"
    )
    return _package(body, title="Compatibility Corpus Tracked Changes")


def build_docx_comments() -> bytes:
    """A document with a comment anchored to a run."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
        '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
        '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>'
        "</Relationships>"
    )
    comments = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:comment w:id="0" w:author="Corpus Reviewer" w:initials="CR" w:date="2026-01-02T03:04:05Z">'
        '<w:p><w:r><w:t>The comment the corpus anchors to a run.</w:t></w:r></w:p>'
        "</w:comment>"
        "</w:comments>"
    )
    body = (
        "<w:p><w:r><w:t>The paragraph below is commented.</w:t></w:r></w:p>"
        "<w:p>"
        '<w:commentRangeStart w:id="0"/>'
        "<w:r><w:t>The commented run.</w:t></w:r>"
        '<w:commentRangeEnd w:id="0"/>'
        '<w:r><w:commentReference w:id="0"/></w:r>'
        "</w:p>"
        "<w:p><w:r><w:t>The final paragraph.</w:t></w:r></w:p>"
    )
    return _package(
        body,
        title="Compatibility Corpus Comments",
        rels=rels,
        content_types=content_types,
        extra=[("word/comments.xml", comments)],
    )


def build_docx_unicode() -> bytes:
    """A Unicode-heavy document: CJK, RTL, combining marks and an emoji."""
    body = (
        "<w:p><w:r><w:t>Chinese: 兼容性与迁移语料库</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Japanese: 互換性と移行のテスト</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Korean: 호환성 코퍼스</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:bidi/></w:pPr>"
        '<w:r><w:rPr><w:rtl/></w:rPr><w:t>Arabic: مرحبا بالعالم</w:t></w:r></w:p>'
        "<w:p><w:pPr><w:bidi/></w:pPr>"
        '<w:r><w:rPr><w:rtl/></w:rPr><w:t>Hebrew: שלום עולם</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>Combining acute: e&#769; (e + U+0301)</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Combining ring: A&#778; (A + U+030A)</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Combining cedilla: s&#807; (s + U+0327)</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Precomposed ring: &#197; (U+00C5)</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Outside the BMP: &#128512; (U+1F600)</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Zero width joiner: &#129489;&#8205;&#129489; (U+1F9D1 ZWJ U+1F9D1)</w:t></w:r></w:p>"
    )
    return _package(body, title="Compatibility Corpus Unicode")


def build_docx_difficult_layout() -> bytes:
    """A very long paragraph and a long table: the difficult layout case."""
    sentence = (
        "The compatibility corpus carries a long paragraph so that pagination, "
        "line breaking and block splitting have something to measure. "
    )
    long_paragraph = "".join(
        f"{sentence}Sentence number {index}. " for index in range(400)
    )
    rows = []
    rows.append(
        "<w:tr><w:trPr><w:tblHeader/></w:trPr>"
        + "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="1500" w:type="dxa"/></w:tcPr>'
            f"<w:p><w:r><w:t>Column {column}</w:t></w:r></w:p></w:tc>"
            for column in range(6)
        )
        + "</w:tr>"
    )
    for row in range(150):
        rows.append(
            "<w:tr>"
            + "".join(
                f'<w:tc><w:tcPr><w:tcW w:w="1500" w:type="dxa"/></w:tcPr>'
                f"<w:p><w:r><w:t>Row {row} column {column}</w:t></w:r></w:p></w:tc>"
                for column in range(6)
            )
            + "</w:tr>"
        )
    table = (
        "<w:tbl>"
        '<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
        '<w:tblGrid>' + '<w:gridCol w:w="1500"/>' * 6 + "</w:tblGrid>"
        + "".join(rows)
        + "</w:tbl>"
    )
    body = (
        "<w:p><w:r><w:t>The paragraph below is very long.</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t xml:space=\"preserve\">{long_paragraph}</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>The table below is long.</w:t></w:r></w:p>"
        + table
        + "<w:p><w:r><w:t>The paragraph after the table.</w:t></w:r></w:p>"
    )
    return _package(body, title="Compatibility Corpus Difficult Layout")


def build_docx_corrupt_truncated() -> bytes:
    """A package whose archive was cut in half."""
    package = build_docx_minimal()
    return package[: len(package) // 2]


def build_docx_corrupt_malformed_xml() -> bytes:
    """A package whose ``word/document.xml`` is not well-formed XML."""
    malformed = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>unclosed paragraph</w:t></w:r>"
        "</w:body></w:document>"
    )
    return _package(
        "",
        title="Compatibility Corpus Malformed XML",
        document_xml=malformed,
    )


# ============================================================================
# The files that are not DOCX packages
# ============================================================================


def build_legacy_word_doc() -> bytes:
    """A legacy Word 97-2003 (OLE2 compound file) file.

    The bytes are the compound file's own header and a directory entry naming
    a ``WordDocument`` stream: enough for the shape to be recognised as the
    legacy binary format, and not a complete Word document.  No conversion
    path exists in the tree, so the reader refuses it.
    """
    header = bytearray(512)
    header[0:8] = bytes((0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1))
    header[24:26] = struct.pack("<H", 0x003E)  # the minor version
    header[26:28] = struct.pack("<H", 0x0003)  # the major version
    header[28:30] = struct.pack("<H", 0xFFFE)  # the byte order
    header[30:32] = struct.pack("<H", 0x0009)  # the sector shift: 512 bytes
    header[32:34] = struct.pack("<H", 0x0006)  # the mini sector shift
    directory = bytearray(512)
    name = "WordDocument".encode("utf-16-le")
    directory[0 : len(name)] = name
    directory[64:66] = struct.pack("<H", 32)  # the name length, in bytes
    directory[66:68] = struct.pack("<H", 5)  # the object type: a stream
    return bytes(header) + bytes(directory)


def build_plain_text() -> bytes:
    """A plain text file: not a package at all."""
    return (
        "This file is plain text.\n"
        "It is not a Word package, and the reader must refuse it.\n"
    ).encode("utf-8")


# ============================================================================
# The vault and the notebook
# ============================================================================

#: The frontmatter of the index note: a title and an inline tag list.
_VAULT_INDEX = """---
title: Compatibility Vault Index
tags: [corpus, vault]
---

The vault of the compatibility corpus.

It holds a wiki-link to a note that exists: [[日本語]].
It holds a wiki-link to a note that does not exist: [[No Such Note]].
It carries a body tag: #corpus.
"""

#: A note whose name is not ASCII, and whose body is CJK.
_VAULT_NON_ASCII = """---
title: 日本語のノート
tags:
  - corpus
  - unicode
---

# 日本語のノート

このノートは名前も本文も ASCII ではない。

[[index]] に戻る。
"""

#: A note whose file starts with a UTF-8 byte order mark.
_VAULT_BOM = """---
title: Note With A Byte Order Mark
tags: [corpus, bom]
---

# Note With A Byte Order Mark

This note starts with a UTF-8 byte order mark.
"""

#: The manifest of the notebook: the sources it holds and the notes it names.
_NOTEBOOK_MANIFEST = {
    "notebook_id": "research-notebook",
    "title": "Compatibility Research Notebook",
    "description": "A notebook of the compatibility corpus.",
    "source_ids": ["src-corpus-001", "src-corpus-002", "src-corpus-003"],
    "note_ids": ["index", "notes/日本語", "notes/bom"],
    "created_at": "2026-01-02T03:04:05.000000Z",
    "modified_at": "2026-01-02T03:04:05.000000Z",
}

#: The bytes of a UTF-8 byte order mark.
_UTF8_BOM: Final[bytes] = b"\xef\xbb\xbf"


def build_vault() -> dict[str, bytes]:
    """Write the vault tree and return the files it holds.

    The keys are the paths of the notes inside the vault, relative to the
    vault's root.
    """
    files = {
        "index.md": _VAULT_INDEX.encode("utf-8"),
        "notes/日本語.md": _VAULT_NON_ASCII.encode("utf-8"),
        # The byte order mark comes before the frontmatter fence, which is
        # exactly what makes this note interesting.
        "notes/bom.md": _UTF8_BOM + _VAULT_BOM.encode("utf-8"),
    }
    for relative, data in files.items():
        _write_bytes(CORPUS_DIR / "vault" / relative, data)
    return files


def build_notebook() -> bytes:
    """Write the notebook manifest and return its bytes."""
    text = json.dumps(_NOTEBOOK_MANIFEST, indent=2, ensure_ascii=False) + "\n"
    data = text.encode("utf-8")
    _write_bytes(CORPUS_DIR / "notebook" / "research-notebook" / "manifest.json", data)
    return data


# ============================================================================
# Digests
# ============================================================================


def _sha256(data: bytes) -> str:
    """The hex digest of the given bytes."""
    return hashlib.sha256(data).hexdigest()


def _tree_digest(root: Path) -> str:
    """The digest of a directory tree.

    The digest is taken over the tree's own listing: every file's path,
    relative to the tree, and the file's own digest, one line per file, in
    path order.  A tree that gains, loses or changes a file changes its
    digest.
    """
    lines: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        lines.append(f"{relative}\t{_sha256(path.read_bytes())}\n")
    return _sha256("".join(lines).encode("utf-8"))


# ============================================================================
# Writing the corpus
# ============================================================================


def _write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to a path, creating the folders above it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _reset_directory() -> None:
    """Empty the corpus directory of everything the generator did not write.

    Only the files this generator owns are removed: the generator itself and
    the manifest stay, and nothing outside the corpus directory is touched.
    """
    for path in sorted(CORPUS_DIR.rglob("*"), reverse=True):
        if path.is_file():
            if path.name in (GENERATOR_NAME, MANIFEST_NAME):
                continue
            path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                # The folder still holds something the generator keeps.
                pass


def _expected(
    read: str,
    *,
    display: str = "not-applicable",
    edit: str = "not-applicable",
    preserve: str = "not-applicable",
    export: str = "not-applicable",
    limitation: str,
) -> dict[str, str]:
    """The expected block of a manifest entry."""
    return {
        "read": read,
        "display": display,
        "edit": edit,
        "preserve": preserve,
        "export": export,
        "limitation": limitation,
    }


def build_corpus() -> list[dict[str, Any]]:
    """Write every fixture and return the manifest's fixture list."""
    _reset_directory()
    build_vault()
    build_notebook()

    entries: list[dict[str, Any]] = []

    def add(
        fixture_id: str,
        path: str,
        kind: str,
        exercises: str,
        expected: dict[str, str],
        generator: str,
        notes: str,
        data: bytes | None = None,
    ) -> None:
        """Write a fixture and record it in the manifest.

        ``data`` is the bytes of a file fixture; a fixture that is a tree is
        written by its own builder before it is recorded here.
        """
        target = CORPUS_DIR / path
        if data is not None:
            _write_bytes(target, data)
        if kind == "vault":
            digest = _tree_digest(target)
        else:
            digest = _sha256(target.read_bytes())
        entries.append(
            {
                "id": fixture_id,
                "path": path,
                "kind": kind,
                "exercises": exercises,
                "sha256": digest,
                "expected": expected,
                "generator": generator,
                "notes": notes,
            }
        )

    add(
        "docx-minimal",
        "docx-minimal.docx",
        "docx",
        "headings, a paragraph, bold and italic runs",
        _expected(
            "read",
            limitation=(
                "the corpus establishes that the package is read; display, "
                "editing, preservation and export are not measured here"
            ),
        ),
        "generate_corpus.py::build_docx_minimal",
        "The smallest document the corpus holds: the baseline every other "
        "DOCX fixture is compared against.",
        data=build_docx_minimal(),
    )

    add(
        "docx-table",
        "docx-table.docx",
        "docx",
        "a table, merged cells and a repeating header row",
        _expected(
            "read",
            limitation=(
                "the table, its grid span, its vertical merge and its header "
                "row are read; the corpus does not measure whether the merges "
                "survive a write"
            ),
        ),
        "generate_corpus.py::build_docx_table",
        "Merges are written the way Word writes them: a grid span on the "
        "cell itself and a vMerge pair across two rows.",
        data=build_docx_table(),
    )

    add(
        "docx-lists",
        "docx-lists.docx",
        "docx",
        "a numbered list and a bulleted list",
        _expected(
            "read",
            limitation=(
                "the two lists are read as list blocks; the corpus does not "
                "measure whether the numbering survives a write"
            ),
        ),
        "generate_corpus.py::build_docx_lists",
        "The bulleted list is nested one level, so the numbering part has to "
        "be read at two levels.",
        data=build_docx_lists(),
    )

    add(
        "docx-link-footnote",
        "docx-link-footnote.docx",
        "docx",
        "a hyperlink and a footnote",
        _expected(
            "read",
            limitation=(
                "the hyperlink target and the footnote text are read; the "
                "corpus does not measure whether a rewritten hyperlink keeps "
                "its target"
            ),
        ),
        "generate_corpus.py::build_docx_link_footnote",
        "The hyperlink is an external relationship, so the target has to "
        "travel through the package's relationship part.",
        data=build_docx_link_footnote(),
    )

    add(
        "docx-image",
        "docx-image.docx",
        "docx",
        "an inline image",
        _expected(
            "read",
            display="limited",
            limitation=(
                "the image part is read as a media reference; the pixels are "
                "not rendered here, so display is limited to the reference"
            ),
        ),
        "generate_corpus.py::build_docx_image",
        "A small inline picture, the ordinary case.",
        data=build_docx_image(),
    )

    add(
        "docx-large-image",
        "docx-large-image.docx",
        "docx",
        "a large image: the corpus's large asset",
        _expected(
            "read",
            display="limited",
            limitation=(
                "the large image part is read as a media reference; the pixels "
                "are not rendered here, so display is limited to the reference"
            ),
        ),
        "generate_corpus.py::build_docx_large_image",
        "A 256 by 256 picture whose bytes do not compress: the corpus's "
        "large asset, and the case a size limit would have to name.",
        data=build_docx_large_image(),
    )

    add(
        "docx-tracked-changes",
        "docx-tracked-changes.docx",
        "docx",
        "a tracked insertion and a tracked deletion",
        _expected(
            "read",
            edit="limited",
            limitation=(
                "the insertion and the deletion are read as revisions; "
                "accepting or rejecting a revision is not established here"
            ),
        ),
        "generate_corpus.py::build_docx_tracked_changes",
        "Both revision kinds in one paragraph, so the reader's revision "
        "handling is measured together.",
        data=build_docx_tracked_changes(),
    )

    add(
        "docx-comments",
        "docx-comments.docx",
        "docx",
        "a comment anchored to a run",
        _expected(
            "read",
            edit="limited",
            limitation=(
                "the comment, its author and its anchor are read; resolving "
                "or editing a comment is not established here"
            ),
        ),
        "generate_corpus.py::build_docx_comments",
        "The comment part is separate from the document part, and the anchor "
        "spans one run.",
        data=build_docx_comments(),
    )

    add(
        "docx-unicode",
        "docx-unicode.docx",
        "docx",
        "CJK, RTL, combining marks, an emoji outside the BMP and a "
        "normalization-sensitive sequence",
        _expected(
            "read",
            display="limited",
            limitation=(
                "the text is read exactly as it is written and is not "
                "normalised: the decomposed ring and the precomposed ring "
                "both survive, and the zero width joiner is kept; the corpus "
                "does not establish that a renderer shapes the RTL runs"
            ),
        ),
        "generate_corpus.py::build_docx_unicode",
        "The decomposed ring (A + U+030A) and the precomposed ring (U+00C5) "
        "are both in the document, so a normalising reader would change it.",
        data=build_docx_unicode(),
    )

    add(
        "docx-difficult-layout",
        "docx-difficult-layout.docx",
        "docx",
        "a very long paragraph and a long table",
        _expected(
            "read",
            limitation=(
                "the long paragraph and the long table are read; the corpus "
                "does not measure pagination, line breaking or block splitting"
            ),
        ),
        "generate_corpus.py::build_docx_difficult_layout",
        "Four hundred sentences in one paragraph and a hundred and fifty rows "
        "of six columns: the difficult layout case.",
        data=build_docx_difficult_layout(),
    )

    add(
        "docx-corrupt-truncated",
        "docx-corrupt-truncated.docx",
        "docx-corrupt",
        "a truncated zip archive",
        _expected(
            "unsupported",
            limitation=(
                "the archive is cut in half, so it is not a zip file at all "
                "and the reader refuses it; no part of it is recoverable"
            ),
        ),
        "generate_corpus.py::build_docx_corrupt_truncated",
        "The central directory is gone, which is what a truncated download "
        "looks like.",
        data=build_docx_corrupt_truncated(),
    )

    add(
        "docx-corrupt-malformed-xml",
        "docx-corrupt-malformed-xml.docx",
        "docx-corrupt",
        "a package whose word/document.xml is malformed XML",
        _expected(
            "unsupported",
            limitation=(
                "the document part is not well-formed XML, so the package "
                "cannot be parsed and the reader refuses it"
            ),
        ),
        "generate_corpus.py::build_docx_corrupt_malformed_xml",
        "The archive is intact; only the document part is broken. This is "
        "the failure a repair path would have to recover from.",
        data=build_docx_corrupt_malformed_xml(),
    )

    add(
        "legacy-word-doc",
        "legacy-word-doc.doc",
        "not-docx",
        "a legacy Word 97-2003 binary file",
        _expected(
            "unsupported",
            limitation=(
                "a legacy Word 97-2003 binary file; the tree holds no "
                "conversion path, so the reader refuses it"
            ),
        ),
        "generate_corpus.py::build_legacy_word_doc",
        "The compound file header and a directory entry naming a "
        "WordDocument stream: the shape of the legacy format, and not a "
        "complete Word document.",
        data=build_legacy_word_doc(),
    )

    add(
        "renamed-docx",
        "renamed.txt",
        "not-docx",
        "a Word package under a .txt name",
        _expected(
            "read",
            limitation=(
                "the reader goes by the package's parts and not by the "
                "extension, so a renamed package is still read"
            ),
        ),
        "generate_corpus.py::build_docx_minimal",
        "The very bytes of docx-minimal.docx under another name: the "
        "extension is the only thing that differs.",
        data=build_docx_minimal(),
    )

    add(
        "plain-text",
        "plain-text.txt",
        "not-docx",
        "a plain text file",
        _expected(
            "unsupported",
            limitation=(
                "plain text is not a Word package, so the reader refuses it"
            ),
        ),
        "generate_corpus.py::build_plain_text",
        "The control case: a file that was never a package.",
        data=build_plain_text(),
    )

    add(
        "vault",
        "vault",
        "vault",
        "a vault of notes with frontmatter, wiki-links and a non-ASCII name",
        _expected(
            "read",
            limitation=(
                "the notes are listed and read; editing, renaming, the "
                "backlink index and the search order are not measured here"
            ),
        ),
        "generate_corpus.py::build_vault",
        "The vault tree. Its digest covers the whole tree, so a note that "
        "changes changes this entry.",
    )

    add(
        "vault-note-index",
        "vault/index.md",
        "vault-note",
        "frontmatter, a wiki-link and a link to a note that does not exist",
        _expected(
            "read",
            limitation=(
                "the frontmatter title, the tags and both wiki-links are "
                "read; the link to the note that does not exist is kept as an "
                "unresolved link"
            ),
        ),
        "generate_corpus.py::build_vault",
        "The index note. Its title comes from the frontmatter, because it "
        "does not open with a heading.",
    )

    add(
        "vault-note-non-ascii",
        "vault/notes/日本語.md",
        "vault-note",
        "a note whose name is not ASCII",
        _expected(
            "read",
            limitation=(
                "a non-ASCII file name and a CJK body are read; nothing "
                "normalises the name"
            ),
        ),
        "generate_corpus.py::build_vault",
        "The name is the test: a vault rule that assumed ASCII would refuse "
        "this note.",
    )

    add(
        "vault-note-bom",
        "vault/notes/bom.md",
        "vault-note",
        "a note that starts with a UTF-8 byte order mark",
        _expected(
            "limited",
            limitation=(
                "the note is read, but the byte order mark is not stripped, so "
                "its frontmatter is not recognised, its frontmatter tags are "
                "lost and its title falls back to its heading"
            ),
        ),
        "generate_corpus.py::build_vault",
        "The file carries a UTF-8 BOM before its frontmatter fence.",
    )

    add(
        "notebook",
        "notebook/research-notebook/manifest.json",
        "notebook",
        "a notebook manifest with sources and a note membership",
        _expected(
            "read",
            limitation=(
                "the manifest is read as a notebook with its sources and its "
                "note membership; the sources and the notes themselves are "
                "the operator's and are not read here"
            ),
        ),
        "generate_corpus.py::build_notebook",
        "The notebook names three sources and three vault notes, so the "
        "membership has to resolve against the vault.",
    )

    return entries


def write_manifest(entries: list[dict[str, Any]]) -> Path:
    """Write the manifest beside the fixtures."""
    manifest = {"schema_version": "1.0", "fixtures": entries}
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    path = CORPUS_DIR / MANIFEST_NAME
    path.write_bytes(text.encode("utf-8"))
    return path


def main() -> None:
    """Write the corpus and its manifest."""
    entries = build_corpus()
    path = write_manifest(entries)
    total = sum(
        (CORPUS_DIR / entry["path"]).stat().st_size
        for entry in entries
        if (CORPUS_DIR / entry["path"]).is_file()
    )
    print(f"wrote {len(entries)} fixtures to {CORPUS_DIR}")
    print(f"wrote the manifest to {path}")
    print(f"total fixture bytes: {total}")


if __name__ == "__main__":
    main()
