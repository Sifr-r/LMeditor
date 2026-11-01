package lmeditor.word

import lmeditor.core.WordParseError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * The reader's contract: what a `.docx` must yield, and what the reader must
 * say when it cannot yield all of it.
 */
class WordReaderTest {

    private fun fixtureBytes(): ByteArray = WordFixtures.buildWord365Fixture()

    private fun fixtureDocument(): WordDocument = WordReader().readBytes(fixtureBytes())

    // ------------------------------------------------------------------
    // The whole-document contract
    // ------------------------------------------------------------------

    @Test
    fun `plain text matches the expected recovery`() {
        assertEquals(WordFixtures.EXPECTED_PLAIN_TEXT, fixtureDocument().plainText())
    }

    @Test
    fun `reading is deterministic`() {
        val bytes = fixtureBytes()
        val first = WordReader().readBytes(bytes)
        val second = WordReader().readBytes(bytes)
        assertEquals(first.plainText(), second.plainText())
        assertEquals(first.blocks.size, second.blocks.size)
    }

    @Test
    fun `reader accepts a path`() {
        val path = java.nio.file.Files.createTempFile("lmeditor-word", ".docx")
        try {
            java.nio.file.Files.write(path, fixtureBytes())
            val document = WordReader().read(path)
            assertEquals(WordFixtures.EXPECTED_PLAIN_TEXT, document.plainText())
        } finally {
            java.nio.file.Files.deleteIfExists(path)
        }
    }

    // ------------------------------------------------------------------
    // Structure
    // ------------------------------------------------------------------

    @Test
    fun `title style becomes a title block`() {
        val title = fixtureDocument().blocks.first()
        assertEquals(WordBlockKind.TITLE, title.kind)
        assertEquals(WordFixtures.FIXTURE_TITLE, title.text)
    }

    @Test
    fun `heading styles become heading blocks with levels`() {
        val headings = fixtureDocument().blocks.filter { it.kind == WordBlockKind.HEADING }
        assertEquals(2, headings.size)
        assertEquals(WordFixtures.FIXTURE_HEADING_1, headings[0].text)
        assertEquals(1, headings[0].level)
        assertEquals(WordFixtures.FIXTURE_HEADING_2, headings[1].text)
        assertEquals(2, headings[1].level)
    }

    @Test
    fun `plain paragraphs become paragraph blocks`() {
        val paragraphs = fixtureDocument().blocks.filter { it.kind == WordBlockKind.PARAGRAPH }
        assertTrue(paragraphs.any { it.text == WordFixtures.FIXTURE_PARAGRAPH })
    }

    @Test
    fun `bold runs survive with their formatting`() {
        val paragraph = fixtureDocument().blocks.first { it.text == WordFixtures.FIXTURE_PARAGRAPH }
        val bold = paragraph.runs.filter { it.bold }
        assertEquals(1, bold.size)
        assertEquals("with emphasis", bold[0].text)
    }

    @Test
    fun `a run keeps its text verbatim`() {
        val paragraph = fixtureDocument().blocks.first { it.text == WordFixtures.FIXTURE_PARAGRAPH }
        assertEquals("A grounded claim ", paragraph.runs[0].text)
        assertEquals(" and a citation marker.", paragraph.runs[2].text)
    }

    @Test
    fun `the block text is normalised after the runs are joined`() {
        val paragraph = fixtureDocument().blocks.first { it.text == WordFixtures.FIXTURE_PARAGRAPH }
        val joined = paragraph.runs.joinToString("") { it.text }
        assertEquals(joined.trim(), paragraph.text)
    }

    // ------------------------------------------------------------------
    // Lists
    // ------------------------------------------------------------------

