package lmeditor.library

import lmeditor.core.BookManifest
import lmeditor.core.BookNotFoundError
import lmeditor.core.BookSection
import lmeditor.core.LibraryError
import lmeditor.core.SectionNotFoundError
import lmeditor.core.SourceChunk
import lmeditor.core.json.Json
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/** The file that holds a book's manifest. */
const val MANIFEST_FILENAME: String = "manifest.json"

/** The directory that holds a book's section files. */
const val SECTIONS_DIRNAME: String = "sections"

/** The file that holds a book's corpus chunks, one JSON object per line. */
const val CHUNKS_FILENAME: String = "chunks.jsonl"

/**
 * The file that holds the document a book was imported from.
 *
 * It carries the document-level records only -- the notes, the media, the
 * styles, the page setup. The blocks live in the section files they belong to,
 * so that reading a section never costs a second file.
 */
const val ORIGIN_FILENAME: String = "origin.json"

/** The longest a book or section id may be. */
const val MAX_IDENTIFIER_LENGTH: Int = 128

/** Characters that may not appear in an id: they separate path segments or are refused by Windows. */
private val FORBIDDEN_ID_CHARS: Set<Char> = setOf('/', '\\', ':', '*', '?', '"', '<', '>', '|')

/** Device names Windows reserves; `NUL.json` is the null device, not a file. */
private val WINDOWS_RESERVED_NAMES: Set<String> =
    setOf("CON", "PRN", "AUX", "NUL") +
        (1..9).map { "COM$it" } +
        (1..9).map { "LPT$it" }

/**
 * Raised when a book or section id is not a safe path segment.
 *
 * The error is a [LibraryError] -- so a caller of the library can catch the
 * domain error -- and it names the offending value, because an unsafe
 * identifier is a bad argument value and must not be mistaken for a missing
 * book.
 */
class UnsafeIdentifierError(
    /** What the identifier names: `book_id` or `section_id`. */
    val kind: String,
    /** The offending value. */
    val value: String,
    /** Why the value is not a safe path segment. */
    val reason: String,
) : LibraryError(
    "unsafe $kind '$value': $reason",
    mapOf("kind" to kind, "value" to value, "reason" to reason),
)

/**
 * Return [value] as a safe path segment, or raise when it is not one.
 *
 * A book id becomes a directory and a section id becomes a file name, so both
 * have to be a single segment of a path: no separator, no `.` or `..`, no drive
 * letter, no absolute path, no NUL, and none of the characters Windows
 * refuses in a file name.
 *
 * @return the identifier, stripped of surrounding whitespace. The stripped form
 *   is the one written to disk, so the file name and the manifest always agree.
 * @throws UnsafeIdentifierError the identifier is empty, too long, or could
 *   escape the directory it is used in.
 */
fun validateIdentifier(value: String, kind: String): String {
    if (value.isBlank()) {
        throw UnsafeIdentifierError(kind, value, "it must be a non-empty string")
    }
    if (value.length > MAX_IDENTIFIER_LENGTH) {
        throw UnsafeIdentifierError(
            kind,
            value,
            "it exceeds the maximum length of $MAX_IDENTIFIER_LENGTH characters",
        )
    }
    if (value.contains('\u0000')) {
        throw UnsafeIdentifierError(kind, value, "it contains a NUL character")
    }
    val forbidden = FORBIDDEN_ID_CHARS.filter { value.contains(it) }.sorted()
    if (forbidden.isNotEmpty()) {
        throw UnsafeIdentifierError(
            kind,
            value,
            "it contains the forbidden character(s) $forbidden",
        )
    }
    val stripped = value.trim()
    if (stripped == "." || stripped == "..") {
        throw UnsafeIdentifierError(kind, value, "it is a relative path segment")
    }
    if (stripped.startsWith("/") || stripped.startsWith("\\") || stripped.contains('\\')) {
        throw UnsafeIdentifierError(kind, value, "it is an absolute path")
    }
    if (stripped.length >= 2 && stripped[1] == ':') {
        throw UnsafeIdentifierError(kind, value, "it carries a drive letter")
    }
    if (stripped.substringBefore('.').uppercase() in WINDOWS_RESERVED_NAMES) {
        throw UnsafeIdentifierError(kind, value, "it is a reserved device name")
    }
    return stripped
}

