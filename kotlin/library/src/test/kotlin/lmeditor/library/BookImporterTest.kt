package lmeditor.library

import lmeditor.word.WordBlock
import lmeditor.word.WordBlockKind
import lmeditor.word.WordDocument
import lmeditor.word.WordRun
import lmeditor.word.WordWriter
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The importer and the exporter, ported from `backend/tests/test_library.py`.
 */
class BookImporterTest {

    /**
     * Sections are stored, chunked and indexed, and the brief is rebuilt.
     */
    @Test
    fun sectionsAreStoredChunkedAndIndexed() {
        val store = newStore()
        val manifest = importSections(
            store = store,
            bookId = "book_01",
            title = "A book",
            sections = listOf(
                Triple("section_0000", "Intro", "The first section of the book."),
                Triple("section_0001", "Middle", "The second section of the book."),
            ),
        )
        assertEquals(2, manifest.sectionCount)
        assertEquals("The first section of the book.", store.readSection("book_01", "section_0000"))
        assertTrue(store.chunks("book_01").isNotEmpty())
        assertTrue(store.brief("book_01").contains("A book"))
        assertTrue(store.brief("book_01").contains("Intro"))
    }

    /**
     * The chunks of a section are recorded against the section they came from.
     */
    @Test
    fun theChunksAreRecordedAgainstTheirSection() {
        val store = newStore()
        importSections(
            store = store,
            bookId = "book_01",
            title = "A book",
            sections = listOf(Triple("section_0001", "Intro", "The first section of the book.")),
        )
        val manifest = store.open("book_01")
        assertEquals(1, manifest.sections[0].chunkIds.size)
    }

    /**
     * Importing twice leaves the same sections and the same chunks.
     */
    @Test
    fun importingTwiceLeavesTheSameSections() {
        val store = newStore()
        val sections = listOf(Triple("section_0001", "Intro", "The first section of the book."))
        importSections(store, "book_01", "A book", sections)
        val first = store.readSection("book_01", "section_0001")
        importSections(store, "book_01", "A book", sections)
        assertEquals(first, store.readSection("book_01", "section_0001"))
        assertEquals(1, store.open("book_01").sectionCount)
    }

    /**
     * A Word document is split at its headings, and the heading becomes the
     * section's title.
     */
    @Test
    fun aWordDocumentIsSplitAtItsHeadings() {
        val document = WordDocument(
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
                WordBlock(
                    blockId = "b3",
                    kind = WordBlockKind.HEADING,
                    level = 1,
                    text = "Conclusion",
                    runs = listOf(WordRun(text = "Conclusion")),
                ),
                WordBlock(
                    blockId = "b4",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "The body of the conclusion.",
                    runs = listOf(WordRun(text = "The body of the conclusion.")),
                ),
            ),
        )
        val sections = sectionsWithOrigin(document)
        assertEquals(2, sections.size)
        assertEquals("Introduction", sections[0].title)
        assertEquals("The body of the introduction.", sections[0].content)
        assertEquals("Conclusion", sections[1].title)
        assertEquals("The body of the conclusion.", sections[1].content)
    }

    /**
     * A document that opens with prose keeps that prose in a section of its
     * own: content before the first heading is not dropped.
     */
    @Test
    fun proseBeforeTheFirstHeadingIsKept() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b1",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "An abstract.",
                    runs = listOf(WordRun(text = "An abstract.")),
                ),
                WordBlock(
                    blockId = "b2",
                    kind = WordBlockKind.HEADING,
                    level = 1,
                    text = "Introduction",
                    runs = listOf(WordRun(text = "Introduction")),
                ),
                WordBlock(
                    blockId = "b3",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "The body.",
                    runs = listOf(WordRun(text = "The body.")),
                ),
            ),
        )
        val sections = sectionsWithOrigin(document)
        assertEquals(2, sections.size)
        assertEquals("An abstract.", sections[0].content)
        assertEquals("", sections[0].title)
        assertEquals("Introduction", sections[1].title)
    }

    /** A document with no heading at all becomes a single section. */
    @Test
    fun aDocumentWithNoHeadingIsOneSection() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b1",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "Just prose.",
                    runs = listOf(WordRun(text = "Just prose.")),
                ),
            ),
        )
        val sections = sectionsWithOrigin(document)
        assertEquals(1, sections.size)
        assertEquals("Just prose.", sections[0].content)
    }

    /**
     * A Word document is imported as a book, and the sections carry the blocks
     * they were made of.
     */
    @Test
    fun aWordDocumentIsImportedAsABook() {
        val document = WordDocument(
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
        val manifest = importWordDocument(store, "book_01", "A book", document)
        assertEquals(1, manifest.sectionCount)
        assertEquals("Introduction", manifest.sections[0].title)
        assertTrue(store.readSectionRecord("book_01", "section_0001").origin != null)
        assertTrue(store.readBookOrigin("book_01") != null)
    }

    /**
     * A `.docx` on disk is imported through the Word reader.
     */
    @Test
    fun aDocxOnDiskIsImported() {
        val document = WordDocument(
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
        val directory = Files.createTempDirectory("lmeditor-docx")
        val path = directory.resolve("book.docx")
        Files.write(path, WordWriter().write(document))

        val store = newStore()
        val manifest = importWordDocument(store, "book_01", "A book", path)
        assertEquals(1, manifest.sectionCount)
        assertEquals("The body of the introduction.", store.readSection("book_01", "section_0001"))
    }
}
