package lmeditor.ingest

import lmeditor.core.ValidationError
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The upload limits and the persisted-upload store, ported from
 * `backend/tests/test_ingestion_limits.py` and
 * `backend/tests/test_ingestion_source_store.py`.
 */
class LimitsAndStoreTest {

    /** A negative size is refused. */
    @Test
    fun aNegativeUploadSizeIsRefused() {
        val failure = assertFailsWith<ValidationError> { validateUploadSize(-1) }
        assertEquals("size", failure.field)
    }

    /** A size over the cap is refused. */
    @Test
    fun anOversizedUploadIsRefused() {
        val failure = assertFailsWith<ValidationError> {
            validateUploadSize(MAX_UPLOAD_BYTES + 1)
        }
        assertEquals("size", failure.field)
    }

    /** A size that fits is returned unchanged. */
    @Test
    fun anAcceptableUploadSizeIsReturnedUnchanged() {
        assertEquals(0, validateUploadSize(0))
        assertEquals(MAX_UPLOAD_BYTES, validateUploadSize(MAX_UPLOAD_BYTES))
    }

    /** An upload is stored under the source's own directory. */
    @Test
    fun anUploadIsStoredUnderTheSourcesDirectory() {
        val root = Files.createTempDirectory("lmeditor-store")
        val store = SourceStore(root)
        val path = store.save("src_01", "notes.txt", "payload".toByteArray())
        assertTrue(path.startsWith(root.toAbsolutePath().normalize()))
        assertEquals("payload", String(Files.readAllBytes(path)))
        assertEquals(1, store.listFiles().size)
    }

    /** Writing the same upload twice leaves the same bytes. */
    @Test
    fun writingTheSameUploadTwiceLeavesTheSameBytes() {
        val root = Files.createTempDirectory("lmeditor-store")
        val store = SourceStore(root)
        val first = store.save("src_01", "notes.txt", "payload".toByteArray())
        val firstBytes = Files.readAllBytes(first)
        val second = store.save("src_01", "notes.txt", "payload".toByteArray())
        assertEquals(first, second)
        assertTrue(firstBytes.contentEquals(Files.readAllBytes(second)))
    }

    /** The store evicts the oldest files until it fits its budget. */
    @Test
    fun theStoreEvictsTheOldestFilesUntilItFitsItsBudget() {
        val root = Files.createTempDirectory("lmeditor-store")
        val store = SourceStore(root, maxBytes = 10)
        store.save("src_01", "a.txt", "0123456789".toByteArray())
        store.save("src_02", "b.txt", "0123456789".toByteArray())
        assertTrue(store.totalBytes() <= 10)
        assertEquals(1, store.listFiles().size)
    }

    /** Removing a source removes every file stored for it. */
    @Test
    fun removingASourceRemovesItsFiles() {
        val root = Files.createTempDirectory("lmeditor-store")
        val store = SourceStore(root)
        store.save("src_01", "a.txt", "a".toByteArray())
        store.save("src_01", "b.txt", "b".toByteArray())
        store.save("src_02", "c.txt", "c".toByteArray())
        assertTrue(store.remove("src_01"))
        assertEquals(1, store.listFiles().size)
        assertTrue(Files.notExists(root.resolve("src_01").resolve("a.txt")))
    }

    /** An unsafe source id is refused before anything is written. */
    @Test
    fun anUnsafeSourceIdIsRefusedBeforeAnythingIsWritten() {
        val root = Files.createTempDirectory("lmeditor-store")
        val store = SourceStore(root)
        assertFailsWith<UnsafeSourceError> {
            store.save("../../evil", "notes.txt", "payload".toByteArray())
        }
        assertTrue(Files.notExists(root.resolve("evil")))
        assertEquals(0, store.listFiles().size)
    }

    /** The upload path of a source is under the store's root. */
    @Test
    fun theUploadPathIsUnderTheStoreRoot() {
        val root = Files.createTempDirectory("lmeditor-store")
        val store = SourceStore(root)
        val path = store.pathFor("src_01", "notes.txt")
        assertTrue(path.startsWith(root.toAbsolutePath().normalize().resolve("src_01")))
    }
}
