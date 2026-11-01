package lmeditor.word

import java.io.ByteArrayOutputStream
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * Word-365-shaped OOXML fixtures for the LMeditor word-processing tests.
 *
 * The point of this object is to give the reader and the writer a document that
 * looks like the ones Word 365 actually produces, rather than a document
 * produced by the same library that reads it. A test that round-trips the
 * writer's own output proves only that the writer is self-consistent.
 *
 * So these fixtures are assembled as raw OPC packages with the part layout Word
 * itself writes: `word/document.xml` plus `styles.xml`, `numbering.xml`,
 * `footnotes.xml`, a header and a footer, a hyperlink relationship, and
 * `docProps/core.xml`. The document body deliberately exercises the structures
 * a naive reader drops on the floor:
 *
 * * a content control (`w:sdt`) wrapping a paragraph
 * * a tracked insertion (`w:ins`) and a tracked deletion (`w:del`)
 * * a text box (`w:txbxContent`)
 * * a field code (`w:fldSimple`) that must contribute no text
 * * a page break (`w:br w:type="page"`)
 * * a table with a repeating header row
 * * nested bullet levels and an ordered list
 * * an external hyperlink and a footnote reference
 */
internal object WordFixtures {

    /** Every part is written with this timestamp so the fixture bytes are stable. */
    private const val FIXED_ZIP_TIME = 946_684_800_000L // 2000-01-01T00:00:00Z

    /** Text the fixture asserts on, by name. */
    const val FIXTURE_TITLE = "Word 365 Reference Document"
    const val FIXTURE_HEADING_1 = "Grounding Section"
    const val FIXTURE_HEADING_2 = "Nested Subsection"
    const val FIXTURE_PARAGRAPH = "A grounded claim with emphasis and a citation marker."
    const val FIXTURE_BULLET = "First bullet"
    const val FIXTURE_NESTED_BULLET = "Nested bullet"
    const val FIXTURE_NUMBERED_1 = "Numbered one"
    const val FIXTURE_NUMBERED_2 = "Numbered two"
    const val FIXTURE_LINK_TEXT = "External reference"
    const val FIXTURE_LINK_TARGET = "https://example.org/source"
    const val FIXTURE_SDT_PARAGRAPH = "Content control paragraph"
    const val FIXTURE_INSERTED_TEXT = "Inserted by review"
    const val FIXTURE_DELETED_TEXT = "Deleted by review"
    const val FIXTURE_TEXTBOX_TEXT = "Text box content"
    const val FIXTURE_FOOTNOTE_TEXT = "the cited passage"
    const val FIXTURE_FINAL_PARAGRAPH = "Final paragraph."
    const val FIXTURE_HEADER_TEXT = "LMeditor Word 365 Fixture"
    const val FIXTURE_FOOTER_TEXT = "Page footer"
    const val FIXTURE_TABLE_HEADER = "Evidence | Verdict"
    const val FIXTURE_TABLE_ROW = "chunk-abc | entailed"

    /** The prose around the figure in [buildImageDocumentPackage]. */
    const val FIXTURE_BEFORE_FIGURE = "Before the figure."
    const val FIXTURE_AFTER_FIGURE = "After the figure."

    /** The exact text the reader is expected to recover, in order. */
    val EXPECTED_PLAIN_TEXT: String = listOf(
        FIXTURE_TITLE,
        FIXTURE_HEADING_1,
        FIXTURE_PARAGRAPH,
        FIXTURE_HEADING_2,
        FIXTURE_BULLET,
        FIXTURE_NESTED_BULLET,
        FIXTURE_NUMBERED_1,
        FIXTURE_NUMBERED_2,
        FIXTURE_LINK_TEXT,
        "$FIXTURE_TABLE_HEADER\n$FIXTURE_TABLE_ROW",
        FIXTURE_SDT_PARAGRAPH,
        FIXTURE_INSERTED_TEXT,
        FIXTURE_TEXTBOX_TEXT,
        FIXTURE_FINAL_PARAGRAPH,
    ).joinToString("\n\n")

