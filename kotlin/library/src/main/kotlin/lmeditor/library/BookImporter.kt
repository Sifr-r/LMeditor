package lmeditor.library

import lmeditor.core.Chunker
import lmeditor.core.LibraryError
import lmeditor.core.SourceChunk
import lmeditor.text.SentenceAwareChunker
import lmeditor.word.ListMarker
import lmeditor.word.WordBlock
import lmeditor.word.WordBlockKind
import lmeditor.word.WordDocument
import lmeditor.word.WordReader
import lmeditor.word.WordRun
import lmeditor.word.WordTable
import lmeditor.word.WordTableCell
import lmeditor.word.WordTableRow
import java.nio.file.Path
import java.util.Base64

/** The block kinds that start a new section of a book. */
private val SECTION_START_KINDS: Set<WordBlockKind> = setOf(WordBlockKind.HEADING, WordBlockKind.TITLE)

/** The chunk size a book is indexed with when the caller names no chunker. */
const val DEFAULT_CHUNK_SIZE: Int = 120

/** The overlap between two consecutive chunks of a book. */
const val DEFAULT_OVERLAP_SIZE: Int = 25

/**
 * One section of an imported Word document.
 *
 * It carries both halves of the section: the text the editor works on, and the
 * document it was imported from -- the blocks, which are the structure the
 * author wrote.
 *
 * @property sectionId the section's id.
 * @property title the section's title, taken from the heading that starts it.
 * @property content the text of the section's body.
 * @property origin the blocks the section was imported from, when it has any.
 */
data class ImportedSection(
    /** The section's id. */
    val sectionId: String,
    /** The section's title. */
    val title: String,
    /** The text of the section's body. */
    val content: String,
    /** The blocks the section was imported from, when it has any. */
    val origin: Map<String, Any?>? = null,
)

/**
 * Split a Word document into the sections of a book, with their origins.
 *
 * Every heading -- and the document title, which is a heading of its own --
 * starts a section and becomes that section's title; the blocks under it become
 * the section's body. Bodies are joined with a blank line, which is how
 * `WordDocument.plainText` joins them.
 *
 * The heading that starts a section is kept apart from its body, because the
 * heading is the section's *title* and not its text: the body is what the
 * editor edits. Every block of the body is kept, including the ones that render
 * to nothing -- a page break is structure, and a body that lost its page breaks
 * would lose the shape of the document.
 *
 * Text that appears before the first heading is kept in a leading section with
 * an empty title, rather than dropped: a document that opens with a paragraph
 * and then a heading has content the book must not lose. A document with no
 * heading at all becomes a single section.
 */
fun sectionsWithOrigin(document: WordDocument): List<ImportedSection> {
    val sections = mutableListOf<ImportedSection>()
    var currentTitle = ""
    var currentHeading: WordBlock? = null
    var currentBody = mutableListOf<WordBlock>()
    var seenHeading = false

    for (block in document.blocks) {
        val rendered = block.renderText()
        if (block.kind in SECTION_START_KINDS) {
            if (seenHeading) {
                sections.add(makeSection(sections.size + 1, currentTitle, currentHeading, currentBody))
            } else if (joined(currentBody).isNotEmpty()) {
                // The document opened with prose: it keeps a section of its own.
                sections.add(makeSection(sections.size + 1, "", null, currentBody))
            }
            currentTitle = rendered.trim()
            currentHeading = block
            currentBody = mutableListOf()
            seenHeading = true
            continue
        }
        currentBody.add(block)
    }

    if (seenHeading) {
        sections.add(makeSection(sections.size + 1, currentTitle, currentHeading, currentBody))
    } else if (joined(currentBody).isNotEmpty()) {
        sections.add(makeSection(sections.size + 1, "", null, currentBody))
    }
    return sections
}

/**
 * Split a Word document into the sections of a book.
 *
 * @return the sections, as `(sectionId, title, content)` triples, in the order
 *   they appear in the document.
 */
fun sectionsFromWordDocument(document: WordDocument): List<Triple<String, String, String>> =
    sectionsWithOrigin(document).map { Triple(it.sectionId, it.title, it.content) }