/**
 * What one read of a section's file yields.
 *
 * The body and the origin come from the same file and are read together, so a
 * caller that needs both -- an export that has to decide whether a section was
 * edited -- pays for one read and not two.
 *
 * The origin is the JSON document the section was imported with; its shape is
 * the importer's, not the store's.
 */
data class SectionRecord(
    /** The section's id. */
    val sectionId: String,
    /** The section's title. */
    val title: String = "",
    /** The section's position in the book. */
    val order: Int = 0,
    /** The section's editable text. */
    val content: String = "",
    /** The document the section was imported from, when it has one. */
    val origin: Map<String, Any?>? = null,
)

/**
 * A library of books kept on disk, one directory per book.
 *
 * A book is a directory:
 *
 * ```
 * <root>/<book_id>/manifest.json     the index of the book, never its body
 * <root>/<book_id>/sections/<section_id>.json   one file per section, holding its text
 * <root>/<book_id>/chunks.jsonl      the corpus chunks the book is indexed by
 * <root>/<book_id>/origin.json       the document the book was imported from
 * ```
 *
 * The store is the only thing that touches the filesystem in this domain.
 * Everything else -- the brief, the working set, the importer -- goes through
 * it, so the rules about what is read when, and what is written atomically,
 * live in one place.
 *
 * @param root the directory the books are kept in. It is created when the
 *   first book is written, not when the store is constructed.
 */