    private const val CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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

    private const val ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

    private const val DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/source" TargetMode="External"/>
</Relationships>"""

    private const val STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr/></w:rPrDefault><w:pPrDefault><w:pPr/></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/></w:style>
</w:styles>"""

    private const val NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0">
<w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#9702;"/></w:lvl>
<w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#9642;"/></w:lvl>
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

    private const val FOOTNOTES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
<w:footnote w:type="continuationSeparator" w:id="1"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
<w:footnote w:id="2"><w:p><w:r><w:t>the cited passage</w:t></w:r></w:p></w:footnote>
</w:footnotes>"""

    private const val HEADER = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:r><w:t>LMeditor Word 365 Fixture</w:t></w:r></w:p>
</w:hdr>"""

    private const val FOOTER = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:r><w:t>Page footer</w:t></w:r></w:p>
</w:ftr>"""

    private const val CORE_PROPERTIES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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

    private const val DOCUMENT_SHELL =
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<w:body>""" +
            "{body}" +
            """<w:sectPr/></w:body></w:document>"""

    /** Assemble a document relationship part with the given extra relationships. */
    fun relationshipDocument(extra: String): String =
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
$extra
</Relationships>"""

    /**
     * Assemble a package around the given body XML.
     *
     * `documentXml` replaces `word/document.xml` outright, which is how the
     * malformed-XML cases are built.
     */
    fun buildPackage(
        body: String,
        styles: String = STYLES,
        numbering: String = NUMBERING,
        rels: String = DOCUMENT_RELS,
        footnotes: String = FOOTNOTES,
        core: String = CORE_PROPERTIES,
        extra: Map<String, String> = emptyMap(),
        documentXml: String? = null,
    ): ByteArray {
        val parts = mutableListOf<Pair<String, String>>()
        parts.add("[Content_Types].xml" to CONTENT_TYPES)
        parts.add("_rels/.rels" to ROOT_RELS)
        parts.add(
            "word/document.xml" to (documentXml ?: DOCUMENT_SHELL.replace("{body}", body)),
        )
        parts.add("word/_rels/document.xml.rels" to rels)
        parts.add("word/styles.xml" to styles)
        parts.add("word/numbering.xml" to numbering)
        parts.add("word/footnotes.xml" to footnotes)
        parts.add("docProps/core.xml" to core)
        for ((name, payload) in extra) {
            parts.add(name to payload)
        }
        return zip(parts)
    }

    /** Assemble a package whose members may be raw bytes. */
    fun zipBytes(parts: List<Pair<String, ByteArray>>): ByteArray {
        val buffer = ByteArrayOutputStream()
        ZipOutputStream(buffer).use { zip ->
            for ((name, data) in parts) {
                val entry = ZipEntry(name)
                entry.time = FIXED_ZIP_TIME
                entry.method = ZipEntry.DEFLATED
                entry.size = data.size.toLong()
                val crc = CRC32()
                crc.update(data)
                entry.crc = crc.value
                zip.putNextEntry(entry)
                zip.write(data)
                zip.closeEntry()
            }
        }
        return buffer.toByteArray()
    }

    /** Assemble a package of text parts. */
    fun zip(parts: List<Pair<String, String>>): ByteArray =
        zipBytes(parts.map { (name, payload) -> name to payload.toByteArray(Charsets.UTF_8) })

    /**
     * Return the bytes of the Word-365-shaped reference document.
     *
     * The package is written with a fixed member order and fixed member
     * timestamps, so the fixture is byte-stable and can be compared directly.
     */
    fun buildWord365Fixture(): ByteArray {
        val parts = mutableListOf<Pair<String, String>>()
        parts.add("[Content_Types].xml" to CONTENT_TYPES)
        parts.add("_rels/.rels" to ROOT_RELS)
        parts.add("word/document.xml" to documentXml())
        parts.add("word/_rels/document.xml.rels" to DOCUMENT_RELS)
        parts.add("word/styles.xml" to STYLES)
        parts.add("word/numbering.xml" to NUMBERING)
        parts.add("word/footnotes.xml" to FOOTNOTES)
        parts.add("word/header1.xml" to HEADER)
        parts.add("word/footer1.xml" to FOOTER)
        parts.add("docProps/core.xml" to CORE_PROPERTIES)
        return zip(parts)
    }

    /** Assemble the fixture body in document order. */
    private fun documentXml(): String =
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:v="urn:schemas-microsoft-com:vml">
<w:body>
<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>$FIXTURE_TITLE</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>$FIXTURE_HEADING_1</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">A grounded claim </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>with emphasis</w:t></w:r><w:r><w:t xml:space="preserve"> and a citation marker.</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>$FIXTURE_HEADING_2</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>$FIXTURE_BULLET</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>$FIXTURE_NESTED_BULLET</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>$FIXTURE_NUMBERED_1</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>$FIXTURE_NUMBERED_2</w:t></w:r></w:p>
<w:p><w:hyperlink r:id="rId6"><w:r><w:t>$FIXTURE_LINK_TEXT</w:t></w:r></w:hyperlink></w:p>
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
<w:p><w:r><w:t>$FIXTURE_SDT_PARAGRAPH</w:t></w:r></w:p>
</w:sdtContent></w:sdt>
<w:p><w:ins w:id="101" w:author="Reviewer" w:date="2026-01-02T03:04:05Z"><w:r><w:t>$FIXTURE_INSERTED_TEXT</w:t></w:r></w:ins><w:del w:id="102" w:author="Reviewer" w:date="2026-01-02T03:04:05Z"><w:r><w:delText>$FIXTURE_DELETED_TEXT</w:delText></w:r></w:del></w:p>
<w:p><w:r><w:pict><v:shape type="#_x0000_t202" style="width:200pt;height:50pt"><v:textbox><w:txbxContent><w:p><w:r><w:t>$FIXTURE_TEXTBOX_TEXT</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
<w:p><w:r><w:footnoteReference w:id="2"/></w:r></w:p>
<w:p><w:r><w:t>$FIXTURE_FINAL_PARAGRAPH</w:t></w:r></w:p>
<w:sectPr><w:headerReference w:type="default" r:id="rId4"/><w:footerReference w:type="default" r:id="rId5"/></w:sectPr>
</w:body>
</w:document>"""

