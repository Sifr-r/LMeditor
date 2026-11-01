package lmeditor.word

import lmeditor.core.ValidationError
import lmeditor.core.WordParseError
import java.io.ByteArrayInputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The parts of a package: what must survive being read, and what the reader
 * must refuse.
 */
class WordPartsTest {

    private fun entriesOf(bytes: ByteArray): List<String> {
        val names = mutableListOf<String>()
        java.util.zip.ZipInputStream(ByteArrayInputStream(bytes)).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                names.add(entry.name)
                zip.closeEntry()
            }
        }
        return names
    }

    // ------------------------------------------------------------------
    // Carried parts
    // ------------------------------------------------------------------

    @Test
    fun `a part the model cannot represent is carried through`() {
        val chart = "<?xml version=\"1.0\"?><c:chart xmlns:c=\"urn:chart\"/>"
        val bytes = WordFixtures.buildPackage(
            "<w:p/>",
            extra = mapOf("word/charts/chart1.xml" to chart),
        )
        val document = WordReader().readBytes(bytes)
        val carried = document.parts.firstOrNull { it.partName == "word/charts/chart1.xml" }
        assertTrue(carried != null, "the chart part must travel with the document")
        assertEquals(chart, String(carried!!.data, Charsets.UTF_8))
    }

    @Test
    fun `a carried part keeps its relationship`() {
        val bytes = WordFixtures.buildPackage(
            "<w:p/>",
            rels = WordFixtures.relationshipDocument(
                "<Relationship Id=\"rIdChart\" " +
                    "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart\" " +
                    "Target=\"charts/chart1.xml\"/>",
            ),
            extra = mapOf("word/charts/chart1.xml" to "<c:chart xmlns:c=\"urn:chart\"/>"),
        )
        val document = WordReader().readBytes(bytes)
        val carried = document.parts.first { it.partName == "word/charts/chart1.xml" }
        assertTrue(carried.relationshipId.isNotEmpty())
        assertTrue(carried.relationshipType.isNotEmpty())
    }

    @Test
    fun `a carried part that no relationship reaches is still carried`() {
        val bytes = WordFixtures.buildPackage(
            "<w:p/>",
            extra = mapOf("word/glossary/document.xml" to "<w:glossaryDocument/>"),
        )
        val document = WordReader().readBytes(bytes)
        assertTrue(document.parts.any { it.partName == "word/glossary/document.xml" })
    }

    @Test
    fun `the modelled parts are not carried as opaque parts`() {
        val document = WordReader().readBytes(WordFixtures.buildWord365Fixture())
        for (name in listOf("word/styles.xml", "word/numbering.xml", "word/footnotes.xml")) {
            assertTrue(document.parts.none { it.partName == name }, "$name is modelled and must not be carried")
        }
    }

    @Test
    fun `a media part that no drawing reaches is reported`() {
        val bytes = WordFixtures.buildPackage(
            "<w:p/>",
            extra = mapOf("word/media/orphan.png" to "not really a png"),
        )
        val reader = WordReader()
        reader.readBytes(bytes)
        assertTrue(reader.lastOmissions.any { it.contains("word/media/orphan.png") })
    }

    @Test
    fun `a relationship that points at nothing is reported`() {
        val rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rIdGone" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/gone.png"/>
</Relationships>"""
        val reader = WordReader()
        reader.readBytes(WordFixtures.buildPackage("<w:p><w:altChunk r:id=\"rIdGone\"/></w:p>", rels = rels))
        assertTrue(
            reader.lastOmissions.any { it.contains("word/media/gone.png") },
            reader.lastOmissions.toString(),
        )
    }

    @Test
    fun `a hyperlink with a missing relationship is reported`() {
        val rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
</Relationships>"""
        val body = "<w:p><w:hyperlink r:id=\"rIdGone\"><w:r><w:t>Link</w:t></w:r></w:hyperlink></w:p>"
        val reader = WordReader()
        reader.readBytes(WordFixtures.buildPackage(body, rels = rels))
        assertTrue(reader.lastOmissions.any { it.contains("rIdGone") })
    }

    // ------------------------------------------------------------------
    // Links
    // ------------------------------------------------------------------

    @Test
    fun `an internal anchor resolves to a fragment`() {
        val document = WordReader().readBytes(WordFixtures.buildLinkTargetPackage())
        val targets = document.blocks.flatMap { it.runs }.mapNotNull { it.hyperlink }
        assertTrue(targets.contains("#section-two"))
    }

    @Test
    fun `an absolute target is kept verbatim`() {
        val document = WordReader().readBytes(WordFixtures.buildLinkTargetPackage())
        val targets = document.blocks.flatMap { it.runs }.mapNotNull { it.hyperlink }
        assertTrue(targets.contains("https://example.org/absolute"))
    }

    @Test
    fun `a relative target without a base is preserved and reported`() {
        val reader = WordReader()
        val document = reader.readBytes(WordFixtures.buildLinkTargetPackage())
        val targets = document.blocks.flatMap { it.runs }.mapNotNull { it.hyperlink }
        assertTrue(targets.contains("docs/page.html"))
        assertTrue(reader.lastOmissions.any { it.contains("docs/page.html") })
    }

    @Test
    fun `a relative target with a base is resolved`() {
        val document = WordReader().readBytes(
            WordFixtures.buildLinkTargetPackage(),
            baseUri = "https://example.org/docs/index.html",
        )
        val targets = document.blocks.flatMap { it.runs }.mapNotNull { it.hyperlink }
        assertTrue(targets.contains("https://example.org/docs/docs/page.html"), targets.toString())
    }

    // ------------------------------------------------------------------
    // Styles
    // ------------------------------------------------------------------

    @Test
    fun `a heading is recognised through its style chain`() {
        val document = WordReader().readBytes(WordFixtures.buildStyleChainPackage())
        val heading = document.blocks.first { it.kind == WordBlockKind.HEADING }
        assertEquals(1, heading.level)
    }

    @Test
    fun `a heading is recognised by its style id`() {
        val document = WordReader().readBytes(WordFixtures.buildStyleNameAndIdPackage())
        val headings = document.blocks.filter { it.kind == WordBlockKind.HEADING }
        assertEquals(2, headings.size)
        assertEquals(3, headings[0].level)
        assertEquals(4, headings[1].level)
    }

    @Test
    fun `a list numbering inherited from a style is recognised`() {
        val document = WordReader().readBytes(WordFixtures.buildStyleNumberingPackage())
        val item = document.blocks.first { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals("Style numbered item", item.text)
    }

    // ------------------------------------------------------------------
    // Breaks and fields
    // ------------------------------------------------------------------

    @Test
    fun `a soft break does not split a paragraph`() {
        val document = WordReader().readBytes(WordFixtures.buildBreaksAndFieldsPackage())
        val paragraph = document.blocks.first { it.text.startsWith("Before") }
        assertEquals("Before After", paragraph.text)
    }

    @Test
    fun `a field code contributes no text`() {
        val document = WordReader().readBytes(WordFixtures.buildBreaksAndFieldsPackage())
        assertTrue(!document.plainText().contains("PAGE"))
    }

    // ------------------------------------------------------------------
    // Tables
    // ------------------------------------------------------------------

    @Test
    fun `a merge origin spans the rows it covers`() {
        val document = WordReader().readBytes(WordFixtures.buildWideMergePackage())
        val tables = document.blocks.mapNotNull { it.table }
        assertTrue(tables.isNotEmpty())
        assertEquals(2, tables[0].rows[0].cells[0].rowSpan)
        assertTrue(tables[0].rows[1].cells[0].isMergeContinuation)
    }

    @Test
    fun `a merge continuation carries no text of its own`() {
        val document = WordReader().readBytes(WordFixtures.buildWideMergePackage())
        val tables = document.blocks.mapNotNull { it.table }
        assertEquals("", tables[0].rows[1].cells[0].text)
    }

    @Test
    fun `a three row merge spans three rows`() {
        val document = WordReader().readBytes(WordFixtures.buildMergedTablePackage())
        val tables = document.blocks.mapNotNull { it.table }
        val threeRow = tables.first { it.rows.size == 3 }
        assertEquals(3, threeRow.rows[0].cells[0].rowSpan)
    }

    @Test
    fun `a column span is recovered`() {
        val document = WordReader().readBytes(WordFixtures.buildMergedTablePackage())
        val tables = document.blocks.mapNotNull { it.table }
        assertEquals(2, tables[0].rows[0].cells[1].colSpan)
    }

    @Test
    fun `the cells of a multi paragraph cell are joined with a space`() {
        val document = WordReader().readBytes(WordFixtures.buildMergedTablePackage())
        val tables = document.blocks.mapNotNull { it.table }
        val multi = tables.first { it.rows.size == 1 && it.rows[0].cells.size == 1 }
        assertEquals("First paragraph of the cell Second paragraph of the cell", multi.rows[0].cells[0].text)
    }

    @Test
    fun `a blank row is still a row`() {
        val document = WordReader().readBytes(WordFixtures.buildBlankRowTablePackage())
        val table = document.blocks.first { it.table != null }.table!!
        assertEquals(3, table.rows.size)
        assertEquals("", table.rows[1].cells[0].text)
    }

    // ------------------------------------------------------------------
    // Package integrity
    // ------------------------------------------------------------------

    @Test
    fun `a legacy doc document is refused with a reason`() {
        val failure = assertFailsWith<WordParseError> {
            WordReader().readBytes(WordFixtures.buildLegacyDocBytes())
        }
        assertTrue(failure.reason.contains("legacy .doc"))
    }

    @Test
    fun `a decompression bomb is refused`() {
        assertFailsWith<WordParseError> { WordReader().readBytes(WordFixtures.buildCompressionBombPackage()) }
    }

    @Test
    fun `a package with too many members is refused`() {
        assertFailsWith<WordParseError> {
            WordReader().readBytes(WordFixtures.buildTooManyMembersPackage())
        }
    }

    @Test
    fun `a package with few members is accepted`() {
        val document = WordReader().readBytes(WordFixtures.buildTooManyMembersPackage(memberCount = 16))
        assertTrue(document.parts.size >= 16)
    }

    @Test
    fun `the legacy doc magic number is recognised`() {
        assertTrue(WordFormats.isLegacyDoc(WordFixtures.buildLegacyDocBytes()))
        assertTrue(!WordFormats.isLegacyDoc(WordFixtures.buildWord365Fixture()))
    }

    @Test
    fun `the zip magic number is recognised`() {
        assertTrue(WordFormats.isZipPackage(WordFixtures.buildWord365Fixture()))
        assertTrue(!WordFormats.isZipPackage(WordFixtures.buildLegacyDocBytes()))
    }

    @Test
    fun `an unknown format is described`() {
        assertTrue(WordFormats.describeUnsupportedFormat(ByteArray(32)).contains("neither"))
    }
}

