package lmeditor.ingest

import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.FileTime

/**
 * Persists uploaded sources under a total-bytes budget.
 *
 * The store owns one directory. Every file it writes lives under that
 * directory, and the path it writes to is *proven* to stay inside it, so a
 * client-supplied file name cannot escape the store.
 *
 * The budget is enforced by evicting the least recently used files, so the
 * store never grows past [maxBytes] however many uploads it is handed.
 */
class SourceStore(
    root: Path? = null,
    /** The total-bytes budget of the store. */
    val maxBytes: Long = MAX_SOURCE_STORE_BYTES,
) {
    /** The directory the store writes into. */
    val root: Path = (root ?: uploadRoot()).toAbsolutePath().normalize()

    /**
     * Return a store rooted at the configured upload directory.
     */
    companion object {
        /** Return a store rooted at the configured upload directory. */
        fun default(): SourceStore = SourceStore(uploadRoot())
    }

    /**
     * Return the path an upload of [filename] is stored at.
     *
     * The path is proven to stay inside the store's root by [safeUploadPath],
     * so a client-supplied filename cannot escape it.
     */
    fun pathFor(sourceId: String, filename: String): Path =
        safeUploadPath(sourceId, filename, base = root)

    /**
     * Persist [content] and evict old files until the store fits its budget.
     *
     * @throws lmeditor.core.ValidationError the content is larger than the
     *   upload cap.
     * @throws UnsafeSourceError the source id or the filename is not safe.
     */
    fun save(sourceId: String, filename: String, content: ByteArray): Path {
        validateUploadSize(content.size)
        val path = pathFor(sourceId, filename)
        Files.createDirectories(path.parent)
        Files.write(path, content)
        evict()
        return path
    }

    /** Return the number of bytes the store currently holds. */
    fun totalBytes(): Long = listFiles().sumOf { sizeOf(it) }

    /**
     * Return every stored file, oldest first.
     *
     * "Oldest" is the modification time: a file written earlier is the one the
     * budget reclaims first.
     */
    fun listFiles(): List<Path> {
        if (!Files.isDirectory(root)) {
            return emptyList()
        }
        val files = mutableListOf<Path>()
        Files.walk(root).use { stream ->
            stream.forEach { path ->
                if (path != root && Files.isRegularFile(path)) {
                    files.add(path)
                }
            }
        }
        files.sortWith(compareBy({ mtimeOf(it) }, { it.toString() }))
        return files
    }

    /**
     * Delete the oldest files until the store fits its budget.
     *
     * @return the paths that were deleted.
     */
    fun evict(): List<Path> {
        val files = listFiles()
        var total = 0L
        val sizes = LinkedHashMap<Path, Long>()
        for (path in files) {
            val size = sizeOf(path)
            sizes[path] = size
            total += size
        }
        val removed = mutableListOf<Path>()
        for (path in files) {
            if (total <= maxBytes) {
                break
            }
            try {
                Files.deleteIfExists(path)
            } catch (exc: IOException) {
                // The file could not be reclaimed; it stays and the budget
                // check simply tries the next one.
                continue
            }
            total -= sizes[path] ?: 0L
            removed.add(path)
        }
        if (removed.isNotEmpty()) {
            pruneEmptyDirectories()
        }
        return removed
    }

    /**
     * Mark a stored file as recently used and return its path.
     *
     * @return null when the file is not in the store.
     */
    fun touch(sourceId: String, filename: String): Path? {
        val path = pathFor(sourceId, filename)
        if (!Files.isRegularFile(path)) {
            return null
        }
        try {
            Files.setLastModifiedTime(path, FileTime.from(java.time.Instant.now()))
        } catch (exc: IOException) {
            // The timestamp could not be updated; the file is still there.
        }
        return path
    }

    /**
     * Remove every file stored for [sourceId].
     *
     * @return true when something was removed.
     */
    fun remove(sourceId: String): Boolean {
        val directory = root.resolve(validateSourceId(sourceId)).normalize()
        if (!directory.startsWith(root) || !Files.isDirectory(directory)) {
            return false
        }
        var removed = false
        Files.walk(directory).use { stream ->
            stream.forEach { path ->
                if (path != directory && Files.isRegularFile(path)) {
                    try {
                        Files.deleteIfExists(path)
                        removed = true
                    } catch (exc: IOException) {
                        // The file stays; the removal is reported as partial.
                    }
                }
            }
        }
        pruneEmptyDirectories()
        return removed
    }

    /** Remove the directories a removal left empty. */
    private fun pruneEmptyDirectories() {
        if (!Files.isDirectory(root)) {
            return
        }
        val directories = mutableListOf<Path>()
        Files.walk(root).use { stream ->
            stream.forEach { path ->
                if (path != root && Files.isDirectory(path)) {
                    directories.add(path)
                }
            }
        }
        // Deepest first, so a directory that only holds an empty directory can
        // be removed too.
        directories.sortByDescending { it.nameCount }
        for (directory in directories) {
            try {
                Files.deleteIfExists(directory)
            } catch (exc: IOException) {
                // The directory is not empty, or cannot be removed.
                continue
            }
        }
    }

    /** Return a path's size in bytes, or 0 when it cannot be read. */
    private fun sizeOf(path: Path): Long = try {
        Files.size(path)
    } catch (exc: IOException) {
        0L
    }

    /** Return a path's modification time, or 0 when it cannot be read. */
    private fun mtimeOf(path: Path): Long = try {
        Files.getLastModifiedTime(path).toMillis()
    } catch (exc: IOException) {
        0L
    }
}
