@file:Suppress("DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING")

package lmeditor.notebooks

import lmeditor.core.LMeditorError
import lmeditor.core.Notebook
import lmeditor.core.NotebookSummary
import lmeditor.core.json.Json
import lmeditor.core.notebookFromWire
import lmeditor.core.stripModelWhitespace
import lmeditor.core.toWireMap
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/** The file that holds a notebook's manifest. */
const val MANIFEST_FILENAME: String = "manifest.json"

/** The format a notebook's timestamps are written in. */
private val TIMESTAMP_FORMAT: DateTimeFormatter =
    DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'")

/**
 * The store of the notebooks: a folder of notebooks, one directory each.
 *
 * A notebook lives at `<root>/<notebook_id>/manifest.json`. The manifest is
 * the core `Notebook` model written as JSON -- the same shape the seam
 * sends -- so the file on disk and the answer over the seam can never
 * disagree, and the core tier's own decoder reads the file back.
 *
 * The store owns every deterministic rule of a notebook: what an id may be,
 * how one is derived from a title, what a manifest must contain, and when a
 * notebook may be created, changed or removed. It touches nothing else: the
 * sources a notebook names and the notes it holds are the operator's, and
 * they are not this store's to read, write or delete.
 */
class NotebookStore(
    /** The folder the notebooks live in. */
    val root: Path,
) {

    /**
     * Return every notebook, ordered by notebook id.
     *
     * A directory without a manifest is not a notebook and is skipped: the
     * root may hold whatever else the operator keeps beside the notebooks.
     * A manifest that cannot be read *is* an error, because a notebook the
     * store cannot read must not be silently missing from the list -- the
     * operator would believe a notebook is gone.
     *
     * @return the notebooks, ordered by id
     * @throws NotebookError a manifest could not be read
     */
    fun list(): List<NotebookSummary> {
        if (!Files.isDirectory(root)) {
            return emptyList()
        }
        val directories = try {
            Files.newDirectoryStream(root).use { stream ->
                stream.filter { Files.isDirectory(it) }.toList()
            }
        } catch (exc: IOException) {
            throw NotebookError(
                "could not read the notebooks folder '$root': ${exc.message}",
                mapOf("root" to root.toString()),
            )
        }
        return directories
            .sortedBy { it.fileName.toString() }
            .mapNotNull { directory ->
                val id = directory.fileName.toString()
                val manifest = directory.resolve(MANIFEST_FILENAME)
                if (!Files.isRegularFile(manifest)) {
                    null
                } else {
                    summaryOf(readManifest(manifest, id))
                }
            }
    }

    /**
     * Return the notebook with [notebookId].
     *
     * @param notebookId the notebook to read
     * @return the notebook
     * @throws UnsafeNotebookIdError the id is not a safe path segment
     * @throws NotebookNotFoundError there is no such notebook
     * @throws NotebookError the manifest could not be read
     */
    fun read(notebookId: String): Notebook {
        val id = NotebookPath.validateNotebookId(notebookId)
        val manifest = manifestPath(id)
        if (!Files.isRegularFile(manifest)) {
            throw NotebookNotFoundError(id)
        }
        return readManifest(manifest, id)
    }

    /**
     * Return the notebook with [notebookId], creating it when it is not there.
     *
     * The call is idempotent: a notebook that is already there is returned
     * unchanged, and its title is never overwritten. Creating a notebook
     * twice therefore never loses what the operator wrote.
     *
     * When no id is named, one is derived from the title, so the same title
     * always yields the same notebook. A derived id that is already taken by
     * a notebook of another title is suffixed -- `-2`, then `-3`, and so on
     * -- until it is free.
     *
     * @param notebookId the notebook to create, or null to derive the id from the title
     * @param title the title of the notebook
     * @param description what the notebook is about
     * @return the notebook, new or the one that was already there
     * @throws UnsafeNotebookIdError the named id is not a safe path segment
     * @throws NotebookError the manifest could not be written
     */
    fun create(notebookId: String?, title: String, description: String): Notebook {
        val named = notebookId?.takeIf { it.isNotBlank() }
        if (named != null) {
            val id = NotebookPath.validateNotebookId(named)
            val existing = existing(id)
            return existing ?: writeNew(id, title, description)
        }
        val base = NotebookPath.deriveNotebookId(title)
        val taken = existing(base)
        if (taken != null && taken.title == title.stripModelWhitespace()) {
            // The same title always yields the same notebook.
            return taken
        }
        return writeNew(freeDerivedId(base), title, description)
    }

    /**
     * Return the notebook with [notebookId] with the title and the
     * description it is given.
     *
     * @param notebookId the notebook to change
     * @param title the new title
     * @param description the new description
     * @return the changed notebook
     * @throws UnsafeNotebookIdError the id is not a safe path segment
     * @throws NotebookNotFoundError there is no such notebook
     * @throws NotebookError the manifest could not be written
     */
    fun setMetadata(notebookId: String, title: String, description: String): Notebook {
        val id = NotebookPath.validateNotebookId(notebookId)
        val notebook = read(id)
        val changed = notebook.copy(
            title = title.stripModelWhitespace(),
            description = description.stripModelWhitespace(),
            modifiedAt = nowTimestamp(),
        )
        writeManifest(changed)
        return changed
    }

    /**
     * Return the notebook with [notebookId] holding [sourceId].
     *
     * The call is idempotent: a notebook that already holds the source is
     * returned unchanged, and nothing is written.
     *
     * @param notebookId the notebook to change
     * @param sourceId the source to hold
     * @return the changed notebook
     * @throws UnsafeNotebookIdError the id is not a safe path segment
     * @throws NotebookNotFoundError there is no such notebook
     * @throws NotebookError the manifest could not be written
     */
    fun addSource(notebookId: String, sourceId: String): Notebook {
        val id = NotebookPath.validateNotebookId(notebookId)
        val source = sourceId.stripModelWhitespace()
        if (source.isEmpty()) {
            throw NotebookError(
                "a source id must not be blank",
                mapOf("notebook_id" to id, "source_id" to sourceId),
            )
        }
        val notebook = read(id)
        if (notebook.sourceIds.contains(source)) {
            return notebook
        }
        val changed = notebook.copy(
            sourceIds = notebook.sourceIds + source,
            modifiedAt = nowTimestamp(),
        )
        writeManifest(changed)
        return changed
    }

    /**
     * Return the notebook with [notebookId] no longer holding [sourceId].
     *
     * The call refuses when the notebook does not hold the source: a removal
     * that changed nothing would be a claim that something was removed when
     * it was not.
     *
     * @param notebookId the notebook to change
     * @param sourceId the source to let go of
     * @return the changed notebook
     * @throws UnsafeNotebookIdError the id is not a safe path segment
     * @throws NotebookNotFoundError there is no such notebook
     * @throws NotebookError the notebook does not hold the source
     */
    fun removeSource(notebookId: String, sourceId: String): Notebook {
        val id = NotebookPath.validateNotebookId(notebookId)
        val source = sourceId.stripModelWhitespace()
        if (source.isEmpty()) {
            throw NotebookError(
                "a source id must not be blank",
                mapOf("notebook_id" to id, "source_id" to sourceId),
            )
        }
        val notebook = read(id)
        if (!notebook.sourceIds.contains(source)) {
            throw NotebookError(
                "the notebook '$id' does not hold the source '$source'",
                mapOf("notebook_id" to id, "source_id" to source),
            )
        }
        val changed = notebook.copy(
            sourceIds = notebook.sourceIds - source,
            modifiedAt = nowTimestamp(),
        )
        writeManifest(changed)
        return changed
    }

    /**
     * Return the notebook with [notebookId] holding [noteId].
     *
     * The call is idempotent: a notebook that already holds the note is
     * returned unchanged, and nothing is written.
     *
     * @param notebookId the notebook to change
     * @param noteId the note to hold
     * @return the changed notebook
     * @throws UnsafeNotebookIdError the id is not a safe path segment
     * @throws NotebookNotFoundError there is no such notebook
     * @throws NotebookError the manifest could not be written
     */
    fun addNote(notebookId: String, noteId: String): Notebook {
        val id = NotebookPath.validateNotebookId(notebookId)
        val note = noteId.stripModelWhitespace()
        if (note.isEmpty()) {
            throw NotebookError(
                "a note id must not be blank",
                mapOf("notebook_id" to id, "note_id" to noteId),
            )
        }
        val notebook = read(id)
        if (notebook.noteIds.contains(note)) {
            return notebook
        }
        val changed = notebook.copy(
            noteIds = notebook.noteIds + note,
            modifiedAt = nowTimestamp(),
        )
        writeManifest(changed)
        return changed
    }

    /**
     * Return the notebook with [notebookId] no longer holding [noteId].
     *
     * The call refuses when the notebook does not hold the note: a removal
     * that changed nothing would be a claim that something was removed when
     * it was not.
     *
     * @param notebookId the notebook to change
     * @param noteId the note to let go of
     * @return the changed notebook
     * @throws UnsafeNotebookIdError the id is not a safe path segment
     * @throws NotebookNotFoundError there is no such notebook
     * @throws NotebookError the notebook does not hold the note
     */
    fun removeNote(notebookId: String, noteId: String): Notebook {
        val id = NotebookPath.validateNotebookId(notebookId)
        val note = noteId.stripModelWhitespace()
        if (note.isEmpty()) {
            throw NotebookError(
                "a note id must not be blank",
                mapOf("notebook_id" to id, "note_id" to noteId),
            )
        }
        val notebook = read(id)
        if (!notebook.noteIds.contains(note)) {
            throw NotebookError(
                "the notebook '$id' does not hold the note '$note'",
                mapOf("notebook_id" to id, "note_id" to note),
            )
        }
        val changed = notebook.copy(
            noteIds = notebook.noteIds - note,
            modifiedAt = nowTimestamp(),
        )
        writeManifest(changed)
        return changed
    }

    /**
     * Remove the notebook with [notebookId].
     *
     * Only the notebook's own directory is removed. The sources it names and
     * the notes it holds are the operator's and are left exactly where they
     * are.
     *
     * @param notebookId the notebook to remove
     * @throws UnsafeNotebookIdError the id is not a safe path segment
     * @throws NotebookNotFoundError there is no such notebook
     * @throws NotebookError the notebook could not be removed
     */
    fun delete(notebookId: String) {
        val id = NotebookPath.validateNotebookId(notebookId)
        val directory = notebookDirectory(id)
        if (!Files.isRegularFile(directory.resolve(MANIFEST_FILENAME))) {
            throw NotebookNotFoundError(id)
        }
        try {
            deleteRecursively(directory)
        } catch (exc: IOException) {
            throw NotebookError(
                "could not remove the notebook '$id': ${exc.message}",
                mapOf("notebook_id" to id, "path" to directory.toString()),
            )
        }
    }

    /** The directory a notebook lives in. */
    private fun notebookDirectory(notebookId: String): Path = root.resolve(notebookId)

    /** The manifest of a notebook. */
    private fun manifestPath(notebookId: String): Path =
        notebookDirectory(notebookId).resolve(MANIFEST_FILENAME)

    /** The notebook at [notebookId], or null when there is none. */
    private fun existing(notebookId: String): Notebook? {
        val manifest = manifestPath(notebookId)
        if (!Files.isRegularFile(manifest)) {
            return null
        }
        return readManifest(manifest, notebookId)
    }

    /** A derived id that is free: the base, then `-2`, then `-3`, and so on. */
    private fun freeDerivedId(base: String): String {
        var candidate = base
        var suffix = 2
        while (Files.isRegularFile(manifestPath(candidate))) {
            candidate = suffixed(base, suffix)
            suffix++
        }
        return candidate
    }

    /** [base] with the suffix [suffix], never longer than a safe id may be. */
    private fun suffixed(base: String, suffix: Int): String {
        val tail = "-$suffix"
        val head = if (base.length + tail.length > NotebookPath.MAX_ID_LENGTH) {
            base.take(NotebookPath.MAX_ID_LENGTH - tail.length).trimEnd('-')
        } else {
            base
        }
        return head + tail
    }

    /** Write a new notebook and return it. */
    private fun writeNew(notebookId: String, title: String, description: String): Notebook {
        val now = nowTimestamp()
        val notebook = Notebook(
            notebookId = notebookId,
            title = title.stripModelWhitespace(),
            description = description.stripModelWhitespace(),
            sourceIds = emptyList(),
            noteIds = emptyList(),
            createdAt = now,
            modifiedAt = now,
        )
        writeManifest(notebook)
        return notebook
    }

    /** The summary of a notebook. */
    private fun summaryOf(notebook: Notebook): NotebookSummary = NotebookSummary(
        notebookId = notebook.notebookId,
        title = notebook.title,
        description = notebook.description,
        sourceCount = notebook.sourceIds.size,
        noteCount = notebook.noteIds.size,
        modifiedAt = notebook.modifiedAt,
    )

    /**
     * Read the manifest at [path] as the notebook [notebookId].
     *
     * A manifest that cannot be read is an error, never a notebook that is
     * quietly not there.
     */
    private fun readManifest(path: Path, notebookId: String): Notebook {
        val text = try {
            Files.readString(path, StandardCharsets.UTF_8)
        } catch (exc: IOException) {
            throw NotebookError(
                "could not read the manifest of the notebook '$notebookId': ${exc.message}",
                mapOf("notebook_id" to notebookId, "path" to path.toString()),
            )
        }
        val notebook = try {
            notebookFromWire(Json.parseObject(text))
        } catch (exc: LMeditorError) {
            throw NotebookError(
                "the manifest of the notebook '$notebookId' is not valid: ${exc.message}",
                mapOf("notebook_id" to notebookId, "path" to path.toString()),
            )
        }
        if (notebook.notebookId != notebookId) {
            throw NotebookError(
                "the manifest of the notebook '$notebookId' names the notebook " +
                    "'${notebook.notebookId}'",
                mapOf(
                    "notebook_id" to notebookId,
                    "manifest_notebook_id" to notebook.notebookId,
                    "path" to path.toString(),
                ),
            )
        }
        return notebook
    }

    /** Write the manifest of a notebook, atomically. */
    private fun writeManifest(notebook: Notebook) {
        val path = manifestPath(notebook.notebookId)
        try {
            Files.createDirectories(path.parent)
            atomicWriteText(path, Json.write(notebook.toWireMap()))
        } catch (exc: IOException) {
            throw NotebookError(
                "could not write the manifest of the notebook '${notebook.notebookId}': " +
                    exc.message,
                mapOf("notebook_id" to notebook.notebookId, "path" to path.toString()),
            )
        }
    }
}