    /**
     * A table with a two-row merge, a three-row merge and a column span.
     *
     * Also carries a cell with two paragraphs, which must join with a single
     * space, and a cell whose own text must survive the merge.
     */
    fun buildMergedTablePackage(): ByteArray = buildPackage(
        """
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
""",
    )

    /** An internal anchor, a relative external target and an absolute one. */
    fun buildLinkTargetPackage(): ByteArray {
        val rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rIdRel" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="docs/page.html" TargetMode="External"/>
<Relationship Id="rIdAbs" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/absolute" TargetMode="External"/>
</Relationships>"""
        val body = """
<w:p><w:hyperlink w:anchor="section-two"><w:r><w:t>Internal anchor</w:t></w:r></w:hyperlink></w:p>
<w:p><w:hyperlink r:id="rIdRel"><w:r><w:t>Relative target</w:t></w:r></w:hyperlink></w:p>
<w:p><w:hyperlink r:id="rIdAbs"><w:r><w:t>Absolute target</w:t></w:r></w:hyperlink></w:p>
"""
        return buildPackage(body, rels = rels)
    }

    /** A heading style that inherits its name through a `w:basedOn` chain. */
    fun buildStyleChainPackage(): ByteArray {
        val styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="DerivedHeading"><w:basedOn w:val="Heading1"/></w:style>
</w:styles>"""
        val body =
            "<w:p><w:pPr><w:pStyle w:val=\"DerivedHeading\"/></w:pPr>" +
                "<w:r><w:t>Inherited heading</w:t></w:r></w:p>"
        return buildPackage(body, styles = styles)
    }