class BookStore(
    /** The directory the store keeps its books in. */
    val root: Path,
) {
    private var readCountInternal: Int = 0

    /**
     * The number of section bodies read from disk so far.
     *
     * The counter is what makes the bounded-context property measurable rather
     * than merely asserted: a working set built for one section of a
     * thousand-section book reads `2 * neighbours + 1` bodies, and this is
     * where that is counted.
     */
    val readCount: Int get() = readCountInternal

    /**
     * Create the book [bookId] and return its manifest.
     *
     * Creating a book that already exists returns the existing manifest
     * instead of overwriting it: the call is idempotent, so an import that
     * runs twice does not discard the book the first run produced.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     */
    fun create(bookId: String, title: String = ""): BookManifest {
        val id = validateIdentifier(bookId, "book_id")
        val manifestPath = manifestPath(id)
        if (Files.isRegularFile(manifestPath)) {
            return readManifest(manifestPath, id)
        }
        val now = nowTimestamp()
        val manifest = BookManifest(
            bookId = id,
            title = title.trim(),
            sectionCount = 0,
            chunkCount = 0,
            createdAt = now,
            modifiedAt = now,
            sections = emptyList(),
            brief = "",
        )
        Files.createDirectories(sectionsDir(id))
        writeManifest(manifest)
        return manifest
    }

    /**
     * Return the manifest of the book [bookId].
     *
     * The manifest is the index of the book: the section titles, the counts
     * and the running brief. No section body is read.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     * @throws LibraryError the manifest cannot be read or is not valid.
     */
    fun open(bookId: String): BookManifest {
        val id = validateIdentifier(bookId, "book_id")
        val manifestPath = manifestPath(id)
        if (!Files.isRegularFile(manifestPath)) {
            throw BookNotFoundError(id)
        }
        return readManifest(manifestPath, id)
    }

    /**
     * Return the manifest of every book in the library, ordered by book id.
     *
     * A directory that holds no manifest is not a book and is skipped; a
     * manifest that cannot be parsed is an error, because a book the library
     * cannot read must not be silently omitted from the list.
     */
    fun listBooks(): List<BookManifest> {
        if (!Files.isDirectory(root)) {
            return emptyList()
        }
        val entries = Files.list(root).use { stream ->
            stream.filter { Files.isDirectory(it) }.sorted().toList()
        }
        return entries.mapNotNull { entry ->
            val manifestPath = entry.resolve(MANIFEST_FILENAME)
            if (!Files.isRegularFile(manifestPath)) {
                null
            } else {
                readManifest(manifestPath, entry.fileName.toString())
            }
        }
    }

    /**
     * Delete the book [bookId] and everything it holds.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     * @throws LibraryError the book could not be removed.
     */
    fun delete(bookId: String) {
        val id = validateIdentifier(bookId, "book_id")
        val bookDir = bookDir(id)
        if (!Files.isRegularFile(bookDir.resolve(MANIFEST_FILENAME))) {
            throw BookNotFoundError(id)
        }
        try {
            deleteRecursively(bookDir)
        } catch (exc: IOException) {
            throw LibraryError(
                "could not delete the book '$id': ${exc.message}",
                mapOf("book_id" to id, "path" to bookDir.toString()),
            )
        }
    }

    /**
     * Add a section to a book, or update it when it is already there.
     *
     * Adding a section that already exists updates it in place: the body is
     * replaced, the word and character counts are recomputed, and the chunk
     * ids the section is indexed by are kept. An empty [title] keeps the title
     * the section already has and an [order] of null keeps the position it
     * already occupies, so a caller that only wants to replace the text does
     * not have to restate the index.
     *
     * The section's origin is *replaced* as well: passing one stores it,
     * passing none clears it. An origin that outlives the body it was
     * imported with would resurrect the old structure on export, so the two
     * are always written together.
     *
     * @throws UnsafeIdentifierError an id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     * @throws LibraryError the content or the order is not usable.
     */
    fun addSection(
        bookId: String,
        sectionId: String,
        content: String,
        title: String = "",
        order: Int? = null,
        origin: Map<String, Any?>? = null,
    ): BookSection {
        val id = validateIdentifier(bookId, "book_id")
        val sid = validateIdentifier(sectionId, "section_id")
        if (order != null && order < 0) {
            throw LibraryError(
                "a section order must not be negative",
                mapOf("order" to order),
            )
        }
        val manifest = loadManifest(id)
        val updated = upsertSection(manifest, sid, content, title, order, origin)
        writeManifest(touched(updated))
        return updated.sections.first { it.sectionId == sid }
    }

    /**
     * Return the body of one section of a book.
     *
     * This is the only method that reads a section body, and it reads exactly
     * one. [readCount] is incremented, so the bound on the working set can be
     * measured rather than asserted.
     *
     * @throws UnsafeIdentifierError an id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     * @throws SectionNotFoundError the book holds no such section.
     */
    fun readSection(bookId: String, sectionId: String): String =
        readSectionRecord(bookId, sectionId).content

    /**
     * Return a section's body *and* its origin from a single read.
     *
     * @throws UnsafeIdentifierError an id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     * @throws SectionNotFoundError the book holds no such section.
     */
    fun readSectionRecord(bookId: String, sectionId: String): SectionRecord {
        val id = validateIdentifier(bookId, "book_id")
        val sid = validateIdentifier(sectionId, "section_id")
        val path = sectionPath(id, sid)
        if (!Files.isRegularFile(path)) {
            if (!Files.isRegularFile(manifestPath(id))) {
                throw BookNotFoundError(id)
            }
            throw SectionNotFoundError(id, sid)
        }
        val payload = readSectionFile(path, id, sid)
        readCountInternal++
        return SectionRecord(
            sectionId = payload.sectionId,
            title = payload.title,
            order = payload.order,
            content = payload.content,
            origin = payload.origin,
        )
    }

    /**
     * Replace the body of an existing section and return its index entry.
     *
     * The word and character counts are recomputed from the new body. The
     * section's title, its position and its chunk ids are kept.
     *
     * The section's origin is kept as well: the body is what the editor
     * changed, and the origin is the record of what the document looked like
     * before.
     *
     * @throws UnsafeIdentifierError an id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     * @throws SectionNotFoundError the book holds no such section.
     */
    fun writeSection(bookId: String, sectionId: String, content: String): BookSection {
        val id = validateIdentifier(bookId, "book_id")
        val sid = validateIdentifier(sectionId, "section_id")
        val manifest = loadManifest(id)
        val existing = findSection(manifest, sid) ?: throw SectionNotFoundError(id, sid)
        val origin = readSectionOrigin(id, sid)
        val section = existing.copy(
            wordCount = countWords(content),
            charCount = charCount(content),
        )
        writeSectionFile(id, section, content, origin)
        val sections = manifest.sections.map { if (it.sectionId == sid) section else it }
        writeManifest(touched(withSections(manifest, sections)))
        return section
    }

    /**
     * Return the number of sections in a book, without reading any body.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     */
    fun sectionCount(bookId: String): Int = loadManifest(validateIdentifier(bookId, "book_id")).sections.size

    /**
     * Record the corpus chunks that belong to a section.
     *
     * The chunk ids live in the manifest, which is the index of the book, so
     * re-indexing a section never rewrites its body.
     *
     * @throws UnsafeIdentifierError an id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     * @throws SectionNotFoundError the book holds no such section.
     */
    fun setSectionChunks(bookId: String, sectionId: String, chunkIds: List<String>): BookSection {
        val id = validateIdentifier(bookId, "book_id")
        val sid = validateIdentifier(sectionId, "section_id")
        val manifest = loadManifest(id)
        val existing = findSection(manifest, sid) ?: throw SectionNotFoundError(id, sid)
        val section = existing.copy(chunkIds = chunkIds.toList())
        val sections = manifest.sections.map { if (it.sectionId == sid) section else it }
        writeManifest(touched(withSections(manifest, sections)))
        return section
    }

    /**
     * Store the corpus chunks of a book and record how many there are.
     *
     * The chunks are added to the book's corpus rather than replacing it: a
     * chunk id is a hash of the chunk's text and its offsets, so two sections
     * that carry the same text produce the same chunk id, and a chunk that is
     * already stored is kept once. Indexing one section therefore never drops
     * the chunks of another, and indexing the same section twice changes
     * nothing.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     */
    fun putChunks(bookId: String, chunks: List<SourceChunk>) {
        val id = validateIdentifier(bookId, "book_id")
        val manifest = loadManifest(id)
        val stored = LinkedHashMap<String, SourceChunk>()
        for (chunk in chunks(bookId)) {
            stored[chunk.chunkId] = chunk
        }
        for (chunk in chunks) {
            stored.putIfAbsent(chunk.chunkId, chunk)
        }
        val ordered = stored.values.toList()
        writeChunksFile(id, ordered)
        writeManifest(touched(manifest.copy(chunkCount = ordered.size)))
    }

    /**
     * Return the corpus chunks stored for a book.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     * @throws LibraryError the chunk file cannot be read or is not valid.
     */
    fun chunks(bookId: String): List<SourceChunk> {
        val id = validateIdentifier(bookId, "book_id")
        val path = chunksPath(id)
        if (!Files.isRegularFile(path)) {
            if (!Files.isRegularFile(manifestPath(id))) {
                throw BookNotFoundError(id)
            }
            return emptyList()
        }
        val text = try {
            Files.readString(path, StandardCharsets.UTF_8)
        } catch (exc: IOException) {
            throw LibraryError(
                "could not read the chunks of book '$id': ${exc.message}",
                mapOf("book_id" to id, "path" to path.toString()),
            )
        }
        val chunks = mutableListOf<SourceChunk>()
        text.lineSequence().forEachIndexed { index, line ->
            if (line.isBlank()) {
                return@forEachIndexed
            }
            chunks.add(parseChunk(line, id, index + 1))
        }
        return chunks
    }

    /**
     * Return the book's running brief: the cached digest of its contents.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     */
    fun brief(bookId: String): String = loadManifest(validateIdentifier(bookId, "book_id")).brief

    /**
     * Return the document a book was imported from, or null.
     *
     * A book that was created from a list of sections has no origin: there is
     * no document behind it, and the export renders it from its text. A book
     * that was imported from a Word document has one, and the export rebuilds
     * the document from it.
     *
     * The origin holds no section body, so reading it is not a read of the
     * book and [readCount] does not move.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     */
    fun readBookOrigin(bookId: String): Map<String, Any?>? {
        val id = validateIdentifier(bookId, "book_id")
        if (!Files.isRegularFile(manifestPath(id))) {
            throw BookNotFoundError(id)
        }
        val path = originPath(id)
        if (!Files.isRegularFile(path)) {
            return null
        }
        val text = try {
            Files.readString(path, StandardCharsets.UTF_8)
        } catch (exc: IOException) {
            throw LibraryError(
                "could not read the origin of book '$id': ${exc.message}",
                mapOf("book_id" to id, "path" to path.toString()),
            )
        }
        return try {
            Json.parseObject(text)
        } catch (exc: RuntimeException) {
            throw LibraryError(
                "the origin of book '$id' is not valid: ${exc.message}",
                mapOf("book_id" to id, "path" to path.toString()),
            )
        }
    }

    /**
     * Record the document a book was imported from.
     *
     * The record replaces any the book held: an import that brings a new
     * document brings new notes, new media and new parts, and the old ones
     * would otherwise be written into the exported file alongside them.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     */
    fun setBookOrigin(bookId: String, origin: Map<String, Any?>) {
        val id = validateIdentifier(bookId, "book_id")
        if (!Files.isRegularFile(manifestPath(id))) {
            throw BookNotFoundError(id)
        }
        val path = originPath(id)
        Files.createDirectories(path.parent)
        atomicWriteText(path, PrettyJson.write(origin))
    }

    /**
     * Replace the book's running brief.
     *
     * @throws UnsafeIdentifierError the book id is not a safe path segment.
     * @throws BookNotFoundError the library holds no such book.
     */
    fun updateBrief(bookId: String, brief: String) {
        val id = validateIdentifier(bookId, "book_id")
        val manifest = loadManifest(id)
        writeManifest(touched(manifest.copy(brief = brief.trim())))
    }

    // --- internals -----------------------------------------------------------

    /** Return the manifest and the section entry after an upsert. */
    private fun upsertSection(
        manifest: BookManifest,
        sectionId: String,
        content: String,
        title: String,
        order: Int?,
        origin: Map<String, Any?>?,
    ): BookManifest {
        val existing = findSection(manifest, sectionId)
        val section = if (existing == null) {
            BookSection(
                sectionId = sectionId,
                title = title.trim(),
                order = order ?: nextOrder(manifest.sections),
                wordCount = countWords(content),
                charCount = charCount(content),
                chunkIds = emptyList(),
            )
        } else {
            BookSection(
                sectionId = sectionId,
                title = if (title.isEmpty()) existing.title else title.trim(),
                order = order ?: existing.order,
                wordCount = countWords(content),
                charCount = charCount(content),
                chunkIds = existing.chunkIds,
            )
        }
        writeSectionFile(manifest.bookId, section, content, origin)
        val sections = if (existing == null) {
            manifest.sections + section
        } else {
            manifest.sections.map { if (it.sectionId == sectionId) section else it }
        }
        return withSections(manifest, sortedSections(sections))
    }

    /** Return the manifest of a book that is known to exist. */
    private fun loadManifest(bookId: String): BookManifest {
        val path = manifestPath(bookId)
        if (!Files.isRegularFile(path)) {
            throw BookNotFoundError(bookId)
        }
        return readManifest(path, bookId)
    }

    /** Read and validate a manifest file. */
    private fun readManifest(path: Path, bookId: String): BookManifest {
        val text = try {
            Files.readString(path, StandardCharsets.UTF_8)
        } catch (exc: IOException) {
            throw LibraryError(
                "could not read the manifest of book '$bookId': ${exc.message}",
                mapOf("book_id" to bookId, "path" to path.toString()),
            )
        }
        val payload = try {
            Json.parseObject(text)
        } catch (exc: RuntimeException) {
            throw LibraryError(
                "the manifest of book '$bookId' is not valid: ${exc.message}",
                mapOf("book_id" to bookId, "path" to path.toString()),
            )
        }
        return manifestFromMap(payload, bookId)
    }

    /** Read and validate a section file. */
    private fun readSectionFile(path: Path, bookId: String, sectionId: String): SectionRecord {
        val text = try {
            Files.readString(path, StandardCharsets.UTF_8)
        } catch (exc: IOException) {
            throw LibraryError(
                "could not read section '$sectionId' of book '$bookId': ${exc.message}",
                mapOf("book_id" to bookId, "section_id" to sectionId, "path" to path.toString()),
            )
        }
        val payload = try {
            Json.parseObject(text)
        } catch (exc: RuntimeException) {
            throw LibraryError(
                "the file of section '$sectionId' of book '$bookId' is not valid: ${exc.message}",
                mapOf("book_id" to bookId, "section_id" to sectionId, "path" to path.toString()),
            )
        }
        @Suppress("UNCHECKED_CAST")
        val origin = payload["origin"] as? Map<String, Any?>
        return SectionRecord(
            sectionId = payload["section_id"] as? String ?: sectionId,
            title = payload["title"] as? String ?: "",
            order = (payload["order"] as? Number)?.toInt() ?: 0,
            content = payload["content"] as? String ?: "",
            origin = origin,
        )
    }

    /** Write a book's manifest atomically. */
    private fun writeManifest(manifest: BookManifest) {
        val path = manifestPath(manifest.bookId)
        Files.createDirectories(path.parent)
        atomicWriteText(path, PrettyJson.write(manifestToMap(manifest)))
    }

    /** Write one section's file atomically. */
    private fun writeSectionFile(
        bookId: String,
        section: BookSection,
        content: String,
        origin: Map<String, Any?>?,
    ) {
        val path = sectionPath(bookId, section.sectionId)
        Files.createDirectories(path.parent)
        val payload = linkedMapOf<String, Any?>(
            "section_id" to section.sectionId,
            "title" to section.title,
            "order" to section.order,
            "word_count" to section.wordCount,
            "char_count" to section.charCount,
            "content" to content,
            "origin" to origin,
        )
        atomicWriteText(path, PrettyJson.write(payload))
    }

    /** Return a section's origin, reading its file once. */
    private fun readSectionOrigin(bookId: String, sectionId: String): Map<String, Any?>? {
        val path = sectionPath(bookId, sectionId)
        if (!Files.isRegularFile(path)) {
            return null
        }
        val payload = readSectionFile(path, bookId, sectionId)
        readCountInternal++
        return payload.origin
    }

    /** Write a book's chunk file atomically, one JSON object per line. */
    private fun writeChunksFile(bookId: String, chunks: List<SourceChunk>) {
        val path = chunksPath(bookId)
        Files.createDirectories(path.parent)
        val body = buildString {
            for (chunk in chunks) {
                append(Json.write(chunkToMap(chunk))).append('\n')
            }
        }
        atomicWriteText(path, body)
    }

    /** Return the manifest with its modification time moved forward. */
    private fun touched(manifest: BookManifest): BookManifest =
        manifest.copy(modifiedAt = nowTimestamp())

    /** Return the directory of a book. */
    private fun bookDir(bookId: String): Path = root.resolve(bookId)

    /** Return the path of a book's manifest. */
    private fun manifestPath(bookId: String): Path = bookDir(bookId).resolve(MANIFEST_FILENAME)

    /** Return the directory of a book's section files. */
    private fun sectionsDir(bookId: String): Path = bookDir(bookId).resolve(SECTIONS_DIRNAME)

    /** Return the path of one section's file. */
    private fun sectionPath(bookId: String, sectionId: String): Path =
        sectionsDir(bookId).resolve("$sectionId.json")

    /** Return the path of a book's chunk file. */
    private fun chunksPath(bookId: String): Path = bookDir(bookId).resolve(CHUNKS_FILENAME)

    /** Return the path of the file that holds a book's origin. */
    private fun originPath(bookId: String): Path = bookDir(bookId).resolve(ORIGIN_FILENAME)
}

