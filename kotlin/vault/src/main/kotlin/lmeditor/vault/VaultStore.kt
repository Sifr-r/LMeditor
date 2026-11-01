package lmeditor.vault

import lmeditor.core.VaultNote
import lmeditor.core.VaultNoteSummary
import lmeditor.core.VaultRenameResult
import lmeditor.core.VaultSearchHit
import lmeditor.core.VaultTag
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.BasicFileAttributes
import java.time.Instant

/**
 * Raised when a note is not in the vault.
 *
 * The error is a [VaultError] -- so a caller of the vault can catch the
 * domain error -- and it names the note that is not there, so a missing note
 * is never mistaken for an unsafe note id.
 */
class NoteNotFoundError(
    /** The note that is not in the vault. */
    val noteId: String,
) : VaultError(
    "NoteNotFoundError: the note '$noteId' is not in the vault.",
    mapOf("note_id" to noteId),
)

/**
 * The store of a vault: the folder of markdown files the operator keeps.
 *
 * A vault has no index and no database. Every rule here reads the folder it
 * is given, so the operator's own edits -- made in Obsidian, or in a text
 * editor -- are seen the next time the vault is read, and the folder stays a
 * folder that any editor can open.
 *
 * The store writes plain UTF-8 markdown files and nothing else. Writing a
 * note is idempotent: the same content twice leaves the same bytes. A write
 * recomputes the note's title, its tags, its links and the backlinks of
 * every note that links to it.
 */