    /**
     * Two headings, one recognised by its style id, one by its style name.
     *
     * The id and the name both match in the main fixture, so here one heading
     * matches only by id and the other only by name (and its name is lower
     * case, which is how Word writes them).
     */
    fun buildStyleNameAndIdPackage(): ByteArray {
        val styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="Custom Style"/></w:style>
<w:style w:type="paragraph" w:styleId="MyOwnStyle"><w:name w:val="heading 4"/></w:style>
</w:styles>"""
        val body =
            "<w:p><w:pPr><w:pStyle w:val=\"Heading3\"/></w:pPr>" +
                "<w:r><w:t>Heading by style id</w:t></w:r></w:p>" +
                "<w:p><w:pPr><w:pStyle w:val=\"MyOwnStyle\"/></w:pPr>" +
                "<w:r><w:t>Heading by style name</w:t></w:r></w:p>"
        return buildPackage(body, styles = styles)
    }

    /** A paragraph that inherits its list numbering from its style. */
    fun buildStyleNumberingPackage(): ByteArray {
        val styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ListBulletStyle"><w:name w:val="List Bullet"/><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr></w:style>
</w:styles>"""
        val body =
            "<w:p><w:pPr><w:pStyle w:val=\"ListBulletStyle\"/></w:pPr>" +
                "<w:r><w:t>Style numbered item</w:t></w:r></w:p>"
        return buildPackage(body, styles = styles)
    }

    /** Soft breaks, tabs, carriage returns and a field code. */
    fun buildBreaksAndFieldsPackage(): ByteArray = buildPackage(
        """
<w:p><w:r><w:t>Before</w:t><w:br/><w:t>After</w:t></w:r></w:p>
<w:p><w:r><w:t>Tabbed</w:t><w:tab/><w:t>Columns</w:t></w:r></w:p>
<w:p><w:r><w:t>Carriage</w:t><w:cr/><w:t>Return</w:t></w:r></w:p>
<w:p><w:r><w:instrText> PAGE \\* MERGEFORMAT </w:instrText></w:r><w:r><w:t>Visible</w:t></w:r></w:p>
""",
    )

    /** Three header parts and two footer parts, to pin the part ordering. */
    fun buildMultiHeaderFooterPackage(): ByteArray {
        fun header(text: String) =
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                "<w:hdr xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">" +
                "<w:p><w:r><w:t>$text</w:t></w:r></w:p></w:hdr>"

        fun footer(text: String) =
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                "<w:ftr xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">" +
                "<w:p><w:r><w:t>$text</w:t></w:r></w:p></w:ftr>"

        val extra = linkedMapOf(
            "word/header1.xml" to header("Header one"),
            "word/header2.xml" to header("Header two"),
            "word/header3.xml" to header("Header three"),
            "word/footer1.xml" to footer("Footer one"),
            "word/footer2.xml" to footer("Footer two"),
        )
        return buildPackage("<w:p/>", extra = extra)
    }