/** Return the section with [sectionId], or null when there is none. */
internal fun findSection(manifest: BookManifest, sectionId: String): BookSection? =
    manifest.sections.firstOrNull { it.sectionId == sectionId }

/**
 * Return the manifest with a new section index and the matching count.
 *
 * The manifest carries the number of sections as well as the sections
 * themselves, and the two must not drift apart.
 */
internal fun withSections(manifest: BookManifest, sections: List<BookSection>): BookManifest =
    manifest.copy(sections = sections, sectionCount = sections.size)

/** Return the sections in book order: by position, then by id. */
internal fun sortedSections(sections: List<BookSection>): List<BookSection> =
    sections.sortedWith(compareBy({ it.order }, { it.sectionId }))

/** Return the position a newly added section takes. */
internal fun nextOrder(sections: List<BookSection>): Int =
    if (sections.isEmpty()) 0 else sections.maxOf { it.order } + 1

/** Return the number of whitespace-separated words in [content]. */
internal fun countWords(content: String): Int =
    content.split(WHITESPACE_RUN).count { it.isNotEmpty() }

/**
 * The number of characters in [content].
 *
 * The count is in code points, not UTF-16 units: an emoji is one character,
 * and the Python side counts it the same way. A count that disagreed would
 * make a book's index disagree with the text it indexes.
 */