/**
 * Import a Word document as a book, one section per heading.
 *
 * The document's sections are stored, chunked and indexed, and the book's brief
 * is rebuilt so that the working set of every section carries a digest of the
 * whole book.
 *
 * The document is kept as well: every section stores the blocks it was made
 * of, and the book stores the document's notes, its media, its styles and its
 * page setup. An export can therefore put the document's tables, images and
 * tracked changes back into the file the writer receives.
 *
 * @param store the library the book is written into.
 * @param bookId the id of the book.
 * @param title the book's title.
 * @param document the document to import.
 * @param chunker the chunker the content is indexed with.
 * @return the manifest of the imported book.
 */
fun importWordDocument(
    store: BookStore,
    bookId: String,
    title: String,
    document: WordDocument,
    chunker: Chunker? = null,
): lmeditor.core.BookManifest {
    val sections = sectionsWithOrigin(document)
    return importSectionsInternal(
        store = store,
        bookId = bookId,
        title = title,
        sections = sections.map { ImportedSection(it.sectionId, it.title, it.content, it.origin) },
        origin = bookOrigin(document),
        chunker = chunker,
    )
}

/**
 * Import the Word document at [path] as a book, one section per heading.
 *
 * @throws lmeditor.core.WordParseError the document could not be parsed.
 */
fun importWordDocument(
    store: BookStore,
    bookId: String,
    title: String,
    path: Path,
    chunker: Chunker? = null,
): lmeditor.core.BookManifest =
    importWordDocument(store, bookId, title, WordReader().read(path), chunker)

/**
 * Import sections into a book, chunk their content and index the result.
 *
 * Every section is stored on its own, its content is chunked, and the ids of
 * the chunks are recorded against the section they came from. The book's brief
 * is then rebuilt from its section index, so the working set of every section
 * carries a digest of the whole book rather than an empty string.
 *
 * The import is idempotent: importing into a book that already exists updates
 * the sections it names and leaves the others alone.
 *
 * A section imported this way has no origin: there is no Word document behind
 * it, so there is nothing to keep but its text. A section that already had one
 * loses it, because an origin that outlived the body it belonged to would
 * bring back the old structure on export.
 *
 * @param store the library the book is written into.
 * @param bookId the id of the book.
 * @param title the book's title.
 * @param sections the sections to import, as `(sectionId, title, content)`
 *   triples.
 * @param chunker the chunker the content is indexed with. The default is a
 *   sentence-aware chunker with a window of 120 characters and an overlap of
 *   25.
 * @return the manifest of the imported book.
 */
fun importSections(
    store: BookStore,
    bookId: String,
    title: String,
    sections: List<Triple<String, String, String>>,
    chunker: Chunker? = null,
): lmeditor.core.BookManifest =
    importSectionsInternal(
        store = store,
        bookId = bookId,
        title = title,
        sections = sections.map { ImportedSection(it.first, it.second, it.third, null) },
        origin = null,
        chunker = chunker,
    )

/**
 * Store, chunk and index the sections of a book.
 *
 * The origin of the book -- the document it was imported from -- is written
 * last, so that a failure while the sections are written leaves the book with
 * the origin it had rather than with an origin that does not match its
 * sections.
 */
private fun importSectionsInternal(
    store: BookStore,
    bookId: String,
    title: String,
    sections: List<ImportedSection>,
    origin: Map<String, Any?>?,
    chunker: Chunker?,
): lmeditor.core.BookManifest {
    val manifest = store.create(bookId, title)
    val activeChunker = chunker
        ?: SentenceAwareChunker(chunkSize = DEFAULT_CHUNK_SIZE, overlapSize = DEFAULT_OVERLAP_SIZE)

    val allChunks = mutableListOf<SourceChunk>()
    for (section in sections) {
        store.addSection(
            bookId = bookId,
            sectionId = section.sectionId,
            content = section.content,
            title = section.title,
            origin = section.origin,
        )
        val sectionChunks = chunkSection(activeChunker, section.content, bookId, manifest.title)
        store.setSectionChunks(bookId, section.sectionId, sectionChunks.map { it.chunkId })
        allChunks.addAll(sectionChunks)
    }

    if (origin != null) {
        val previous = store.readBookOrigin(bookId)
        store.setBookOrigin(bookId, mergeOrigins(previous, origin))
    }
    store.putChunks(bookId, allChunks)
    store.updateBrief(bookId, buildBrief(store, bookId))
    return store.open(bookId)
}