/**
 * Return the current time in the format a notebook's manifest records it in.
 *
 * The format is the one the library's own manifests use -- ISO-8601 in UTC,
 * to the microsecond -- so a notebook and a book record their times the same
 * way and the client parses one shape.
 */
internal fun nowTimestamp(): String =
    TIMESTAMP_FORMAT.format(Instant.now().atOffset(ZoneOffset.UTC))

/**
 * Write [text] to [path] in UTF-8, atomically.
 *
 * The text is written to a temporary sibling and then moved into place, which
 * is atomic on every platform LMeditor runs on. An interrupted write therefore
 * leaves the previous version of the file intact instead of a half-written
 * notebook.
 */
internal fun atomicWriteText(path: Path, text: String) {
    val tempPath = path.resolveSibling("${path.fileName}.tmp")
    try {
        Files.writeString(tempPath, text, StandardCharsets.UTF_8)
        try {
            Files.move(
                tempPath,
                path,
                StandardCopyOption.REPLACE_EXISTING,
                StandardCopyOption.ATOMIC_MOVE,
            )
        } catch (exc: java.nio.file.AtomicMoveNotSupportedException) {
            Files.move(tempPath, path, StandardCopyOption.REPLACE_EXISTING)
        }
    } catch (exc: IOException) {
        try {
            Files.deleteIfExists(tempPath)
        } catch (cleanupError: IOException) {
            throw NotebookError(
                "could not write '$path' and could not clean up its temporary file: " +
                    "${cleanupError.message}",
                mapOf("path" to path.toString()),
            )
        }
        throw exc
    }
}

/** Delete a directory and everything it holds. */
private fun deleteRecursively(directory: Path) {
    Files.walk(directory).use { stream ->
        stream.sorted(Comparator.reverseOrder()).forEach { path ->
            Files.deleteIfExists(path)
        }
    }
}
