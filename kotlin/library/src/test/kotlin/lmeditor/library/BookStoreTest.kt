package lmeditor.library

import lmeditor.core.BookNotFoundError
import lmeditor.core.LibraryError
import lmeditor.core.SectionNotFoundError
import lmeditor.core.SourceChunk
import lmeditor.core.json.Json
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The on-disk store, ported from `backend/tests/test_library.py`.
 */
class BookStoreTest {

    /** A book is created, listed and opened. */
    @Test
    fun aBookIsCreatedListedAndOpened() {
        val store = newStore()
        val manifest = store.create("book_01", "A book")
        assertEquals("book_01", manifest.bookId)
        assertEquals("A book", manifest.title)
        assertEquals(0, manifest.sectionCount)

        assertEquals(listOf("book_01"), store.listBooks().map { it.bookId })
        assertEquals("A book", store.open("book_01").title)
    }

    /** The layout is the layout the Python store uses. */
    @Test
    fun theLayoutIsTheOneThePythonStoreUses() {
        val root = newRoot()
        val store = BookStore(root)
        store.create("book_01", "A book")
        store.addSection("book_01", "section_0000", "The body of the first section.")
        store.putChunks("book_01", listOf(chunk("chunk_01")))
        store.setBookOrigin("book_01", mapOf("headers" to listOf("a header")))

        val bookDir = root.resolve("book_01")
        assertTrue(Files.isRegularFile(bookDir.resolve(MANIFEST_FILENAME)))
        assertTrue(Files.isRegularFile(bookDir.resolve(SECTIONS_DIRNAME).resolve("section_0000.json")))
        assertTrue(Files.isRegularFile(bookDir.resolve(CHUNKS_FILENAME)))
        assertTrue(Files.isRegularFile(bookDir.resolve(ORIGIN_FILENAME)))
    }

    /**
     * The manifest is the JSON document the Python tier writes.
     *
     * The keys and their order are pinned here: a book written by one tier has
     * to be found by the other, and that means the manifest has to look the
     * same to both.
     */
    @Test
    fun theManifestIsTheJsonThePythonTierWrites() {
        val root = newRoot()
        val store = BookStore(root)
        store.create("book_01", "A book")
        store.addSection("book_01", "section_0000", "Two words", title = "Intro")

        val text = Files.readString(root.resolve("book_01").resolve(MANIFEST_FILENAME))
        val payload = Json.parseObject(text)
        assertEquals(
            listOf(
                "book_id",
                "title",
                "section_count",
                "chunk_count",
                "created_at",
                "modified_at",
                "sections",
                "brief",
            ),
            payload.keys.toList(),
        )
        assertEquals("book_01", payload["book_id"])
        assertEquals("A book", payload["title"])
        assertEquals(1L, payload["section_count"])
        assertEquals("", payload["brief"])

        @Suppress("UNCHECKED_CAST")
        val sections = payload["sections"] as List<Map<String, Any?>>
        assertEquals(
            listOf("section_id", "title", "order", "word_count", "char_count", "chunk_ids"),
            sections[0].keys.toList(),
        )
        assertEquals("section_0000", sections[0]["section_id"])
        assertEquals("Intro", sections[0]["title"])
        assertEquals(2L, sections[0]["word_count"])
        assertEquals(9L, sections[0]["char_count"])
    }

    /** The section file holds the same keys the Python tier writes. */
    @Test
    fun theSectionFileIsTheJsonThePythonTierWrites() {
        val root = newRoot()
        val store = BookStore(root)
        store.create("book_01", "A book")
        store.addSection("book_01", "section_0000", "The body.")

        val text = Files.readString(root.resolve("book_01").resolve(SECTIONS_DIRNAME).resolve("section_0000.json"))
        val payload = Json.parseObject(text)
        assertEquals(
            listOf(
                "section_id",
                "title",
                "order",
                "word_count",
                "char_count",
                "content",
                "origin",
            ),
            payload.keys.toList(),
        )
        assertEquals("The body.", payload["content"])
    }

    /** A section is added, read and written back. */
    @Test
    fun aSectionIsAddedReadAndWritten() {
        val store = newStore()
        store.create("book_01", "A book")
        val section = store.addSection("book_01", "section_0000", "The body.", title = "Intro")
        assertEquals("section_0000", section.sectionId)
        assertEquals("Intro", section.title)
        assertEquals(2, section.wordCount)
        assertEquals(9, section.charCount)

        assertEquals("The body.", store.readSection("book_01", "section_0000"))

        val written = store.writeSection("book_01", "section_0000", "A longer body.")
        assertEquals(3, written.wordCount)
        assertEquals("A longer body.", store.readSection("book_01", "section_0000"))
    }

    /**
     * Writing the same section twice leaves the same bytes.
     *
     * The section file is written atomically and its content depends only on
     * the body it holds, so a write that changes nothing changes nothing on
     * disk.
     */
    @Test
    fun writingTheSameSectionTwiceLeavesTheSameBytes() {
        val root = newRoot()
        val store = BookStore(root)
        store.create("book_01", "A book")
        store.addSection("book_01", "section_0000", "The body.")
        val path = root.resolve("book_01").resolve(SECTIONS_DIRNAME).resolve("section_0000.json")
        val first = Files.readAllBytes(path)

        store.writeSection("book_01", "section_0000", "The body.")
        val second = Files.readAllBytes(path)
        assertTrue(first.contentEquals(second))
    }

    /** The chunks of a book are stored and read back. */
    @Test
    fun theChunksAreStoredAndReadBack() {
        val store = newStore()
        store.create("book_01", "A book")
        store.putChunks("book_01", listOf(chunk("chunk_01"), chunk("chunk_02")))
        val chunks = store.chunks("book_01")
        assertEquals(2, chunks.size)
        assertEquals(hexId("chunk_01"), chunks[0].chunkId)
        assertEquals("Some text.", chunks[0].text)
        assertEquals(2, store.open("book_01").chunkCount)
    }