/**
 * Return the chunks of one section's content.
 *
 * An empty section has no chunks: the chunker refuses blank text, and a
 * section with no text has nothing to retrieve.
 */
private fun chunkSection(
    chunker: Chunker,
    content: String,
    documentId: String,
    documentTitle: String,
): List<SourceChunk> {
    if (content.isBlank()) {
        return emptyList()
    }
    return try {
        chunker.chunk(
            text = content,
            documentId = documentId,
            metadata = mapOf("document_title" to documentTitle),
        )
    } catch (exc: RuntimeException) {
        throw LibraryError(
            "could not chunk the content of section '$documentId': ${exc.message}",
            mapOf("document_id" to documentId, "reason" to (exc.message ?: "")),
        )
    }
}

/** Return the section for one part of a document. */
private fun makeSection(
    index: Int,
    title: String,
    heading: WordBlock?,
    body: List<WordBlock>,
): ImportedSection {
    val blocks = body.toList()
    val text = joined(blocks)
    return ImportedSection(
        sectionId = "section_${"%04d".format(index)}",
        title = title,
        content = text,
        origin = sectionOrigin(heading, blocks, text),
    )
}

/**
 * Return the text of a section's body, joined with a blank line.
 *
 * A block that renders to nothing -- a page break, an empty paragraph --
 * contributes no text, but it is still one of the section's blocks: the caller
 * keeps them all.
 */
internal fun joined(blocks: List<WordBlock>): String =
    blocks.map { it.renderText() }.filter { it.isNotBlank() }.joinToString("\n\n")

// --- The Word origin codec ---------------------------------------------------

/**
 * Return the origin record of a section: the blocks it was imported from.
 *
 * The record is the JSON document the store keeps beside the section's text.
 * Its keys are the ones the Python tier uses -- `heading`, `blocks` and
 * `text` -- so a file written here has the same shape as a file written there.
 */
internal fun sectionOrigin(
    heading: WordBlock?,
    blocks: List<WordBlock>,
    text: String,
): Map<String, Any?> = linkedMapOf(
    "heading" to heading?.let { wordBlockToMap(it) },
    "blocks" to blocks.map { wordBlockToMap(it) },
    "text" to text,
)

/**
 * Return the document-level record of a Word document.
 *
 * It holds everything that is not a block: the notes, the comments, the
 * headers and footers, the page setup, the styles, the media and the package
 * parts. An export carries it through, so the file the writer receives still
 * holds the document's images, its charts and its notes.
 */
internal fun bookOrigin(document: WordDocument): Map<String, Any?> = linkedMapOf(
    "properties" to linkedMapOf(
        "title" to document.properties.title,
        "author" to document.properties.author,
        "subject" to document.properties.subject,
        "keywords" to document.properties.keywords,
        "comments" to document.properties.comments,
        "created" to document.properties.created?.toString(),
        "modified" to document.properties.modified?.toString(),
        "revision" to document.properties.revision,
    ),
    "footnotes" to document.footnotes.map { note ->
        linkedMapOf(
            "footnote_id" to note.footnoteId,
            "text" to note.text,
            "runs" to note.runs.map { wordRunToMap(it) },
        )
    },
    "endnotes" to document.endnotes.map { note ->
        linkedMapOf(
            "endnote_id" to note.endnoteId,
            "text" to note.text,
            "runs" to note.runs.map { wordRunToMap(it) },
        )
    },
    "comments" to document.comments.map { comment ->
        linkedMapOf(
            "comment_id" to comment.commentId,
            "author" to comment.author,
            "initials" to comment.initials,
            "date" to comment.date?.toString(),
            "text" to comment.text,
            "runs" to comment.runs.map { wordRunToMap(it) },
            "anchor_block_id" to comment.anchorBlockId,
            "anchor_text" to comment.anchorText,
        )
    },
    "headers" to document.headers,
    "footers" to document.footers,
    "sections" to document.sections.map { section ->
        linkedMapOf(
            "page_width" to section.pageWidth,
            "page_height" to section.pageHeight,
            "orientation" to section.orientation,
            "margin_top" to section.marginTop,
            "margin_right" to section.marginRight,
            "margin_bottom" to section.marginBottom,
            "margin_left" to section.marginLeft,
            "end_block_id" to section.endBlockId,
        )
    },
    "styles" to document.styles.map { style ->
        linkedMapOf(
            "style_id" to style.styleId,
            "name" to style.name,
            "based_on" to style.basedOn,
            "style_type" to style.styleType,
            "num_id" to style.numId,
            "num_level" to style.numLevel,
        )
    },
    "media" to document.media.map { media ->
        linkedMapOf(
            "media_id" to media.mediaId,
            "part_name" to media.partName,
            "content_type" to media.contentType,
            "data_base64" to Base64.getEncoder().encodeToString(media.data),
            "relationship_id" to media.relationshipId,
        )
    },
    "parts" to document.parts.map { part ->
        linkedMapOf(
            "part_name" to part.partName,
            "content_type" to part.contentType,
            "data_base64" to Base64.getEncoder().encodeToString(part.data),
            "relationship_id" to part.relationshipId,
            "relationship_type" to part.relationshipType,
        )
    },
)