internal fun charCount(content: String): Int = content.codePointCount(0, content.length)

/** The whitespace a word boundary falls on. */
private val WHITESPACE_RUN: Regex = Regex("[\\p{IsWhite_Space}]+")

/** Return the current time in the format the manifest records it in. */
internal fun nowTimestamp(): String =
    TIMESTAMP.format(Instant.now().atOffset(ZoneOffset.UTC))

/** The format a manifest's timestamps are written in. */
private val TIMESTAMP: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'")

/** Return the manifest as the JSON document the store writes. */
internal fun manifestToMap(manifest: BookManifest): Map<String, Any?> = linkedMapOf(
    "book_id" to manifest.bookId,
    "title" to manifest.title,
    "section_count" to manifest.sectionCount,
    "chunk_count" to manifest.chunkCount,
    "created_at" to manifest.createdAt,
    "modified_at" to manifest.modifiedAt,
    "sections" to manifest.sections.map { sectionToMap(it) },
    "brief" to manifest.brief,
)

/** Return the section as the JSON document the store writes. */
internal fun sectionToMap(section: BookSection): Map<String, Any?> = linkedMapOf(
    "section_id" to section.sectionId,
    "title" to section.title,
    "order" to section.order,
    "word_count" to section.wordCount,
    "char_count" to section.charCount,
    "chunk_ids" to section.chunkIds,
)