    @Test
    fun `bullet list items carry the bullet marker`() {
        val bullets = fixtureDocument().blocks.filter { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(WordFixtures.FIXTURE_BULLET, bullets[0].text)
        assertEquals(ListMarker.BULLET, bullets[0].listMarker)
    }

    @Test
    fun `nested list items carry their depth`() {
        val bullets = fixtureDocument().blocks.filter { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(WordFixtures.FIXTURE_NESTED_BULLET, bullets[1].text)
        assertEquals(1, bullets[1].level)
    }

    @Test
    fun `ordered list items carry the number marker`() {
        val numbered = fixtureDocument().blocks
            .filter { it.kind == WordBlockKind.LIST_ITEM && it.listMarker == ListMarker.NUMBER }
        assertEquals(WordFixtures.FIXTURE_NUMBERED_1, numbered[0].text)
        assertEquals(WordFixtures.FIXTURE_NUMBERED_2, numbered[1].text)
    }

    @Test
    fun `ordered items share a numbering group`() {
        val numbered = fixtureDocument().blocks
            .filter { it.kind == WordBlockKind.LIST_ITEM && it.listMarker == ListMarker.NUMBER }
        assertEquals(numbered[0].listGroup, numbered[1].listGroup)
    }

    // ------------------------------------------------------------------
    // Tables
    // ------------------------------------------------------------------

    @Test
    fun `table cells are recovered`() {
        val table = fixtureDocument().blocks.first { it.table != null }.table!!
        assertEquals(2, table.rows.size)
        assertEquals("Evidence", table.rows[0].cells[0].text)
        assertEquals("entailed", table.rows[1].cells[1].text)
    }

    @Test
    fun `table header row is marked`() {
        val table = fixtureDocument().blocks.first { it.table != null }.table!!
        assertEquals(1, table.headerRows)
        assertTrue(table.rows[0].cells.all { it.isHeader })
    }

    @Test
    fun `table renders pipe separated rows`() {
        val table = fixtureDocument().blocks.first { it.table != null }.table!!
        val rendered = table.rows.joinToString("\n") { row -> row.cells.joinToString(" | ") { it.text } }
        assertEquals(WordFixtures.FIXTURE_TABLE_HEADER + "\n" + WordFixtures.FIXTURE_TABLE_ROW, rendered)
    }

    @Test
    fun `a cell after a merged region keeps its text`() {
        // The merge covers the first two columns of the table, so the cell in
        // the third column is outside the merged region: it is an ordinary cell
        // and its text is the table's own. Reading it as a continuation would
        // delete the cell's content without a word.
        val table = WordReader().readBytes(WordFixtures.buildMergeRegionPackage())
            .blocks.first { it.table != null }.table!!
        assertEquals("MERGED-ORIGIN", table.rows[0].cells[0].text)
        assertEquals(2, table.rows[0].cells[0].rowSpan)
        assertTrue(table.rows[1].cells[0].isMergeContinuation)
        assertEquals("", table.rows[1].cells[0].text)
        assertEquals("MERGE-REGION-PAYLOAD", table.rows[1].cells[1].text)
    }

    @Test
    fun `a cell inside a merged region carries no text`() {
        // The second table of the wide-merge fixture is the ragged variant: its
        // plain cell has no column of its own left, so it belongs to the merge
        // and Word never displays its text.
        val tables = WordReader().readBytes(WordFixtures.buildWideMergePackage())
            .blocks.filter { it.table != null }.map { it.table!! }
        val ragged = tables[1]
        assertTrue(ragged.rows[1].cells[1].isMergeContinuation)
        assertEquals("", ragged.rows[1].cells[1].text)
    }

    // ------------------------------------------------------------------
    // Links, notes and revisions
    // ------------------------------------------------------------------

    @Test
    fun `hyperlink target is resolved`() {
        val link = fixtureDocument().blocks.first { block ->
            block.runs.any { it.hyperlink != null }
        }
        assertEquals(WordFixtures.FIXTURE_LINK_TARGET, link.runs.first { it.hyperlink != null }.hyperlink)
    }

    @Test
    fun `footnote text is recovered`() {
        val footnotes = fixtureDocument().footnotes
        assertEquals(1, footnotes.size)
        assertEquals(WordFixtures.FIXTURE_FOOTNOTE_TEXT, footnotes[0].text)
    }

    @Test
    fun `separator footnotes are skipped`() {
        assertEquals(1, fixtureDocument().footnotes.size)
    }

    @Test
    fun `footnote reference attaches to a run`() {
        val run = fixtureDocument().blocks.flatMap { it.runs }.first { it.footnoteRef != null }
        assertEquals("2", run.footnoteRef)
    }

    @Test
    fun `inserted text is included`() {
        assertTrue(fixtureDocument().plainText().contains(WordFixtures.FIXTURE_INSERTED_TEXT))
    }

    @Test
    fun `deleted text is excluded`() {
        assertTrue(!fixtureDocument().plainText().contains(WordFixtures.FIXTURE_DELETED_TEXT))
    }

    @Test
    fun `content control paragraph is recovered`() {
        assertTrue(fixtureDocument().plainText().contains(WordFixtures.FIXTURE_SDT_PARAGRAPH))
    }

    @Test
    fun `text box content is recovered`() {
        assertTrue(fixtureDocument().plainText().contains(WordFixtures.FIXTURE_TEXTBOX_TEXT))
    }

    @Test
    fun `field codes contribute no text`() {
        assertTrue(!fixtureDocument().plainText().contains("Cached table of contents"))
    }

    @Test
    fun `page break becomes a page break block`() {
        val breaks = fixtureDocument().blocks.filter { it.kind == WordBlockKind.PAGE_BREAK }
        assertEquals(1, breaks.size)
    }

    @Test
    fun `header and footer text is recovered`() {
        val document = fixtureDocument()
        assertEquals(1, document.headers.size)
        assertEquals(WordFixtures.FIXTURE_HEADER_TEXT, document.headers[0])
        assertEquals(1, document.footers.size)
        assertEquals(WordFixtures.FIXTURE_FOOTER_TEXT, document.footers[0])
    }

    @Test
    fun `headers are matched by reference type and not by part order`() {
        // The package's `header1.xml` holds the even-page header. A reader
        // that matched the headers by part order would hand back the even-page
        // header first and swap the roles with it.
        val document = WordReader().readBytes(WordFixtures.buildReferencedHeaderPackage())
        assertEquals(
            listOf("Default page header", "First page header", "Even page header"),
            document.headers,
        )
    }

    @Test
    fun `a header reference keeps its role`() {
        val document = WordReader().readBytes(WordFixtures.buildReferencedHeaderPackage())
        val refs = document.sections[0].headerRefs.associate { it.refType to it.text }
        assertEquals(
            mapOf(
                "default" to "Default page header",
                "first" to "First page header",
                "even" to "Even page header",
            ),
            refs,
        )
    }

    @Test
    fun `core properties are recovered`() {
        val properties = fixtureDocument().properties
        assertEquals("Word 365 Reference Document", properties.title)
        assertEquals("Fixture subject", properties.subject)
        assertEquals("LMeditor Fixtures", properties.author)
        assertEquals("fixture,word,365", properties.keywords)
        assertEquals("Fixture comments", properties.comments)
        assertEquals(3, properties.revision)
    }

    @Test
    fun `core timestamps are timezone aware`() {
        val properties = fixtureDocument().properties
        assertNotNull(properties.created)
        assertNotNull(properties.modified)
        assertEquals(2026, properties.created!!.year)
    }

    // ------------------------------------------------------------------
    // Failures
    // ------------------------------------------------------------------

    @Test
    fun `garbage bytes raise a parse error`() {
        val failure = assertFailsWith<WordParseError> { WordReader().readBytes(ByteArray(64) { 0x41 }) }
        assertTrue(failure.reason.isNotEmpty())
    }

    @Test
    fun `a zip without a document part raises a parse error`() {
        val bytes = WordFixtures.zip(listOf("[Content_Types].xml" to "<Types/>"))
        val failure = assertFailsWith<WordParseError> { WordReader().readBytes(bytes) }
        assertTrue(failure.reason.contains("word/document.xml"))
    }

    @Test
    fun `a missing file raises a parse error`() {
        val path = java.nio.file.Files.createTempFile("lmeditor-word", ".docx")
        java.nio.file.Files.deleteIfExists(path)
        assertFailsWith<WordParseError> { WordReader().read(path) }
    }

    @Test
    fun `a truncated package raises a parse error`() {
        val bytes = fixtureBytes()
        assertFailsWith<WordParseError> { WordReader().readBytes(bytes.copyOfRange(0, bytes.size / 2)) }
    }

    @Test
    fun `a malformed document part raises a parse error`() {
        val bytes = WordFixtures.buildMalformedDocumentPackage()
        assertFailsWith<WordParseError> { WordReader().readBytes(bytes) }
    }

    @Test
    fun `a malformed styles part raises a parse error`() {
        val bytes = WordFixtures.buildMalformedStylesPackage()
        assertFailsWith<WordParseError> { WordReader().readBytes(bytes) }
    }

    // ------------------------------------------------------------------
    // The omission register
    // ------------------------------------------------------------------

    @Test
    fun `omissions are reported not swallowed`() {
        val reader = WordReader()
        reader.readBytes(fixtureBytes())
        assertTrue(reader.lastOmissions.isNotEmpty())
        assertTrue(reader.lastOmissions.any { it.contains("image", ignoreCase = true) })
    }

    @Test
    fun `an image contributes no text but is reported`() {
        val reader = WordReader()
        val document = reader.readBytes(WordFixtures.buildImageDocumentPackage())
        val text = document.plainText()
        assertTrue(text.contains(WordFixtures.FIXTURE_BEFORE_FIGURE))
        assertTrue(text.contains(WordFixtures.FIXTURE_AFTER_FIGURE))
        assertTrue(!text.contains("figure.png"))
        assertTrue(reader.lastOmissions.any { it.contains("image", ignoreCase = true) })
    }

    @Test
    fun `the omission register counts repeated omissions`() {
        val reader = WordReader()
        reader.readBytes(WordFixtures.buildTwoImageDocumentPackage())
        assertTrue(
            reader.lastOmissions.any {
                it.lowercase().startsWith("image") && it.endsWith(" x2")
            },
            reader.lastOmissions.toString(),
        )
    }

    @Test
    fun `a table inside a cell is read`() {
        val document = WordReader().readBytes(WordFixtures.buildNestedTablePackage())
        val tables = document.blocks.mapNotNull { it.table }
        assertTrue(tables.isNotEmpty())
        val outerCell = tables[0].rows[0].cells[0]
        assertEquals(1, outerCell.tables.size)
        assertEquals("Inner cell", outerCell.tables[0].rows[0].cells[0].text)
    }
}