/** Return the origin of a book after a new document was imported into it. */
internal fun mergeOrigins(
    previous: Map<String, Any?>?,
    current: Map<String, Any?>,
): Map<String, Any?> {
    if (previous == null) {
        return current
    }
    val merged = LinkedHashMap<String, Any?>()
    for (key in current.keys) {
        merged[key] = current[key]
    }
    // The records the new document does not carry are kept, because the
    // sections that were left alone may still refer to them: a section that
    // keeps its image needs the media the image lives in, and a section that
    // keeps a footnote marker needs the footnote.
    for (key in listOf("footnotes", "endnotes", "comments", "styles", "media", "parts")) {
        merged[key] = mergeById(previous[key], current[key])
    }
    return merged
}

/**
 * Return the records of both, the current ones winning on a shared id.
 *
 * The order is stable: a record that both documents carry keeps the place it
 * had, so the numbers Word gives the notes do not move when a document is
 * imported again.
 */
private fun mergeById(previous: Any?, current: Any?): List<Any?> {
    val merged = LinkedHashMap<String, Any?>()
    for (entry in (previous as? List<*>).orEmpty()) {
        val map = entry as? Map<*, *> ?: continue
        val key = map["footnote_id"] ?: map["endnote_id"] ?: map["comment_id"] ?:
            map["style_id"] ?: map["media_id"] ?: map["part_name"] ?: continue
        merged[key.toString()] = entry
    }
    for (entry in (current as? List<*>).orEmpty()) {
        val map = entry as? Map<*, *> ?: continue
        val key = map["footnote_id"] ?: map["endnote_id"] ?: map["comment_id"] ?:
            map["style_id"] ?: map["media_id"] ?: map["part_name"] ?: continue
        merged[key.toString()] = entry
    }
    return merged.values.toList()
}

/** Return the block as the JSON document the origin holds. */
internal fun wordBlockToMap(block: WordBlock): Map<String, Any?> = linkedMapOf(
    "block_id" to block.blockId,
    "kind" to block.kind.wire,
    "text" to block.text,
    "level" to block.level,
    "list_marker" to block.listMarker?.wire,
    "style" to block.style,
    "runs" to block.runs.map { wordRunToMap(it) },
    "table" to block.table?.let { wordTableToMap(it) },
)

/** Return the run as the JSON document the origin holds. */
internal fun wordRunToMap(run: WordRun): Map<String, Any?> = linkedMapOf(
    "text" to run.text,
    "bold" to run.bold,
    "italic" to run.italic,
    "underline" to run.underline,
    "superscript" to run.superscript,
    "subscript" to run.subscript,
    "strike" to run.strike,
    "font" to run.font,
    "size" to run.size,
    "color" to run.color,
    "highlight" to run.highlight,
    "hyperlink" to run.hyperlink,
    "footnote_ref" to run.footnoteRef,
    "endnote_ref" to run.endnoteRef,
    "comment_refs" to run.commentRefs,
    "image_ref" to run.imageRef,
    "equation_xml" to run.equationXml,
    "part_ref" to run.partRef,
)