/** Return the manifest a stored document describes. */
internal fun manifestFromMap(payload: Map<String, Any?>, bookId: String): BookManifest {
    val sections = (payload["sections"] as? List<*>).orEmpty().mapNotNull { entry ->
        val section = entry as? Map<*, *> ?: return@mapNotNull null
        BookSection(
            sectionId = section["section_id"] as? String ?: "",
            title = section["title"] as? String ?: "",
            order = (section["order"] as? Number)?.toInt() ?: 0,
            wordCount = (section["word_count"] as? Number)?.toInt() ?: 0,
            charCount = (section["char_count"] as? Number)?.toInt() ?: 0,
            chunkIds = (section["chunk_ids"] as? List<*>).orEmpty().mapNotNull { it as? String },
        )
    }
    return BookManifest(
        bookId = payload["book_id"] as? String ?: bookId,
        title = payload["title"] as? String ?: "",
        sectionCount = (payload["section_count"] as? Number)?.toInt() ?: sections.size,
        chunkCount = (payload["chunk_count"] as? Number)?.toInt() ?: 0,
        createdAt = payload["created_at"] as? String ?: "",
        modifiedAt = payload["modified_at"] as? String ?: "",
        sections = sections,
        brief = payload["brief"] as? String ?: "",
    )
}