/**
 * The model: the invariants it enforces, and the errors it raises.
 */
class WordModelTest {

    @Test
    fun `a duplicate block id is refused`() {
        val block = WordBlock(
            blockId = "b0001",
            kind = WordBlockKind.PARAGRAPH,
            text = "x",
            runs = listOf(WordRun(text = "x")),
        )
        assertFailsWith<ValidationError> { WordDocument(blocks = listOf(block, block)) }
    }

    @Test
    fun `a block whose text does not match its runs is refused`() {
        assertFailsWith<ValidationError> {
            WordBlock(
                blockId = "b0001",
                kind = WordBlockKind.PARAGRAPH,
                text = "something else",
                runs = listOf(WordRun(text = "x")),
            )
        }
    }

    @Test
    fun `a heading without a level is refused`() {
        assertFailsWith<ValidationError> {
            WordBlock(
                blockId = "b0001",
                kind = WordBlockKind.HEADING,
                text = "x",
                level = 0,
                runs = listOf(WordRun(text = "x")),
            )
        }
    }

    @Test
    fun `a list item without a marker is refused`() {
        assertFailsWith<ValidationError> {
            WordBlock(
                blockId = "b0001",
                kind = WordBlockKind.LIST_ITEM,
                text = "x",
                runs = listOf(WordRun(text = "x")),
            )
        }
    }

