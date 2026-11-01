package lmeditor.library

import lmeditor.word.WordBlock
import lmeditor.word.WordBlockKind
import lmeditor.word.WordDocument
import lmeditor.word.WordMedia
import lmeditor.word.WordRun
import lmeditor.word.WordTable
import lmeditor.word.WordTableCell
import lmeditor.word.WordTableRow
import lmeditor.word.WordWriter
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * The exporter, ported from `backend/tests/test_library.py`.
 */
class BookExporterTest {

    /**
     * A book that was created from a list of sections is rendered from its
     * text.
     */
    @Test
    fun aBookWithoutAnOriginIsRenderedFromItsText() {
        val store = newStore()
        importSections(
            store = store,
            bookId = "book_01",
            title = "A book",
            sections = listOf(
                Triple("section_0000", "Intro", "The first section of the book."),
                Triple("section_0001", "Middle", "The second section of the book."),
            ),
        )
        val document = buildBookDocument(store, "book_01")
        val text = document.plainText()
        assertTrue(text.contains("The first section of the book."))
        assertTrue(text.contains("The second section of the book."))
        assertTrue(text.contains("Intro"))
    }

    /**
     * A section whose text has not changed keeps the blocks it was imported
     * from, so its structure survives the round trip.
     */
    @Test
    fun anUnchangedSectionKeepsItsBlocks() {
        val source = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b1",
                    kind = WordBlockKind.HEADING,
                    level = 1,
                    text = "Introduction",
                    runs = listOf(WordRun(text = "Introduction")),
                ),
                WordBlock(
                    blockId = "b2",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "The body of the introduction.",
                    runs = listOf(WordRun(text = "The body of the introduction.")),
                ),
            ),
        )
        val store = newStore()
        importWordDocument(store, "book_01", "A book", source)

        val document = buildBookDocument(store, "book_01")
        assertEquals(2, document.blocks.size)
        assertEquals(WordBlockKind.HEADING, document.blocks[0].kind)
        assertEquals("Introduction", document.blocks[0].text)
        assertEquals(WordBlockKind.PARAGRAPH, document.blocks[1].kind)
    }

    /**
     * A section whose text has changed is rendered again from its new text.
     */
    @Test
    fun anEditedSectionIsRenderedAgain() {
        val source = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b1",
                    kind = WordBlockKind.HEADING,
                    level = 1,
                    text = "Introduction",
                    runs = listOf(WordRun(text = "Introduction")),
                ),
                WordBlock(
                    blockId = "b2",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "The body of the introduction.",
                    runs = listOf(WordRun(text = "The body of the introduction.")),
                ),
            ),
        )
        val store = newStore()
        importWordDocument(store, "book_01", "A book", source)
        store.writeSection("book_01", "section_0001", "A rewritten body.")

        val document = buildBookDocument(store, "book_01")
        assertTrue(document.plainText().contains("A rewritten body."))
    }

    /**
     * The exported document is a `.docx` package that can be written and read
     * back.
     */
    @Test
    fun theExportedDocumentIsAReadablePackage() {
        val store = newStore()
        importSections(
            store = store,
            bookId = "book_01",
            title = "A book",
            sections = listOf(Triple("section_0001", "Intro", "The first section of the book.")),
        )
        val document = buildBookDocument(store, "book_01")
        val bytes = WordWriter().write(document)
        assertTrue(bytes.size > 0)

        val directory = Files.createTempDirectory("lmeditor-export")
        val path = directory.resolve("book.docx")
        Files.write(path, bytes)
        val readBack = lmeditor.word.WordReader().read(path)
        assertTrue(readBack.plainText().contains("The first section of the book."))
    }

    /**
     * A book that was imported from a document keeps that document's notes,
     * media and styles, so the exported file still carries them.
     */
    @Test
    fun theDocumentLevelRecordsSurviveTheRoundTrip() {
        val source = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b1",
                    kind = WordBlockKind.HEADING,
                    level = 1,
                    text = "Introduction",
                    runs = listOf(WordRun(text = "Introduction")),
                ),
            ),
            headers = listOf("a running header"),
            footers = listOf("a running footer"),
        )
        val store = newStore()
        importWordDocument(store, "book_01", "A book", source)
        val document = buildBookDocument(store, "book_01")
        assertEquals(listOf("a running header"), document.headers)
        assertEquals(listOf("a running footer"), document.footers)
    }

    // ------------------------------------------------------------------
    // The structure an edited section keeps
    // ------------------------------------------------------------------

    /**
     * A table of an edited section is still a table.
     *
     * The operator did not touch the table's text, so the table is the one the
     * document was imported with -- its rows, its cells and its structure --
     * and not the pipe-separated lines the editor saw.
     */
    @Test
    fun aTableOfAnEditedSectionSurvivesTheExport() {
        val store = newStore()
        importWordDocument(store, "book_01", "A book", sourceDocument())
        store.writeSection(
            "book_01",
            "section_0002",
            "A new prose.\n\nEvidence | Verdict\nchunk-abc | entailed",
        )

        val document = buildBookDocument(store, "book_01")

        val table = document.blocks.firstOrNull { it.kind == WordBlockKind.TABLE }
        assertNotNull(table, "the table of the edited section must survive")
        val rows = assertNotNull(table.table).rows
        assertEquals(2, rows.size)
        assertEquals("Evidence", rows[0].cells[0].text)
        assertEquals("Verdict", rows[0].cells[1].text)
        assertEquals("chunk-abc", rows[1].cells[0].text)
        assertEquals("entailed", rows[1].cells[1].text)
    }

    /**
     * An image of an edited section is still there.
     *
     * An image renders to no text at all, so the text editor never saw it and
     * could not have removed it.
     */
    @Test
    fun anImageOfAnEditedSectionSurvivesTheExport() {
        val store = newStore()
        importWordDocument(store, "book_01", "A book", sourceDocument())
        store.writeSection("book_01", "section_0002", "A new prose.")

        val document = buildBookDocument(store, "book_01")

        val drawings = document.blocks.flatMap { it.runs }.filter { it.imageRef != null }
        assertEquals(1, drawings.size, "the drawing of the edited section must survive")
        assertEquals("media-1", drawings[0].imageRef)
    }

    /**
     * A page break of an edited section is still there.
     *
     * A page break renders to no text, so the text editor never saw it and
     * could not have removed it.
     */
    @Test
    fun aPageBreakOfAnEditedSectionSurvivesTheExport() {
        val store = newStore()
        importWordDocument(store, "book_01", "A book", sourceDocument())
        store.writeSection("book_01", "section_0002", "A new prose.")

        val document = buildBookDocument(store, "book_01")

        assertTrue(
            document.blocks.any { it.kind == WordBlockKind.PAGE_BREAK },
            "the page break of the edited section must survive",
        )
    }

    /**
     * The heading of an edited section keeps the style it was imported with.
     *
     * The title of the document is a `Title`-styled block, and it comes back as
     * one: it does not become a `Heading1` the way it did when the section was
     * rendered again from its text.
     */
    @Test
    fun theHeadingOfAnEditedSectionKeepsItsStyle() {
        val store = newStore()
        importWordDocument(store, "book_01", "A book", sourceDocument())
        store.writeSection("book_01", "section_0001", "A new opening.")

        val document = buildBookDocument(store, "book_01")

        val heading = document.blocks.first()
        assertEquals(WordBlockKind.TITLE, heading.kind)
        assertEquals("Title", heading.style)
        assertEquals("The Book", heading.text)
    }

    /**
     * The prose of an edited section is the text the operator wrote.
     */
    @Test
    fun theProseOfAnEditedSectionIsTheOperatorsText() {
        val store = newStore()
        importWordDocument(store, "book_01", "A book", sourceDocument())
        store.writeSection("book_01", "section_0002", "A rewritten body.")

        val document = buildBookDocument(store, "book_01")

        val text = document.plainText()
        assertTrue(text.contains("A rewritten body."))
        assertFalse(text.contains("The prose of the section."))
    }

    /**
     * A table the operator changed is still a table.
     *
     * The operator's own words are what the table carries, and it is a real
     * Word table and not the prose the pipe-separated lines would make.
     */
    @Test
    fun aTableTheOperatorChangedIsStillExportedAsATable() {
        val store = newStore()
        importWordDocument(store, "book_01", "A book", sourceDocument())
        store.writeSection(
            "book_01",
            "section_0002",
            "A new prose.\n\nEvidence | Verdict\nchunk-xyz | entailed",
        )

        val document = buildBookDocument(store, "book_01")

        val table = document.blocks.firstOrNull { it.kind == WordBlockKind.TABLE }
        assertNotNull(table, "the changed table must still be a table")
        val rows = assertNotNull(table.table).rows
        assertEquals(2, rows.size)
        assertEquals("Evidence", rows[0].cells[0].text)
        assertEquals("chunk-xyz", rows[1].cells[0].text)
        assertEquals("entailed", rows[1].cells[1].text)
    }

    /**
     * A section that was not edited is exported as it was imported.
     */
    @Test
    fun anUneditedSectionIsExportedAsItWasImported() {
        val store = newStore()
        importWordDocument(store, "book_01", "A book", sourceDocument())

        val document = buildBookDocument(store, "book_01")

        val ids = document.blocks.map { it.blockId }
        assertEquals(listOf("b1", "b2", "b3", "b4", "b5", "b6"), ids)
        val table = document.blocks.first { it.kind == WordBlockKind.TABLE }
        assertEquals("chunk-abc", assertNotNull(table.table).rows[1].cells[0].text)
        assertTrue(document.blocks.any { it.kind == WordBlockKind.PAGE_BREAK })
        assertTrue(document.plainText().contains("The prose of the section."))
    }

    // ------------------------------------------------------------------
    // The fixture
    // ------------------------------------------------------------------

    /**
     * Return a document with a title, a section, a table, an image and a page
     * break.
     *
     * The title is a `Title`-styled block, and the section it opens holds the
     * structure the text editor cannot see.
     */
    private fun sourceDocument(): WordDocument = WordDocument(
        blocks = listOf(
            WordBlock(
                blockId = "b1",
                kind = WordBlockKind.TITLE,
                text = "The Book",
                style = "Title",
                runs = listOf(WordRun(text = "The Book")),
            ),
            WordBlock(
                blockId = "b2",
                kind = WordBlockKind.HEADING,
                level = 1,
                text = "The Section",
                style = "Heading1",
                runs = listOf(WordRun(text = "The Section")),
            ),
            WordBlock(
                blockId = "b3",
                kind = WordBlockKind.PARAGRAPH,
                text = "The prose of the section.",
                runs = listOf(WordRun(text = "The prose of the section.")),
            ),
            WordBlock(
                blockId = "b4",
                kind = WordBlockKind.TABLE,
                table = WordTable(
                    rows = listOf(
                        WordTableRow(cells = listOf(cell("Evidence"), cell("Verdict"))),
                        WordTableRow(cells = listOf(cell("chunk-abc"), cell("entailed"))),
                    ),
                ),
            ),
            WordBlock(
                blockId = "b5",
                kind = WordBlockKind.PARAGRAPH,
                text = "",
                runs = listOf(WordRun(text = "", imageRef = "media-1")),
            ),
            WordBlock(
                blockId = "b6",
                kind = WordBlockKind.PAGE_BREAK,
            ),
        ),
        media = listOf(
            WordMedia(
                mediaId = "media-1",
                partName = "word/media/image1.png",
                contentType = "image/png",
                data = byteArrayOf(1, 2, 3),
            ),
        ),
    )

    /** Return a table cell holding [text]. */
    private fun cell(text: String): WordTableCell = WordTableCell(
        text = text,
        runs = listOf(WordRun(text = text)),
    )
}