/** Return the chunk as the JSON document the store writes. */
internal fun chunkToMap(chunk: SourceChunk): Map<String, Any?> = linkedMapOf(
    "chunk_id" to chunk.chunkId,
    "document_id" to chunk.documentId,
    "text" to chunk.text,
    "char_start" to chunk.charStart,
    "char_end" to chunk.charEnd,
    "byte_start" to chunk.byteStart,
    "byte_end" to chunk.byteEnd,
    "token_count" to chunk.tokenCount,
    "start_char" to chunk.startChar,
    "end_char" to chunk.endChar,
    "metadata" to chunk.metadata,
)

/** Return the chunk a stored line describes. */
internal fun parseChunk(line: String, bookId: String, lineNumber: Int): SourceChunk {
    val payload = try {
        Json.parseObject(line)
    } catch (exc: RuntimeException) {
        throw LibraryError(
            "line $lineNumber of the chunk file of book '$bookId' is not valid: ${exc.message}",
            mapOf("book_id" to bookId, "line" to lineNumber),
        )
    }
    @Suppress("UNCHECKED_CAST")
    val metadata = payload["metadata"] as? Map<String, Any?> ?: emptyMap()
    return try {
        SourceChunk(
            chunkId = payload["chunk_id"] as? String ?: "",
            documentId = payload["document_id"] as? String ?: "",
            text = payload["text"] as? String ?: "",
            charStart = (payload["char_start"] as? Number)?.toInt() ?: 0,
            charEnd = (payload["char_end"] as? Number)?.toInt() ?: 0,
            byteStart = (payload["byte_start"] as? Number)?.toInt() ?: 0,
            byteEnd = (payload["byte_end"] as? Number)?.toInt() ?: 0,
            tokenCount = (payload["token_count"] as? Number)?.toInt() ?: 0,
            startChar = (payload["start_char"] as? Number)?.toInt() ?: 0,
            endChar = (payload["end_char"] as? Number)?.toInt() ?: 0,
            metadata = metadata,
        )
    } catch (exc: RuntimeException) {
        throw LibraryError(
            "line $lineNumber of the chunk file of book '$bookId' is not valid: ${exc.message}",
            mapOf("book_id" to bookId, "line" to lineNumber),
        )
    }
}