    /**
     * A package whose headers are named by the role they play.
     *
     * `header1.xml` holds the even-page header, `header2.xml` the default one
     * and `header3.xml` the first-page one, and the section names them by
     * their role rather than by their name. A reader that matched the headers
     * by part order would swap them.
     */
    fun buildReferencedHeaderPackage(): ByteArray {
        fun header(text: String) =
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                "<w:hdr xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">" +
                "<w:p><w:r><w:t>$text</w:t></w:r></w:p></w:hdr>"

        val body = "<w:p/>"
        val documentXml =
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"" +
                " xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">" +
                "<w:body>$body<w:sectPr>" +
                "<w:headerReference w:type=\"even\" r:id=\"rId4\"/>" +
                "<w:headerReference w:type=\"default\" r:id=\"rId5\"/>" +
                "<w:headerReference w:type=\"first\" r:id=\"rId6\"/>" +
                "</w:sectPr></w:body></w:document>"
        val rels = relationshipDocument(
            "<Relationship Id=\"rId4\"" +
                " Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/header\"" +
                " Target=\"header1.xml\"/>" +
                "<Relationship Id=\"rId5\"" +
                " Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/header\"" +
                " Target=\"header2.xml\"/>" +
                "<Relationship Id=\"rId6\"" +
                " Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/header\"" +
                " Target=\"header3.xml\"/>",
        )
        val extra = linkedMapOf(
            "word/header1.xml" to header("Even page header"),
            "word/header2.xml" to header("Default page header"),
            "word/header3.xml" to header("First page header"),
        )
        return buildPackage(body, rels = rels, extra = extra, documentXml = documentXml)
    }

    /** A package whose `word/document.xml` is not well-formed XML. */
    fun buildMalformedDocumentPackage(): ByteArray =
        buildPackage("", documentXml = "<w:document><w:body><w:p></w:body></w:document>")

    /** A package whose `word/styles.xml` is not well-formed XML. */
    fun buildMalformedStylesPackage(): ByteArray =
        buildPackage("<w:p/>", styles = "<w:styles><w:style></w:styles>")

    /** A package whose core properties carry exact, known timestamps. */
    fun buildPreciseCorePropertiesPackage(): ByteArray {
        val core = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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
        return buildPackage("<w:p/>", core = core)
    }

    /**
     * Merges that span more than one grid column.
     *
     * The first table is the canonical Word 2x2 merged block: one cell with
     * `w:gridSpan="2"` and `w:vMerge w:val="restart"` above one cell with
     * `w:gridSpan="2"` and a bare `w:vMerge`.
     *
     * The second table is the ragged variant: the origin spans two columns, and
     * the row below holds a continuing cell in the first covered column and a
     * plain cell in the second. The plain cell sits inside the merged region, so
     * Word would not display its text.
     */
    fun buildWideMergePackage(): ByteArray = buildPackage(
        """
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
""",
    )

    /**
     * A merge that covers two of the table's three columns.
     *
     * The origin spans two columns and starts the merge; the row below holds a
     * bare `w:vMerge` that continues it and a plain cell that has the third
     * column of its own. The plain cell is outside the merged region, so its
     * text belongs to the table and must survive a round trip.
     */
    fun buildMergeRegionPackage(): ByteArray = buildPackage(
        """
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
""",
    )

    /** A one-column table whose middle row is blank, for the row-count rule. */
    fun buildBlankRowTablePackage(): ByteArray = buildPackage(
        """
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr><w:tc><w:p><w:r><w:t>alpha</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p/></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>omega</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
""",
    )

    /**
     * A document with an inline picture between two paragraphs.
     *
     * The picture contributes no text -- but it must be *recorded*, not silently
     * dropped, and the prose around it must survive untouched.
     */
    fun buildImageDocumentPackage(): ByteArray = imagePackage(1)

    /**
     * A document with **two** identical pictures.
     *
     * The omission register counts how many times it reported the same thing,
     * so a document with two pictures is the only way to see that counter
     * render -- one picture would report the omission exactly once.
     */
    fun buildTwoImageDocumentPackage(): ByteArray = imagePackage(2)

