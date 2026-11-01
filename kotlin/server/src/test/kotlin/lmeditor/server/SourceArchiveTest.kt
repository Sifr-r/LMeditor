package lmeditor.server

import lmeditor.core.BibliographicRecord
import lmeditor.core.SourceChunk
import lmeditor.core.SourceKind
import lmeditor.core.ValidationError
import lmeditor.core.StorageWriteError
import org.junit.jupiter.api.Assumptions.assumeTrue
import java.io.IOException
import java.nio.channels.FileChannel
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption.WRITE
import java.time.Instant
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class SourceArchiveTest {
    private fun source(id: String, second: Long = 0): StoredSource {
        val text = "A café has evidence for $id."
        return StoredSource(
            sourceId = id,
            title = "Research $id",
            kind = SourceKind.TEXT,
            origin = "https://example.test/$id",
            chunks = listOf(SourceChunk.create(
                documentId = id, text = text,
                charStart = 0, charEnd = text.length,
                byteStart = 0, byteEnd = text.toByteArray(Charsets.UTF_8).size,
            )),
            record = BibliographicRecord(title = "Research $id", authors = listOf("A. Writer"), year = 2026),
            byteSize = 240,
            omissions = listOf("Original image not extracted"),
            addedAt = Instant.EPOCH.plusSeconds(second),
        )
    }

    private fun withRoot(block: (Path) -> Unit) {
        val root = Files.createTempDirectory("lmeditor-source-archive-")
        try {
            block(root)
        } finally {
            Files.walk(root).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach { Files.deleteIfExists(it) } }
        }
    }

    private fun recordFile(root: Path): Path = Files.list(root).use { paths ->
        paths.filter { it.fileName.toString().endsWith(".json") }.findFirst().orElseThrow()
    }

    @Test
    fun `fresh archive reconstructs full source and evidence`() = withRoot { root ->
        val original = source("paper")
        SourceArchive(root).add(original)

        assertEquals(original, SourceArchive(root).get("paper"))
        assertEquals(listOf(original), SourceArchive(root).list())
        assertTrue(Files.readString(recordFile(root)).contains("\"schema_version\":\"1.0.0\""))
    }

    @Test
    fun `cache eviction retains all archived sources and chunks`() = withRoot { root ->
        val library = SourceLibrary(maxSources = 1, archive = SourceArchive(root))
        val first = source("first")
        val second = source("second", 1)
        library.add(first)
        library.add(second)

        assertEquals(2, library.size)
        assertEquals(first, library.get("first"))
        assertEquals(second.chunks + first.chunks, library.chunksFor(listOf("second", "missing", "first")))
        assertEquals(listOf(first, second), SourceLibrary(archive = SourceArchive(root)).list())
    }

    @Test
    fun `archive lists by addition timestamp across reconstruction`() = withRoot { root ->
        val archive = SourceArchive(root)
        archive.add(source("later", 10))
        archive.add(source("earlier", 1))
        assertEquals(listOf("earlier", "later"), SourceArchive(root).list().map { it.sourceId })
    }

    @Test
    fun `writing identical record twice keeps identical bytes`() = withRoot { root ->
        val archive = SourceArchive(root)
        val original = source("paper")
        archive.add(original)
        val before = Files.readAllBytes(recordFile(root)).toList()
        archive.add(original)
        assertEquals(before, Files.readAllBytes(recordFile(root)).toList())
        assertEquals(1, archive.list().size)
        assertEquals(1L, Files.list(root).use { it.count() })
    }

    @Test
    fun `unsafe path characters in IDs are hashed inside root`() = withRoot { root ->
        val original = source("../../other\\source: café")
        val archive = SourceArchive(root)
        archive.add(original)
        assertEquals(original, archive.get(original.sourceId))
        val path = recordFile(root)
        assertEquals(root, path.parent)
        assertTrue(Regex("[0-9a-f]{64}\\.json").matches(path.fileName.toString()))
    }

    @Test
    fun `invalid identifiers and relative roots are rejected`() = withRoot { root ->
        assertFailsWith<ValidationError> { SourceArchive(Path.of("relative")) }
        val archive = SourceArchive(root)
        listOf("", "  ", "bad\u0000id", "x".repeat(4097)).forEach { id ->
            assertFailsWith<ValidationError> { archive.add(source(id)) }
        }
        assertTrue(archive.list().isEmpty())
    }

    @Test
    fun `corrupt persisted data is reported even with a warm cache`() = withRoot { root ->
        val library = SourceLibrary(archive = SourceArchive(root))
        library.add(source("paper"))
        Files.writeString(recordFile(root), "{broken")
        assertFailsWith<ValidationError> { library.get("paper") }
        assertFailsWith<ValidationError> { library.list() }
        assertFailsWith<ValidationError> { library.add(source("paper")) }
        assertEquals("{broken", Files.readString(recordFile(root)))
    }

    @Test
    fun `unsupported schema cannot be mistaken for an empty archive`() = withRoot { root ->
        val archive = SourceArchive(root)
        archive.add(source("paper"))
        val path = recordFile(root)
        Files.writeString(path, Files.readString(path).replace("1.0.0", "2.0.0"))
        val error = assertFailsWith<ValidationError> { SourceArchive(root).get("paper") }
        assertTrue(error.message.contains("unsupported schema"))
        assertFailsWith<ValidationError> { archive.list() }
        val before = Files.readString(path)
        assertFailsWith<ValidationError> { archive.add(source("paper")) }
        assertEquals(before, Files.readString(path))
    }

    @Test
    fun `record identity cannot be changed under another hashed filename`() = withRoot { root ->
        val archive = SourceArchive(root)
        archive.add(source("paper"))
        val path = recordFile(root)
        Files.writeString(path, Files.readString(path).replace("\"source_id\":\"paper\"", "\"source_id\":\"impostor\""))
        assertFailsWith<ValidationError> { archive.get("paper") }
        assertFailsWith<ValidationError> { archive.list() }
    }

    @Test
    fun `removal persists and is idempotent`() = withRoot { root ->
        val library = SourceLibrary(archive = SourceArchive(root))
        library.add(source("paper"))
        assertTrue(library.remove("paper"))
        assertFalse(library.remove("paper"))
        assertNull(SourceArchive(root).get("paper"))
        assertEquals(0, library.size)
    }

    @Test
    fun `failed replacement preserves previous archived source`() = withRoot { root ->
        val library = SourceLibrary(archive = SourceArchive(root))
        val original = source("paper")
        library.add(original)
        assertFailsWith<ValidationError> { library.add(original.copy(byteSize = -1, title = "Rejected")) }
        assertEquals(original, library.get("paper"))
        assertEquals(original, SourceArchive(root).get("paper"))
    }

    @Test
    fun `clear only deletes owned records and persists`() = withRoot { root ->
        val archive = SourceArchive(root)
        archive.add(source("paper"))
        val unrelated = root.resolve("operator-notes.txt")
        Files.writeString(unrelated, "keep")
        val library = SourceLibrary(archive = archive)
        library.clear()
        library.clear()
        assertEquals(emptyList(), SourceArchive(root).list())
        assertEquals("keep", Files.readString(unrelated))
    }

    @Test
    fun `a directory at a record location is a visible failure`() = withRoot { root ->
        val archive = SourceArchive(root)
        archive.add(source("paper"))
        val path = recordFile(root)
        Files.delete(path)
        Files.createDirectory(path)
        assertFailsWith<ValidationError> { archive.add(source("paper")) }
        assertFailsWith<ValidationError> { archive.get("paper") }
    }

    @Test
    fun `symlink records are never read or overwritten`() = withRoot { root ->
        val archive = SourceArchive(root.resolve("archive"))
        archive.add(source("paper"))
        val path = recordFile(archive.root)
        val original = Files.readString(path)
        val outside = root.resolve("outside.json")
        Files.writeString(outside, original)
        Files.delete(path)
        val supported = try {
            Files.createSymbolicLink(path, outside)
            true
        } catch (exc: IOException) {
            false
        } catch (exc: UnsupportedOperationException) {
            false
        }
        assumeTrue(supported, "symbolic link privilege is unavailable on this machine")
        assertFailsWith<ValidationError> { archive.get("paper") }
        assertFailsWith<ValidationError> { archive.add(source("paper")) }
        assertFailsWith<ValidationError> { archive.remove("paper") }
        assertEquals(original, Files.readString(outside))
    }

    @Test
    fun `Windows mandatory file lock produces typed write failure without losing old record`() = withRoot { root ->
        assumeTrue(System.getProperty("os.name").startsWith("Windows"), "mandatory file locks are a Windows guarantee")
        val library = SourceLibrary(archive = SourceArchive(root))
        val original = source("paper")
        library.add(original)
        FileChannel.open(recordFile(root), WRITE).use { channel ->
            channel.lock().use {
                val error = assertFailsWith<StorageWriteError> { library.add(original.copy(title = "Rejected")) }
                assertTrue(error.details["reason"] in listOf("permission_denied", "io_error"))
            }
        }
        assertEquals(original, library.get("paper"))
        assertEquals(original, SourceArchive(root).get("paper"))
    }

    @Test
    fun `rootless registry keeps existing bounded working set behavior`() {
        val library = SourceLibrary(maxSources = 1)
        library.add(source("old"))
        library.add(source("new", 1))
        assertNull(library.get("old"))
        assertNotNull(library.get("new"))
        assertEquals(1, library.size)
    }
}
