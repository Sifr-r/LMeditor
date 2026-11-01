package lmeditor.server

import lmeditor.core.LMeditorError
import lmeditor.core.Hashing
import lmeditor.core.StorageWriteError
import lmeditor.core.ValidationError
import lmeditor.core.json.Json
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.AccessDeniedException
import java.nio.file.FileSystemException
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.nio.file.StandardCopyOption.REPLACE_EXISTING
import java.nio.file.StandardOpenOption.WRITE

/**
 * Versioned source records stored independently of the bounded memory registry.
 * Source IDs are hashed, never resolved as paths. Writes flush a unique sibling
 * before atomic replacement; a failed replacement leaves the previous record.
 * This guarantees process-crash atomicity. Directory metadata cannot be flushed
 * portably on Windows/JDK, so this does not promise power-loss durability.
 * This archives extracted passages and metadata, not original upload bytes.
 */
class SourceArchive(root: Path) {
    val root: Path = root.normalize()
    private val monitor = Any()

    init {
        if (!root.isAbsolute) fail("open", "the root must be absolute")
        checkedRoot()
    }

    fun add(stored: StoredSource): StoredSource = synchronized(monitor) {
        operation("write", stored.sourceId) {
            validateId(stored.sourceId)
            if (stored.byteSize != null && stored.byteSize < 0) fail("write", "negative byte size", stored.sourceId)
            checkedRoot()
            Files.createDirectories(root)
            checkedRoot()
            val path = recordPath(stored.sourceId)
            checkedRecord(path)
            // Never overwrite a record from a newer format (or a corrupt one)
            // while pretending it was successfully opened by this writer.
            if (Files.exists(path, NOFOLLOW_LINKS)) readRecord(path, stored.sourceId)
            val bytes = Json.write(mapOf("schema_version" to MAX_SUPPORTED_VERSION, "source" to storedSourceToWire(stored)))
                .toByteArray(Charsets.UTF_8)
            if (bytes.size > MAX_RECORD_BYTES) fail("write", "record exceeds $MAX_RECORD_BYTES bytes", stored.sourceId)
            var temporary: Path? = null
            try {
                temporary = Files.createTempFile(root, ".source-", ".tmp")
                FileChannel.open(temporary, WRITE).use { channel ->
                    val buffer = ByteBuffer.wrap(bytes)
                    while (buffer.hasRemaining()) channel.write(buffer)
                    channel.force(true)
                }
                checkedRoot()
                checkedRecord(path)
                Files.move(temporary, path, ATOMIC_MOVE, REPLACE_EXISTING)
                temporary = null
            } catch (exc: Exception) {
                if (temporary != null) {
                    try {
                        Files.deleteIfExists(temporary)
                    } catch (cleanup: Exception) {
                        exc.addSuppressed(cleanup)
                    }
                }
                throw exc
            }
            stored
        }
    }

    fun get(sourceId: String): StoredSource? = synchronized(monitor) {
        operation("read", sourceId) {
            validateId(sourceId)
            checkedRoot()
            val path = recordPath(sourceId)
            checkedRecord(path)
            if (Files.notExists(path, NOFOLLOW_LINKS)) null else readRecord(path, sourceId)
        }
    }

    fun list(): List<StoredSource> = synchronized(monitor) {
        operation("list") {
            recordPaths().map { readRecord(it) }.sortedBy { it.addedAt }
        }
    }

    fun remove(sourceId: String): Boolean = synchronized(monitor) {
        operation("remove", sourceId) {
            validateId(sourceId)
            checkedRoot()
            val path = recordPath(sourceId)
            checkedRecord(path)
            Files.deleteIfExists(path)
        }
    }

    /** Remove archive-owned records only; unrelated files are never deleted. */
    fun clear(): Unit = synchronized(monitor) {
        operation("clear") {
            val paths = recordPaths()
            paths.forEach { checkedRecord(it) }
            paths.forEach { Files.deleteIfExists(it) }
        }
    }

    private fun recordPaths(): List<Path> {
        checkedRoot()
        if (Files.notExists(root, NOFOLLOW_LINKS)) return emptyList()
        return Files.list(root).use { paths ->
            paths.filter { RECORD_NAME.matches(it.fileName.toString()) }.sorted().toList()
        }
    }