    /**
     * Storing the same chunk twice does not duplicate it.
     *
     * A chunk id is a hash of the chunk's text and its offsets, so the same
     * chunk indexed twice is the same chunk.
     */
    @Test
    fun theSameChunkIsStoredOnce() {
        val store = newStore()
        store.create("book_01", "A book")
        store.putChunks("book_01", listOf(chunk("chunk_01")))
        store.putChunks("book_01", listOf(chunk("chunk_01"), chunk("chunk_02")))
        assertEquals(2, store.chunks("book_01").size)
    }

    /** The chunk file holds the JSON the Python tier writes, one object per line. */
    @Test
    fun theChunkFileIsTheJsonThePythonTierWrites() {
        val root = newRoot()
        val store = BookStore(root)
        store.create("book_01", "A book")
        store.putChunks("book_01", listOf(chunk("chunk_01")))
        val text = Files.readString(root.resolve("book_01").resolve(CHUNKS_FILENAME))
        val lines = text.trim().lines()
        assertEquals(1, lines.size)
        val payload = Json.parseObject(lines[0])
        assertEquals(
            listOf(
                "chunk_id",
                "document_id",
                "text",
                "char_start",
                "char_end",
                "byte_start",
                "byte_end",
                "token_count",
                "start_char",
                "end_char",
                "metadata",
            ),
            payload.keys.toList(),
        )
    }

    /** A book that is not there is reported as missing. */
    @Test
    fun aMissingBookIsReportedAsMissing() {
        val store = newStore()
        assertFailsWith<BookNotFoundError> { store.open("book_404") }
        assertFailsWith<BookNotFoundError> { store.readSection("book_404", "section_0000") }
    }

    /** A section that is not there is reported as missing. */
    @Test
    fun aMissingSectionIsReportedAsMissing() {
        val store = newStore()
        store.create("book_01", "A book")
        assertFailsWith<SectionNotFoundError> { store.readSection("book_01", "section_404") }
    }

    /** An unsafe book id is refused before anything is touched. */
    @Test
    fun anUnsafeBookIdIsRefused() {
        val store = newStore()
        assertFailsWith<UnsafeIdentifierError> { store.create("../../evil", "A book") }
        assertFailsWith<UnsafeIdentifierError> { store.open("a/b") }
    }

    /** An unsafe section id is refused. */
    @Test
    fun anUnsafeSectionIdIsRefused() {
        val store = newStore()
        store.create("book_01", "A book")
        assertFailsWith<UnsafeIdentifierError> {
            store.addSection("book_01", "../evil", "body")
        }
    }

    /** A negative section order is refused. */
    @Test
    fun aNegativeSectionOrderIsRefused() {
        val store = newStore()
        store.create("book_01", "A book")
        assertFailsWith<LibraryError> {
            store.addSection("book_01", "section_0000", "body", order = -1)
        }
    }

    /** A book is deleted, and it is gone. */
    @Test
    fun aBookIsDeleted() {
        val root = newRoot()
        val store = BookStore(root)
        store.create("book_01", "A book")
        store.addSection("book_01", "section_0000", "The body.")
        store.delete("book_01")
        assertTrue(Files.notExists(root.resolve("book_01")))
        assertEquals(emptyList(), store.listBooks())
    }

    /** The origin of a book is stored and read back. */
    @Test
    fun theOriginIsStoredAndReadBack() {
        val store = newStore()
        store.create("book_01", "A book")
        assertEquals(null, store.readBookOrigin("book_01"))
        store.setBookOrigin("book_01", mapOf("headers" to listOf("a header")))
        assertEquals(listOf("a header"), store.readBookOrigin("book_01")!!["headers"])
    }

    /** The brief of a book is stored and read back. */
    @Test
    fun theBriefIsStoredAndReadBack() {
        val store = newStore()
        store.create("book_01", "A book")
        assertEquals("", store.brief("book_01"))
        store.updateBrief("book_01", "  A digest of the book.  ")
        assertEquals("A digest of the book.", store.brief("book_01"))
    }

    /** The ids that are not safe path segments are refused. */
    @Test
    fun theUnsafeIdentifiersAreRefused() {
        val unsafe = listOf("", "   ", "../evil", "a/b", "a\\b", "a:b", "with\u0000nul", "x".repeat(129), ".", "..")
        for (id in unsafe) {
            assertFailsWith<UnsafeIdentifierError>("'$id' must be refused") {
                validateIdentifier(id, "book_id")
            }
        }
        for (id in listOf("src_url_api", "a.b.c", "paper-2024", "x".repeat(128))) {
            assertEquals(id, validateIdentifier(id, "book_id"))
        }
    }
}

/** Return a store over a fresh temporary directory. */
internal fun newStore(): BookStore = BookStore(newRoot())

/** Return a fresh temporary directory. */
internal fun newRoot(): Path = Files.createTempDirectory("lmeditor-library")

/** Return a chunk with the given id. */
internal fun chunk(id: String): SourceChunk = SourceChunk(
    chunkId = hexId(id),
    documentId = "doc_01",
    text = "Some text.",
    charStart = 0,
    charEnd = 10,
    byteStart = 0,
    byteEnd = 10,
    tokenCount = 2,
    startChar = 0,
    endChar = 10,
    metadata = mapOf("document_title" to "A book"),
)

/** Return the SHA-256 digest of [name] as the lowercase hex a chunk id is. */
internal fun hexId(name: String): String =
    java.security.MessageDigest.getInstance("SHA-256")
        .digest(name.toByteArray())
        .joinToString("") { "%02x".format(it) }