class VaultStore(
    /** The folder the notes live in. */
    val root: Path,
) {

    /**
     * Return every note of the vault, in note-id order.
     *
     * Hidden files and folders are skipped, so Obsidian's own `.obsidian`
     * folder is never read, and symlinked entries are skipped, so a vault
     * cannot be walked out of through a link.
     *
     * @return the summaries of the notes, ordered by note id
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun listNotes(): List<VaultNoteSummary> = loadAll().map { note ->
        VaultNoteSummary(
            noteId = note.file.noteId,
            title = note.title,
            path = note.file.relativePath,
            tags = note.tags,
            wordCount = note.wordCount,
            modifiedAt = modifiedAt(note.file.path),
        )
    }

    /**
     * Return the note a note id names.
     *
     * @param noteId the note's id: its path inside the vault without the `.md`
     * @return the note, with its content, its tags, its links and its backlinks
     * @throws UnsafeNoteIdError the id is not a safe path inside the vault
     * @throws NoteNotFoundError the note is not in the vault
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun readNote(noteId: String): VaultNote {
        val id = VaultPath.validateNoteId(noteId)
        val file = VaultPath.resolve(root, id)
        if (!Files.isRegularFile(file)) {
            throw NoteNotFoundError(id)
        }
        val content = readText(file, id)
        return noteOf(id, file, content, loadAll())
    }

    /**
     * Write a note, and return it.
     *
     * The write is idempotent: the same content twice leaves the same bytes.
     * The note's title, its tags, its links and the backlinks of every note
     * that links to it are recomputed, so the note that comes back is the
     * note the vault holds.
     *
     * @param noteId the note's id: its path inside the vault without the `.md`
     * @param content the markdown the note holds
     * @return the note, as the vault holds it after the write
     * @throws UnsafeNoteIdError the id is not a safe path inside the vault
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun writeNote(noteId: String, content: String): VaultNote {
        val id = VaultPath.validateNoteId(noteId)
        val file = VaultPath.resolve(root, id)
        writeFile(file, content)
        return noteOf(id, file, content, loadAll())
    }

    /**
     * Create a note, and return it.
     *
     * The title becomes the note's first heading, which is how Obsidian
     * names a note that has no frontmatter -- unless the content already
     * opens with a heading of its own, in which case the note names itself
     * and a second heading would say something the operator did not write.
     * Creating a note that is already there changes nothing and returns the
     * note that is there: the call is idempotent and never overwrites the
     * operator's work.
     *
     * @param noteId the note's id: its path inside the vault without the `.md`
     * @param title the title the note carries
     * @param content the markdown the note holds
     * @return the note, as the vault holds it
     * @throws UnsafeNoteIdError the id is not a safe path inside the vault
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun createNote(noteId: String, title: String, content: String): VaultNote {
        val id = VaultPath.validateNoteId(noteId)
        val file = VaultPath.resolve(root, id)
        if (Files.isRegularFile(file)) {
            return readNote(id)
        }
        val text = when {
            title.isBlank() -> content
            Markdown.opensWithHeading(content) -> content
            else -> "# " + title.trim() + "\n\n" + content
        }
        writeFile(file, text)
        return noteOf(id, file, text, loadAll())
    }

    /**
     * Delete a note, and the file it holds.
     *
     * Nothing else is touched: the folders the note sat in stay, because the
     * operator's folder structure is theirs.
     *
     * @param noteId the note's id: its path inside the vault without the `.md`
     * @throws UnsafeNoteIdError the id is not a safe path inside the vault
     * @throws NoteNotFoundError the note is not in the vault
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun deleteNote(noteId: String) {
        val id = VaultPath.validateNoteId(noteId)
        val file = VaultPath.resolve(root, id)
        if (!Files.isRegularFile(file)) {
            throw NoteNotFoundError(id)
        }
        try {
            Files.delete(file)
        } catch (exc: IOException) {
            throw VaultError(
                "could not delete the note '$id': ${exc.message}",
                mapOf("note_id" to id),
            )
        }
    }

    /**
     * Rename a note, and report what that changed.
     *
     * The file is moved. When [updateLinks] is asked for, the wiki-links in
     * the other notes that pointed at the old note are rewritten to point at
     * the new one, keeping their alias and their heading, and the number of
     * rewritten links is reported.
     *
     * @param fromId the note's id before the rename
     * @param toId the note's id after the rename
     * @param updateLinks whether the links that pointed at the note are rewritten
     * @return what the rename did
     * @throws UnsafeNoteIdError an id is not a safe path inside the vault
     * @throws NoteNotFoundError the note is not in the vault
     * @throws VaultError a note already holds the new id
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun renameNote(fromId: String, toId: String, updateLinks: Boolean): VaultRenameResult {
        val from = VaultPath.validateNoteId(fromId)
        val to = VaultPath.validateNoteId(toId)
        val source = VaultPath.resolve(root, from)
        if (!Files.isRegularFile(source)) {
            throw NoteNotFoundError(from)
        }
        val target = VaultPath.resolve(root, to)
        if (Files.exists(target)) {
            throw VaultError("the note '$to' is already in the vault", mapOf("note_id" to to))
        }
        val notes = loadAll()
        val index = linkIndex(notes)
        val parent = target.parent
        if (parent != null) {
            createDirectories(parent, to)
        }
        try {
            Files.move(source, target)
        } catch (exc: IOException) {
            throw VaultError(
                "could not rename the note '$from': ${exc.message}",
                mapOf("note_id" to from),
            )
        }
        val updated = if (updateLinks) rewriteLinksTo(from, to, notes, index) else 0
        return VaultRenameResult(fromId = from, toId = to, updatedLinks = updated)
    }

    /**
     * Return the notes that carry the terms of a query, best first.
     *
     * @param query the query: a whitespace-separated set of terms
     * @param limit the greatest number of hits to return
     * @return the hits, best first
     * @throws ValidationError the limit is not a positive number
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun search(query: String, limit: Int): List<VaultSearchHit> {
        val notes = loadAll().map { note ->
            SearchableNote(
                noteId = note.file.noteId,
                title = note.title,
                path = note.file.relativePath,
                body = note.body,
                tags = note.tags,
            )
        }
        return VaultSearch.search(notes, query, limit)
    }

    /**
     * Return every tag of the vault, and how many notes carry it.
     *
     * @return the tags, ordered by name
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun tags(): List<VaultTag> {
        val counts = LinkedHashMap<String, Int>()
        for (note in loadAll()) {
            for (tag in note.tags) {
                counts[tag] = (counts[tag] ?: 0) + 1
            }
        }
        return counts.entries.sortedBy { it.key }.map { entry ->
            VaultTag(tag = entry.key, count = entry.value)
        }
    }

    /** A note file of the vault, as it is on disk. */
    private data class NoteFile(
        /** The note's id: its path inside the vault without the `.md`. */
        val noteId: String,
        /** The note's path inside the vault, with the extension. */
        val relativePath: String,
        /** The file the note is held in. */
        val path: Path,
    )

    /** A note of the vault, read and parsed. */
    private data class LoadedNote(
        /** The file the note is held in. */
        val file: NoteFile,
        /** The text the file holds. */
        val content: String,
        /** The title of the note. */
        val title: String,
        /** The tags the note carries, lower-cased and sorted. */
        val tags: List<String>,
        /** The wiki-link targets the note holds, in the order they were written. */
        val links: List<String>,
        /** The text of the note without its frontmatter. */
        val body: String,
        /** The number of words in the body. */
        val wordCount: Int,
    )

    /**
     * Return every note of the vault, read and parsed, in note-id order.
     */
    private fun loadAll(): List<LoadedNote> {
        val notes = mutableListOf<LoadedNote>()
        for (file in noteFiles()) {
            val content = readText(file.path, file.noteId)
            val parsed = parse(content, file)
            notes.add(
                LoadedNote(
                    file = file,
                    content = content,
                    title = parsed.title,
                    tags = parsed.tags,
                    links = parsed.links,
                    body = parsed.body,
                    wordCount = parsed.wordCount,
                ),
            )
        }
        return notes
    }

    /**
     * Return every note file of the vault, in note-id order.
     *
     * Hidden entries and symlinked entries are skipped, and only files that
     * carry the `.md` extension are notes.
     */
    private fun noteFiles(): List<NoteFile> {
        val realRoot = VaultPath.realRoot(root)
        val found = mutableListOf<NoteFile>()
        collect(realRoot, realRoot, found)
        return found.sortedBy { it.noteId }
    }

    /** Collect the note files of a folder, and of the folders under it. */
    private fun collect(directory: Path, realRoot: Path, found: MutableList<NoteFile>) {
        val entries = try {
            Files.newDirectoryStream(directory).use { stream -> stream.toList() }
        } catch (exc: IOException) {
            throw VaultError(
                "could not read the folder '$directory': ${exc.message}",
                mapOf("path" to directory.toString()),
            )
        }
        for (entry in entries.sortedBy { it.fileName.toString() }) {
            val name = entry.fileName.toString()
            if (VaultPath.isHiddenName(name)) {
                continue
            }
            if (Files.isSymbolicLink(entry)) {
                continue
            }
            if (Files.isDirectory(entry)) {
                collect(entry, realRoot, found)
            } else if (Files.isRegularFile(entry) && isNoteFile(name)) {
                val relative = relativePath(realRoot, entry)
                found.add(
                    NoteFile(
                        noteId = relative.substring(0, relative.length - NOTE_EXTENSION.length),
                        relativePath = relative,
                        path = entry,
                    ),
                )
            }
        }
    }

    /** Whether a file name carries the extension a note carries. */
    private fun isNoteFile(name: String): Boolean =
        name.length > NOTE_EXTENSION.length && name.lowercase().endsWith(NOTE_EXTENSION)

    /** Return the path of a file inside the vault, with `/` separators. */
    private fun relativePath(realRoot: Path, file: Path): String =
        realRoot.relativize(file).joinToString("/") { it.toString() }

    /** Read what the markdown grammar makes of a note. */
    private fun parse(content: String, file: NoteFile): ParsedNote {
        val heading = Markdown.headingTitle(content)
        val frontmatter = Markdown.frontmatter(content)
        val title = when {
            heading.isNotEmpty() -> heading
            frontmatter.title.isNotEmpty() -> frontmatter.title
            else -> file.noteId.substringAfterLast('/')
        }
        return ParsedNote(
            title = title,
            tags = Markdown.tags(content),
            links = Markdown.links(content),
            body = Markdown.body(content),
            wordCount = Markdown.wordCount(content),
        )
    }

    /** Build the note the caller receives, with its backlinks. */
    private fun noteOf(id: String, file: Path, content: String, notes: List<LoadedNote>): VaultNote {
        val path = relativePath(VaultPath.realRoot(root), file)
        val parsed = parse(content, NoteFile(id, path, file))
        return VaultNote(
            noteId = id,
            title = parsed.title,
            path = path,
            content = content,
            tags = parsed.tags,
            links = parsed.links,
            backlinks = backlinksOf(id, notes),
            createdAt = createdAt(file),
            modifiedAt = modifiedAt(file),
            wordCount = parsed.wordCount,
        )
    }

    /**
     * Return the notes that link to a note.
     *
     * A wiki-link is resolved against the titles of the notes and against
     * their paths without their extension, case-insensitively. A link that
     * resolves to nothing is a link to a note that does not exist yet, and
     * it is no backlink of anybody.
     */
    private fun backlinksOf(noteId: String, notes: List<LoadedNote>): List<String> {
        val index = linkIndex(notes)
        val backlinks = sortedSetOf<String>()
        for (note in notes) {
            if (note.file.noteId.equals(noteId, ignoreCase = true)) {
                continue
            }
            for (target in note.links) {
                if (resolvesTo(target, index).equals(noteId, ignoreCase = true)) {
                    backlinks.add(note.file.noteId)
                }
            }
        }
        return backlinks.toList()
    }

    /**
     * Return the index a wiki-link is resolved against.
     *
     * The paths without their extension come first, so a link that names a
     * path wins over a link that names only a file. The file name comes next,
     * because that is how Obsidian resolves a link: `[[note]]` names
     * `folder/note.md` as readily as it names `note.md`. The title comes last,
     * because a note whose heading is not its file name can still be linked
     * by that heading.
     *
     * Two notes with the same file name make the file name ambiguous; the
     * first of them in note-id order wins, so the answer is the same every
     * time.
     */
    private fun linkIndex(notes: List<LoadedNote>): Map<String, String> {
        val index = LinkedHashMap<String, String>()
        for (note in notes) {
            index.putIfAbsent(note.file.noteId.lowercase(), note.file.noteId)
        }
        for (note in notes) {
            val fileName = note.file.noteId.substringAfterLast('/')
            if (fileName.isNotEmpty()) {
                index.putIfAbsent(fileName.lowercase(), note.file.noteId)
            }
        }
        for (note in notes) {
            if (note.title.isNotEmpty()) {
                index.putIfAbsent(note.title.lowercase(), note.file.noteId)
            }
        }
        return index
    }

    /** Return the note a wiki-link names, or an empty string. */
    private fun resolvesTo(target: String, index: Map<String, String>): String =
        index[target.trim().lowercase()] ?: ""

    /**
     * Rewrite the links that pointed at a note, and report how many there were.
     */
    private fun rewriteLinksTo(
        from: String,
        to: String,
        notes: List<LoadedNote>,
        index: Map<String, String>,
    ): Int {
        var updated = 0
        for (note in notes) {
            if (note.file.noteId.equals(from, ignoreCase = true)) {
                continue
            }
            val pointsAtOld = { target: String ->
                resolvesTo(target, index).equals(from, ignoreCase = true)
            }
            val pointing = Markdown.countLinks(note.content, pointsAtOld)
            if (pointing == 0) {
                continue
            }
            writeFile(note.file.path, Markdown.rewriteLinks(note.content, to, pointsAtOld))
            updated += pointing
        }
        return updated
    }

    /** Write the text of a note, creating the folders it sits in. */
    private fun writeFile(file: Path, content: String) {
        val parent = file.parent
        if (parent != null) {
            createDirectories(parent, file.toString())
        }
        try {
            Files.writeString(file, content, StandardCharsets.UTF_8)
        } catch (exc: IOException) {
            throw VaultError(
                "could not write '$file': ${exc.message}",
                mapOf("path" to file.toString()),
            )
        }
    }

    /** Create the folders a note sits in. */
    private fun createDirectories(parent: Path, noteId: String) {
        try {
            Files.createDirectories(parent)
        } catch (exc: IOException) {
            throw VaultError(
                "could not create the folder '$parent': ${exc.message}",
                mapOf("note_id" to noteId),
            )
        }
    }

    /** Read the text of a note. */
    private fun readText(file: Path, noteId: String): String = try {
        Files.readString(file, StandardCharsets.UTF_8)
    } catch (exc: IOException) {
        throw VaultError(
            "could not read the note '$noteId': ${exc.message}",
            mapOf("note_id" to noteId),
        )
    }

    /** Return when the file was created, in ISO-8601. */
    private fun createdAt(file: Path): String {
        val millis = try {
            Files.readAttributes(file, BasicFileAttributes::class.java).creationTime().toMillis()
        } catch (exc: IOException) {
            Files.getLastModifiedTime(file).toMillis()
        } catch (exc: UnsupportedOperationException) {
            Files.getLastModifiedTime(file).toMillis()
        }
        return timestamp(millis)
    }

    /** Return when the file was last changed, in ISO-8601. */
    private fun modifiedAt(file: Path): String =
        timestamp(Files.getLastModifiedTime(file).toMillis())

    /** Return a moment in time as ISO-8601, to the second. */
    private fun timestamp(millis: Long): String =
        Instant.ofEpochMilli(millis / 1000L * 1000L).toString()
}