    private fun imagePackage(count: Int): ByteArray {
        val picture =
            "<w:p><w:r><w:drawing>" +
                "<wp:inline xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\">" +
                "<wp:extent cx=\"1905000\" cy=\"1905000\"/>" +
                "<wp:docPr id=\"1\" name=\"Picture 1\"/>" +
                "<a:graphic xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">" +
                "<a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">" +
                "<pic:pic xmlns:pic=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">" +
                "<pic:nvPicPr><pic:cNvPr id=\"1\" name=\"figure.png\"/><pic:cNvPicPr/></pic:nvPicPr>" +
                "<pic:blipFill><a:blip r:embed=\"rId9\"/>" +
                "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>" +
                "<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"1905000\" cy=\"1905000\"/>" +
                "</a:xfrm><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>" +
                "</pic:pic></a:graphicData></a:graphic></wp:inline>" +
                "</w:drawing></w:r></w:p>"
        val body =
            "<w:p><w:r><w:t>$FIXTURE_BEFORE_FIGURE</w:t></w:r></w:p>" +
                picture.repeat(count) +
                "<w:p><w:r><w:t>$FIXTURE_AFTER_FIGURE</w:t></w:r></w:p>"
        return buildPackage(body)
    }

    /**
     * A table whose cell contains another table.
     *
     * Word allows a table inside a cell. The inner table's content is content
     * like any other, so it must be recovered rather than dropped with the
     * cell's other children.
     */
    fun buildNestedTablePackage(): ByteArray = buildPackage(
        """
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
""",
    )

    /**
     * Return bytes that carry the OLE2 magic number of a legacy `.doc`.
     *
     * A legacy document is an OLE2 compound file; the reader must recognise it
     * and refuse it with a reason the operator can act on, rather than failing
     * with a confusing "not a valid zip container".
     */
    fun buildLegacyDocBytes(): ByteArray = WordFormats.LEGACY_DOC_MAGIC + ByteArray(512)

    /**
     * A package whose single member decompresses far beyond its own size.
     *
     * The member holds a run of zeroes, which deflates to almost nothing: the
     * archive is a few kilobytes and expands to tens of megabytes, which is the
     * shape of a decompression bomb.
     */
    fun buildCompressionBombPackage(): ByteArray {
        val payload = ByteArray(48 * 1024 * 1024)
        val parts = mutableListOf<Pair<String, ByteArray>>()
        parts.add("[Content_Types].xml" to CONTENT_TYPES.toByteArray(Charsets.UTF_8))
        parts.add("_rels/.rels" to ROOT_RELS.toByteArray(Charsets.UTF_8))
        parts.add(
            "word/document.xml" to
                DOCUMENT_SHELL.replace("{body}", "").toByteArray(Charsets.UTF_8),
        )
        parts.add("word/_rels/document.xml.rels" to DOCUMENT_RELS.toByteArray(Charsets.UTF_8))
        parts.add("word/styles.xml" to STYLES.toByteArray(Charsets.UTF_8))
        parts.add("word/numbering.xml" to NUMBERING.toByteArray(Charsets.UTF_8))
        parts.add("word/footnotes.xml" to FOOTNOTES.toByteArray(Charsets.UTF_8))
        parts.add("docProps/core.xml" to CORE_PROPERTIES.toByteArray(Charsets.UTF_8))
        parts.add("word/media/bomb.bin" to payload)
        return zipBytes(parts)
    }

    /** A package holding more members than the reader accepts. */
    fun buildTooManyMembersPackage(memberCount: Int = 4096): ByteArray {
        val parts = mutableListOf<Pair<String, String>>()
        parts.add("[Content_Types].xml" to CONTENT_TYPES)
        parts.add("_rels/.rels" to ROOT_RELS)
        parts.add("word/document.xml" to DOCUMENT_SHELL.replace("{body}", ""))
        parts.add("word/_rels/document.xml.rels" to DOCUMENT_RELS)
        parts.add("word/styles.xml" to STYLES)
        parts.add("word/numbering.xml" to NUMBERING)
        parts.add("word/footnotes.xml" to FOOTNOTES)
        parts.add("docProps/core.xml" to CORE_PROPERTIES)
        for (index in 0 until memberCount) {
            parts.add("word/padding/part%05d.xml".format(index) to "<w:p/>")
        }
        return zip(parts)
    }
}