    @Test
    fun `a table block without a table is refused`() {
        assertFailsWith<ValidationError> {
            WordBlock(blockId = "b0001", kind = WordBlockKind.TABLE)
        }
    }

    @Test
    fun `a run referencing an unknown footnote is refused`() {
        assertFailsWith<ValidationError> {
            WordDocument(
                blocks = listOf(
                    WordBlock(
                        blockId = "b0001",
                        kind = WordBlockKind.PARAGRAPH,
                        text = "",
                        runs = listOf(WordRun(text = "", footnoteRef = "missing")),
                    ),
                ),
            )
        }
    }

    @Test
    fun `a merge continuation with text is refused`() {
        assertFailsWith<ValidationError> {
            WordTableCell(text = "content", isMergeContinuation = true)
        }
    }

    @Test
    fun `a merge continuation spanning rows is refused`() {
        assertFailsWith<ValidationError> {
            WordTableCell(rowSpan = 2, isMergeContinuation = true)
        }
    }

    @Test
    fun `a deleted run is not part of the final text`() {
        val run = WordRun(
            text = "gone",
            revision = WordRevision(kind = WordRevisionKind.DELETION, author = "A"),
        )
        assertTrue(run.isDeleted)
        val block = WordBlock(
            blockId = "b0001",
            kind = WordBlockKind.PARAGRAPH,
            text = "",
            runs = listOf(run),
        )
        assertEquals("", block.text)
    }

    @Test
    fun `normalisation collapses whitespace and exotic spaces`() {
        assertEquals("a b", WordText.normalizeWhitespace("a \t\n b"))
        assertEquals("a b", WordText.normalizeWhitespace("a" + 0x00A0.toChar() + "b"))
        assertEquals("a b", WordText.normalizeWhitespace("a" + 0x200B.toChar() + "b"))
    }

    @Test
    fun `normalisation is idempotent`() {
        val once = WordText.normalizeWhitespace("  a   b  ")
        assertEquals(once, WordText.normalizeWhitespace(once))
    }

    @Test
    fun `control characters are stripped but tabs survive`() {
        assertEquals("ab\tc", WordText.stripControlCharacters("a b\tc"))
    }

    @Test
    fun `texts are joined and normalised`() {
        assertEquals("a b c", WordText.joinTexts(listOf("a", "", "b", "c")))
        assertEquals("", WordText.joinTexts(listOf("", "")))
    }
}