    private fun readRecord(path: Path, requestedId: String? = null): StoredSource = try {
        decodeRecord(path, requestedId)
    } catch (exc: ValidationError) {
        throw ValidationError(
            "Invalid archived source '${path.fileName}': ${exc.message}",
            field = "source_archive",
            value = mapOf("root" to root.toString(), "source_id" to requestedId, "path" to path.toString()),
        ).also { it.initCause(exc) }
    }

    private fun decodeRecord(path: Path, requestedId: String?): StoredSource {
        checkedRecord(path)
        if (Files.size(path) > MAX_RECORD_BYTES) fail("read", "record exceeds $MAX_RECORD_BYTES bytes", requestedId)
        val envelope = Json.parseObject(Files.readString(path, Charsets.UTF_8))
        val version = envelope["schema_version"]
        if (version != MIN_SUPPORTED_VERSION) {
            fail("read", "unsupported schema version '$version' in ${path.fileName}", requestedId)
        }
        val raw = envelope["source"]
        if (raw !is Map<*, *> || raw.keys.any { it !is String }) {
            fail("read", "invalid source record in ${path.fileName}", requestedId)
        }
        @Suppress("UNCHECKED_CAST")
        val stored = storedSourceFromWire(raw as Map<String, Any?>)
        validateId(stored.sourceId)
        if (stored.byteSize != null && stored.byteSize < 0) fail("read", "negative byte size", stored.sourceId)
        if (recordPath(stored.sourceId) != path || (requestedId != null && stored.sourceId != requestedId)) {
            fail("read", "record identity does not match ${path.fileName}", requestedId)
        }
        return stored
    }

    private fun validateId(sourceId: String) {
        if (sourceId.isBlank() || sourceId.length > 4096 || sourceId.any { it.isISOControl() }) {
            fail("validate", "source ID must be nonblank, at most 4096 characters and contain no controls", sourceId)
        }
    }

    private fun recordPath(sourceId: String): Path {
        return root.resolve("${Hashing.sha256Hex(sourceId)}.json")
    }

    private fun checkedRoot() {
        var path: Path? = root
        while (path != null) {
            if (Files.isSymbolicLink(path)) fail("open", "symlink archive paths are not allowed: $path")
            if (Files.exists(path, NOFOLLOW_LINKS) && !Files.isDirectory(path, NOFOLLOW_LINKS)) {
                fail("open", "archive path is not a directory: $path")
            }
            path = path.parent
        }
    }

    private fun checkedRecord(path: Path) {
        if (Files.isSymbolicLink(path)) fail("access", "symlink records are not allowed: ${path.fileName}")
        if (Files.exists(path, NOFOLLOW_LINKS) && !Files.isRegularFile(path, NOFOLLOW_LINKS)) {
            fail("access", "record is not a regular file: ${path.fileName}")
        }
    }

    private inline fun <T> operation(name: String, sourceId: String? = null, block: () -> T): T =
        try {
            block()
        } catch (exc: LMeditorError) {
            throw exc
        } catch (exc: Exception) {
            val error = if (name in setOf("write", "remove", "clear") && (exc is IOException || exc is SecurityException)) {
                val reason = when {
                    exc is AccessDeniedException || exc is SecurityException -> "permission_denied"
                    exc is FileSystemException && listOf("space", "disk full").any { exc.reason.orEmpty().contains(it, ignoreCase = true) } -> "disk_full"
                    else -> "io_error"
                }
                StorageWriteError(root.toString(), reason)
            } else {
                LMeditorError(
                    "Could not $name source archive: ${exc.message ?: exc.javaClass.simpleName}",
                    mapOf("operation" to name, "root" to root.toString(), "source_id" to sourceId),
                )
            }
            throw error.also {
                it.initCause(exc)
            }
        }

    private fun fail(operation: String, reason: String, sourceId: String? = null): Nothing =
        throw ValidationError(
            "Could not $operation source archive: $reason",
            field = "source_archive",
            value = mapOf("root" to root.toString(), "source_id" to sourceId),
        )

    companion object {
        const val MIN_SUPPORTED_VERSION: String = "1.0.0"
        const val MAX_SUPPORTED_VERSION: String = "1.0.0"
        private const val MAX_RECORD_BYTES: Int = 128 * 1024 * 1024
        private val RECORD_NAME = Regex("[0-9a-f]{64}\\.json")
    }
}