/** Return the table as the JSON document the origin holds. */
internal fun wordTableToMap(table: WordTable): Map<String, Any?> = linkedMapOf(
    "header_rows" to table.headerRows,
    "column_widths" to table.columnWidths,
    "rows" to table.rows.map { row ->
        linkedMapOf(
            "cells" to row.cells.map { cell ->
                linkedMapOf(
                    "text" to cell.text,
                    "row_span" to cell.rowSpan,
                    "col_span" to cell.colSpan,
                    "is_header" to cell.isHeader,
                    "is_merge_continuation" to cell.isMergeContinuation,
                    "width" to cell.width,
                    "runs" to cell.runs.map { wordRunToMap(it) },
                    "tables" to cell.tables.map { wordTableToMap(it) },
                )
            },
        )
    },
)

/** Return the block a stored document holds. */
internal fun wordBlockFromMap(map: Map<*, *>): WordBlock {
    val kind = WordBlockKind.fromWire(map["kind"] as? String ?: "paragraph")
    val runs = (map["runs"] as? List<*>).orEmpty().mapNotNull { entry ->
        (entry as? Map<*, *>)?.let { wordRunFromMap(it) }
    }
    val table = (map["table"] as? Map<*, *>)?.let { wordTableFromMap(it) }
    return WordBlock(
        blockId = map["block_id"] as? String ?: "block",
        kind = kind,
        text = map["text"] as? String ?: "",
        level = (map["level"] as? Number)?.toInt() ?: 0,
        listMarker = (map["list_marker"] as? String)?.let { ListMarker.fromWire(it) },
        style = map["style"] as? String,
        runs = runs,
        table = table,
    )
}

/** Return the run a stored document holds. */
internal fun wordRunFromMap(map: Map<*, *>): WordRun = WordRun(
    text = map["text"] as? String ?: "",
    bold = map["bold"] as? Boolean ?: false,
    italic = map["italic"] as? Boolean ?: false,
    underline = map["underline"] as? Boolean ?: false,
    superscript = map["superscript"] as? Boolean ?: false,
    subscript = map["subscript"] as? Boolean ?: false,
    strike = map["strike"] as? Boolean ?: false,
    font = map["font"] as? String,
    size = (map["size"] as? Number)?.toInt(),
    color = map["color"] as? String,
    highlight = map["highlight"] as? String,
    hyperlink = map["hyperlink"] as? String,
    footnoteRef = map["footnote_ref"] as? String,
    endnoteRef = map["endnote_ref"] as? String,
    commentRefs = (map["comment_refs"] as? List<*>).orEmpty().mapNotNull { it as? String },
    imageRef = map["image_ref"] as? String,
    equationXml = map["equation_xml"] as? String,
    partRef = map["part_ref"] as? String,
)

/** Return the table a stored document holds. */
internal fun wordTableFromMap(map: Map<*, *>): WordTable {
    val rows = (map["rows"] as? List<*>).orEmpty().mapNotNull { entry ->
        val row = entry as? Map<*, *> ?: return@mapNotNull null
        WordTableRow(
            cells = (row["cells"] as? List<*>).orEmpty().mapNotNull { cellEntry ->
                val cell = cellEntry as? Map<*, *> ?: return@mapNotNull null
                WordTableCell(
                    text = cell["text"] as? String ?: "",
                    rowSpan = (cell["row_span"] as? Number)?.toInt() ?: 1,
                    colSpan = (cell["col_span"] as? Number)?.toInt() ?: 1,
                    isHeader = cell["is_header"] as? Boolean ?: false,
                    isMergeContinuation = cell["is_merge_continuation"] as? Boolean ?: false,
                    width = (cell["width"] as? Number)?.toInt(),
                    runs = (cell["runs"] as? List<*>).orEmpty().mapNotNull { runEntry ->
                        (runEntry as? Map<*, *>)?.let { wordRunFromMap(it) }
                    },
                    tables = (cell["tables"] as? List<*>).orEmpty().mapNotNull { tableEntry ->
                        (tableEntry as? Map<*, *>)?.let { wordTableFromMap(it) }
                    },
                )
            },
        )
    }
    return WordTable(
        rows = rows,
        headerRows = (map["header_rows"] as? Number)?.toInt() ?: 0,
        columnWidths = (map["column_widths"] as? List<*>).orEmpty().mapNotNull { (it as? Number)?.toInt() },
    )
}