/**
 * Write [text] to [path] in UTF-8, atomically.
 *
 * The text is written to a temporary sibling and then moved into place, which
 * is atomic on every platform LMeditor runs on. An interrupted write therefore
 * leaves the previous version of the file intact instead of a half-written
 * book.
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
            // The temporary file could not be removed; the write still failed.
        }
        throw exc
    }
}

/** Delete a directory and everything it holds. */
private fun deleteRecursively(directory: Path) {
    Files.walk(directory).use { stream ->
        stream.sorted(Comparator.reverseOrder()).forEach { path ->
            try {
                Files.deleteIfExists(path)
            } catch (exc: IOException) {
                // The file could not be removed; the walk continues.
            }
        }
    }
}

/**
 * Serialise a value as the JSON text the store writes.
 *
 * The output is the shape the Python tier's `model_dump_json(indent=2)`
 * produces: two-space indentation, a space after every colon, one element per
 * line inside an array, and no trailing newline. A file written here is read
 * back by the Python tier without a translation step.
 */
internal object PrettyJson {

    /** Return [value] as indented JSON text. */
    fun write(value: Any?): String {
        val out = StringBuilder()
        writeValue(value, out, 0)
        return out.toString()
    }

    private fun writeValue(value: Any?, out: StringBuilder, depth: Int) {
        when (value) {
            null -> out.append("null")
            is String -> writeString(value, out)
            is Boolean -> out.append(if (value) "true" else "false")
            is Int -> out.append(value.toString())
            is Long -> out.append(value.toString())
            is Double -> out.append(if (value == value.toLong().toDouble()) value.toString() else value.toString())
            is Number -> out.append(value.toString())
            is Map<*, *> -> {
                out.append("{\n")
                var first = true
                for ((key, entry) in value) {
                    if (!first) {
                        out.append(",\n")
                    }
                    first = false
                    indent(out, depth + 1)
                    writeString(key.toString(), out)
                    out.append(": ")
                    writeValue(entry, out, depth + 1)
                }
                if (!first) {
                    out.append('\n')
                }
                indent(out, depth)
                out.append('}')
            }
            is Iterable<*> -> {
                out.append("[\n")
                var first = true
                for (entry in value) {
                    if (!first) {
                        out.append(",\n")
                    }
                    first = false
                    indent(out, depth + 1)
                    writeValue(entry, out, depth + 1)
                }
                if (!first) {
                    out.append('\n')
                }
                indent(out, depth)
                out.append(']')
            }
            is Array<*> -> writeValue(value.toList(), out, depth)
            else -> writeString(value.toString(), out)
        }
    }

    private fun indent(out: StringBuilder, depth: Int) {
        for (index in 0 until depth) {
            out.append("  ")
        }
    }

    private fun writeString(value: String, out: StringBuilder) {
        out.append('"')
        for (ch in value) {
            when (ch) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                '\b' -> out.append("\\b")
                '\u000C' -> out.append("\\f")
                else ->
                    if (ch < ' ' || ch == '\u2028' || ch == '\u2029') {
                        out.append("\\u").append(ch.code.toString(16).padStart(4, '0'))
                    } else {
                        out.append(ch)
                    }
            }
        }
        out.append('"')
    }
}
