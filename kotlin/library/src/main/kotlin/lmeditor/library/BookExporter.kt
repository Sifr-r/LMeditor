package lmeditor.library

import lmeditor.core.BookManifest
import lmeditor.core.BookSection
import lmeditor.core.DocumentDraft
import lmeditor.core.LibraryError
import lmeditor.core.SectionDraft
import lmeditor.word.WordBlock
import lmeditor.word.WordBlockKind
import lmeditor.word.WordComment
import lmeditor.word.WordDocument
import lmeditor.word.WordEndnote
import lmeditor.word.WordFootnote
import lmeditor.word.WordManuscript
import lmeditor.word.WordProperties
import lmeditor.word.WordRun
import lmeditor.word.WordSection
import lmeditor.word.WordStyle
import lmeditor.word.WordMedia
import lmeditor.word.WordPart
import java.util.Base64

/**
 * Build the Word document for a book.
 *
 * A book that was imported from a Word document is rebuilt from that document:
 * a section whose text has not changed keeps the blocks it was imported with,
 * and a section whose text has changed keeps the structure the text editor
 * could not see -- its heading, its tables, its images and its page breaks --
 * and takes only its prose from its new text. A book that was created from a
 * list of sections has no document behind it and is rendered from its text
 * alone.
 *
 * @param store the library the book lives in.
 * @param bookId the book to render.
 * @return the document model, ready for `WordWriter.write`.
 * @throws lmeditor.core.BookNotFoundError the book is not in the library.
 * @throws lmeditor.core.SectionNotFoundError a section the manifest names is
 *   missing from the book directory.
 */
fun buildBookDocument(store: BookStore, bookId: String): WordDocument {
    val manifest = store.open(bookId)
    val origin = store.readBookOrigin(bookId)
    if (origin == null) {
        return manuscriptDocument(store, manifest, bookId)
    }
    return try {
        originDocument(store, manifest, bookId, origin)
    } catch (exc: LibraryError) {
        // The origin could not be understood. Rendering the book from its text
        // keeps the words; giving up would keep neither.
        manuscriptDocument(store, manifest, bookId)
    }
}

/**
 * Render a book from its text, through the manuscript bridge.
 *
 * This is the path a book without an origin takes, and the path a book with an
 * origin falls back to when that origin cannot be understood.
 */
private fun manuscriptDocument(
    store: BookStore,
    manifest: BookManifest,
    bookId: String,
): WordDocument {
    val sections = manifest.sections.map { entry ->
        val content = store.readSection(bookId, entry.sectionId)
        SectionDraft(
            sectionId = entry.sectionId,
            title = entry.title.ifEmpty { entry.sectionId },
            content = content,
            order = entry.order,
            citedChunkIds = entry.chunkIds,
        )
    }
    val draft = DocumentDraft(
        documentId = bookId,
        title = manifest.title.ifEmpty { bookId },
        sections = sections,
    )
    return WordManuscript.buildManuscriptDocument(draft)
}

/**
 * Rebuild a book from the Word document it was imported from.
 *
 * Every section is read once, and its file yields both its text and the blocks
 * it was made of, so the decision between an untouched section and an edited
 * one costs no second read.
 */
private fun originDocument(
    store: BookStore,
    manifest: BookManifest,
    bookId: String,
    origin: Map<String, Any?>,
): WordDocument {
    val records = manifest.sections.map { entry ->
        store.readSectionRecord(bookId, entry.sectionId)
    }
    val ids = IdAllocator(
        mapOf(
            "block" to records
                .flatMap { originBlocks(it.origin) }
                .map { it.blockId }
                .toSet(),
            "footnote" to originList(origin, "footnotes").map { it["footnote_id"]?.toString() ?: "" }.toSet(),
            "endnote" to originList(origin, "endnotes").map { it["endnote_id"]?.toString() ?: "" }.toSet(),
            "comment" to originList(origin, "comments").map { it["comment_id"]?.toString() ?: "" }.toSet(),
            "list_group" to records
                .flatMap { originBlocks(it.origin) }
                .mapNotNull { it.listGroup }
                .toSet(),
        ),
    )

    val blocks = mutableListOf<WordBlock>()
    val footnotes = mutableListOf<WordFootnote>()
    val endnotes = mutableListOf<WordEndnote>()
    val comments = mutableListOf<WordComment>()
    for (index in manifest.sections.indices) {
        val entry = manifest.sections[index]
        val record = records[index]
        val rendered = sectionBlocks(entry, record, origin, ids)
        blocks.addAll(rendered.blocks)
        footnotes.addAll(rendered.footnotes)
        endnotes.addAll(rendered.endnotes)
        comments.addAll(rendered.comments)
    }

    return WordDocument(
        blocks = blocks,
        properties = propertiesFrom(origin),
        footnotes = originFootnotes(origin) + footnotes,
        endnotes = originEndnotes(origin) + endnotes,
        comments = originComments(origin) + comments,
        headers = (origin["headers"] as? List<*>).orEmpty().mapNotNull { it as? String },
        footers = (origin["footers"] as? List<*>).orEmpty().mapNotNull { it as? String },
        sections = sectionsFrom(origin),
        styles = stylesFrom(origin),
        media = mediaFrom(origin),
        parts = partsFrom(origin),
    )
}

/** The blocks of one rebuilt section, and the notes they refer to. */
private class SectionBlocks(
    val blocks: List<WordBlock>,
    val footnotes: List<WordFootnote>,
    val endnotes: List<WordEndnote>,
    val comments: List<WordComment>,
)

/**
 * Return the blocks of one section of a rebuilt book.
 *
 * A section whose text still is the text its blocks rendered to is returned as
 * it was imported: that is what keeps its table, its image and its tracked
 * change alive. A section whose text has changed keeps the structure the text
 * editor could not see and takes only its prose from the new text.
 */
private fun sectionBlocks(
    entry: BookSection,
    record: SectionRecord,
    origin: Map<String, Any?>,
    ids: IdAllocator,
): SectionBlocks {
    val sectionOrigin = record.origin
    if (sectionOrigin != null && record.content == (sectionOrigin["text"] as? String ?: "")) {
        if (originIsUsable(sectionOrigin, origin)) {
            return SectionBlocks(originBlocks(sectionOrigin), emptyList(), emptyList(), emptyList())
        }
    }
    return editedSection(entry, record, origin, ids)
}

/**
 * Return the blocks of a section whose text the operator changed.
 *
 * The section is rebuilt around the structure the text editor could not see.
 * The heading is the block the section was imported with, so that its style
 * survives; a block that renders to no text -- an image, a page break, an
 * empty paragraph -- is kept where it was, because the editor never saw it and
 * so cannot have removed it; and the prose is taken from the new text.
 */
private fun editedSection(
    entry: BookSection,
    record: SectionRecord,
    origin: Map<String, Any?>,
    ids: IdAllocator,
): SectionBlocks {
    val sectionOrigin = record.origin
    if (sectionOrigin == null || !originIsUsable(sectionOrigin, origin)) {
        // There is no origin to keep, or the origin refers to notes the
        // document does not have: the section is rendered from its text alone.
        return renderedSection(entry, record, ids)
    }
    val heading = originHeading(sectionOrigin)
    val body = originBody(sectionOrigin)
    val plan = planTables(body, record.content)
    val prose = renderContent(entry, plan.text, ids, keepHeading = heading == null)

    // A table the operator changed is rebuilt on its own: its text is turned
    // back into a markdown table, and the bridge renders it as a Word table
    // again.
    val rebuilt = mutableListOf<WordBlock?>()
    val rebuiltNotes = mutableListOf<SectionBlocks>()
    for (table in plan.tables) {
        if (table.status != TableStatus.REBUILT) {
            rebuilt.add(null)
            continue
        }
        val rendered = renderContent(entry, table.markdown, ids, keepHeading = false)
        rebuiltNotes.add(rendered)
        rebuilt.add(rendered.blocks.firstOrNull { it.kind == WordBlockKind.TABLE })
    }

    val blocks = mutableListOf<WordBlock>()
    if (heading != null) {
        blocks.add(heading)
    }
    var contentIndex = 0
    var tableIndex = 0
    for (block in body) {
        if (block.kind == WordBlockKind.TABLE) {
            val table = plan.tables[tableIndex]
            val rebuiltTable = rebuilt[tableIndex]
            tableIndex++
            val kept = when (table.status) {
                TableStatus.KEPT -> table.original
                TableStatus.REBUILT -> rebuiltTable
                TableStatus.DROPPED -> null
            }
            if (kept != null) {
                blocks.add(kept)
            }
            continue
        }
        if (block.renderText().isBlank()) {
            // An image, a page break, an empty paragraph: the text editor
            // could not see it, so it could not have removed it.
            blocks.add(block)
            continue
        }
        val next = prose.blocks.getOrNull(contentIndex)
        if (next != null) {
            blocks.add(next)
            contentIndex++
        }
    }
    // The prose the operator wrote beyond the original's is appended.
    blocks.addAll(prose.blocks.subList(contentIndex, prose.blocks.size))

    return SectionBlocks(
        blocks = blocks,
        footnotes = prose.footnotes + rebuiltNotes.flatMap { it.footnotes },
        endnotes = prose.endnotes + rebuiltNotes.flatMap { it.endnotes },
        comments = prose.comments + rebuiltNotes.flatMap { it.comments },
    )
}

/**
 * Return the blocks a section was imported from, heading first.
 *
 * The heading comes first because it came first in the document: the section's
 * title is the block that starts it.
 */
internal fun originBlocks(sectionOrigin: Map<String, Any?>?): List<WordBlock> {
    if (sectionOrigin == null) {
        return emptyList()
    }
    val blocks = originBody(sectionOrigin).toMutableList()
    val heading = originHeading(sectionOrigin)
    if (heading != null) {
        blocks.add(0, heading)
    }
    return blocks
}

/**
 * Return the heading a section was imported with.
 *
 * The heading is the block that starts the section. The operator edits the
 * body, not the title, so the heading is kept as it was -- with its style and
 * its runs.
 */
private fun originHeading(sectionOrigin: Map<String, Any?>?): WordBlock? =
    (sectionOrigin?.get("heading") as? Map<*, *>)?.let { wordBlockFromMap(it) }

/**
 * Return the blocks a section's body was made of.
 *
 * The heading is not among them: the heading is the section's title, and the
 * title is kept apart from the text the editor works on.
 */
private fun originBody(sectionOrigin: Map<String, Any?>?): List<WordBlock> =
    (sectionOrigin?.get("blocks") as? List<*>).orEmpty().mapNotNull { entry ->
        (entry as? Map<*, *>)?.let { wordBlockFromMap(it) }
    }

/**
 * Report whether a section's origin refers only to notes the book holds.
 *
 * A run that points at a footnote the document does not have would make the
 * exported package invalid, so such a section is rendered from its text rather
 * than exported as it was.
 */
private fun originIsUsable(sectionOrigin: Map<String, Any?>, origin: Map<String, Any?>): Boolean {
    val footnotes = originList(origin, "footnotes").map { it["footnote_id"]?.toString() }.toSet()
    val endnotes = originList(origin, "endnotes").map { it["endnote_id"]?.toString() }.toSet()
    val comments = originList(origin, "comments").map { it["comment_id"]?.toString() }.toSet()
    for (block in originBlocks(sectionOrigin)) {
        for (run in blockRuns(block)) {
            val footnoteRef = run.footnoteRef
            if (footnoteRef != null && footnoteRef !in footnotes) {
                return false
            }
            val endnoteRef = run.endnoteRef
            if (endnoteRef != null && endnoteRef !in endnotes) {
                return false
            }
            if (run.commentRefs.any { it !in comments }) {
                return false
            }
        }
    }
    return true
}

/** Return the runs of a block, including the ones inside its table cells. */
private fun blockRuns(block: WordBlock): List<WordRun> {
    val runs = mutableListOf<WordRun>()
    runs.addAll(block.runs)
    val table = block.table ?: return runs
    for (row in table.rows) {
        for (cell in row.cells) {
            runs.addAll(cell.runs)
            for (nested in cell.tables) {
                for (nestedRow in nested.rows) {
                    for (nestedCell in nestedRow.cells) {
                        runs.addAll(nestedCell.runs)
                    }
                }
            }
        }
    }
    return runs
}

/**
 * Render one section from its text, through the manuscript bridge.
 *
 * The bridge's own heading for the section is kept: the section has no origin
 * to take a heading from.
 */
private fun renderedSection(
    entry: BookSection,
    record: SectionRecord,
    ids: IdAllocator,
): SectionBlocks = renderContent(entry, record.content, ids, keepHeading = true)

/**
 * Render a text through the manuscript bridge.
 *
 * The bridge is what turns the editor's markdown into real Word structure: a
 * heading, lists, a table, bold and italic runs, a fenced code block.
 *
 * @param keepHeading whether the bridge's own heading for the section is kept.
 *   A section that keeps the heading it was imported with does not take it.
 */
private fun renderContent(
    entry: BookSection,
    content: String,
    ids: IdAllocator,
    keepHeading: Boolean,
): SectionBlocks {
    val title = entry.title.ifEmpty { entry.sectionId }
    val draft = DocumentDraft(
        documentId = entry.sectionId,
        title = title,
        sections = listOf(
            SectionDraft(
                sectionId = entry.sectionId,
                title = title,
                content = content,
                order = entry.order,
                citedChunkIds = entry.chunkIds,
            ),
        ),
    )
    val rendered = WordManuscript.buildManuscriptDocument(draft)
    // The bridge puts the document's title first, and the section's own heading
    // after it.
    val blocks = rendered.blocks.drop(if (keepHeading) 1 else 2)

    val footnoteMap = rendered.footnotes.associate { it.footnoteId to ids.take("footnote", it.footnoteId) }
    val endnoteMap = rendered.endnotes.associate { it.endnoteId to ids.take("endnote", it.endnoteId) }
    val commentMap = rendered.comments.associate { it.commentId to ids.take("comment", it.commentId) }

    val newFootnotes = rendered.footnotes.map { note ->
        note.copy(
            footnoteId = footnoteMap[note.footnoteId] ?: note.footnoteId,
            runs = note.runs.map { remapRun(it, footnoteMap, endnoteMap, commentMap) },
        )
    }
    val newEndnotes = rendered.endnotes.map { note ->
        note.copy(
            endnoteId = endnoteMap[note.endnoteId] ?: note.endnoteId,
            runs = note.runs.map { remapRun(it, footnoteMap, endnoteMap, commentMap) },
        )
    }
    val newComments = rendered.comments.map { comment ->
        comment.copy(
            commentId = commentMap[comment.commentId] ?: comment.commentId,
            runs = comment.runs.map { remapRun(it, footnoteMap, endnoteMap, commentMap) },
        )
    }

    val newBlocks = blocks.map { block ->
        val blockId = ids.take("block", "${entry.sectionId}-${block.blockId}")
        val listGroup = block.listGroup?.let { ids.take("list_group", it) }
        remapBlock(block, blockId, listGroup, footnoteMap, endnoteMap, commentMap)
    }
    return SectionBlocks(newBlocks, newFootnotes, newEndnotes, newComments)
}

/** Return the run with its note references pointed at the new ids. */
private fun remapRun(
    run: WordRun,
    footnoteMap: Map<String, String>,
    endnoteMap: Map<String, String>,
    commentMap: Map<String, String>,
): WordRun = run.copy(
    footnoteRef = run.footnoteRef?.let { footnoteMap[it] ?: it },
    endnoteRef = run.endnoteRef?.let { endnoteMap[it] ?: it },
    commentRefs = run.commentRefs.map { commentMap[it] ?: it },
)

/** Return the block with its new id and its note references remapped. */
private fun remapBlock(
    block: WordBlock,
    blockId: String,
    listGroup: String?,
    footnoteMap: Map<String, String>,
    endnoteMap: Map<String, String>,
    commentMap: Map<String, String>,
): WordBlock = block.copy(
    blockId = blockId,
    listGroup = listGroup,
    runs = block.runs.map { remapRun(it, footnoteMap, endnoteMap, commentMap) },
)

/**
 * Hands out ids that are unique within the document being rebuilt.
 *
 * The ids of the document that was imported are taken first, so a new id can
 * never collide with one the document already carries. The bridge numbers its
 * notes `f1`, `f2`, ... and its blocks `m0001`, `m0002`, ... for every section
 * it renders, so two sections rendered apart carry the same ids. A collision is
 * resolved deterministically: the first free `<id>-2`, `<id>-3`, ... is taken.
 */
internal class IdAllocator(pools: Map<String, Set<String>>) {
    private val pools: MutableMap<String, MutableSet<String>> =
        pools.mapValues { it.value.toMutableSet() }.toMutableMap()

    /** Return an id of [pool] that no other id of that pool carries. */
    fun take(pool: String, desired: String): String {
        val taken = pools.getOrPut(pool) { mutableSetOf() }
        var candidate = desired
        var index = 2
        while (candidate in taken) {
            candidate = "$desired-$index"
            index++
        }
        taken.add(candidate)
        return candidate
    }
}

// --- The tables of an edited section ------------------------------------------

/** What happens to a table of a section whose text was edited. */
private enum class TableStatus {
    /** The operator did not change the table: it is kept as it was imported. */
    KEPT,

    /** The operator changed the table: it is rebuilt from their own text. */
    REBUILT,

    /** The operator removed the table: it is gone. */
    DROPPED,
}

/**
 * The fate of one table of an edited section.
 *
 * @property status what happens to the table.
 * @property original the table as it was imported, when it is kept.
 * @property markdown the operator's text as a markdown table, when the table is
 *   rebuilt.
 */
private class TablePlan(
    val status: TableStatus,
    val original: WordBlock? = null,
    val markdown: String = "",
)

/**
 * The fate of every table of a section, and the text without them.
 *
 * @property tables the fate of every table, in the order of the blocks.
 * @property text the operator's text with the tables' text taken out of it.
 */
private class TablePlanResult(
    val tables: List<TablePlan>,
    val text: String,
)

/**
 * Return the fate of every table of a section, and the text without them.
 *
 * A table is the one block whose text the editor sees as plain lines: its rows
 * are pipe-separated. The table is therefore kept as it was imported -- with
 * its borders, its widths and its cell structure -- when those lines are still
 * in the text, and rebuilt from the operator's own lines when they are not.
 *
 * The tables are looked for from left to right, so that a section with two
 * tables keeps the first one and rebuilds the second.
 */
private fun planTables(body: List<WordBlock>, editedText: String): TablePlanResult {
    val tables = body.filter { it.kind == WordBlockKind.TABLE }
    if (tables.isEmpty()) {
        return TablePlanResult(emptyList(), editedText)
    }
    val plans = mutableListOf<TablePlan>()
    val edits = mutableListOf<TextEdit>()
    var cursor = 0
    for (table in tables) {
        val originalText = table.renderText()
        val index = editedText.indexOf(originalText, cursor)
        if (index >= 0) {
            // The operator did not touch the table: it is kept as it was, and
            // its text is taken out of the prose so that it is not twice.
            plans.add(TablePlan(TableStatus.KEPT, original = table))
            edits.add(TextEdit(index, index + originalText.length, ""))
            cursor = index + originalText.length
            continue
        }
        val run = findTableRun(editedText, table, cursor, edits)
        if (run == null) {
            // The operator removed the table's text: the table is gone.
            plans.add(TablePlan(TableStatus.DROPPED))
            continue
        }
        plans.add(TablePlan(TableStatus.REBUILT, markdown = markdownTable(run.lines)))
        edits.add(TextEdit(run.start, run.end, ""))
        cursor = run.end
    }
    return TablePlanResult(plans, applyEdits(editedText, edits))
}

/**
 * A run of pipe-separated lines in the operator's text.
 *
 * @property start the offset the run starts at.
 * @property end the offset the run ends at.
 * @property lines the lines of the run.
 */
private class PipeRun(val start: Int, val end: Int, val lines: List<String>)

/**
 * Return the run of pipe-separated lines that holds the operator's version of
 * [table], or `null` when there is none.
 *
 * The run must come after [cursor] and must not overlap an edit that was
 * already made. A run that still holds one of the table's own lines is the
 * operator's table; a run that has the shape of the table is taken when the
 * operator rewrote every cell of it.
 */
private fun findTableRun(
    text: String,
    table: WordBlock,
    cursor: Int,
    edits: List<TextEdit>,
): PipeRun? {
    val originalLines = table.renderText().split("\n")
    val candidates = pipeRuns(text).filter { run ->
        run.start >= cursor && edits.none { run.start < it.end && it.start < run.end }
    }
    for (candidate in candidates) {
        if (candidate.lines.any { line -> originalLines.contains(line) }) {
            return candidate
        }
    }
    val columns = originalLines.firstOrNull()?.let { tableCells(it).size } ?: 0
    for (candidate in candidates) {
        if (candidate.lines.size != originalLines.size) {
            continue
        }
        val first = candidate.lines.firstOrNull() ?: continue
        if (tableCells(first).size == columns) {
            return candidate
        }
    }
    return null
}

/**
 * Return the runs of pipe-separated lines in [text].
 *
 * A line carries a pipe when it holds one that is not escaped: an escaped
 * pipe belongs to the content of a cell and does not make the line a row.
 */
private fun pipeRuns(text: String): List<PipeRun> {
    val lines = textLines(text)
    val runs = mutableListOf<PipeRun>()
    var index = 0
    while (index < lines.size) {
        if (!hasPipe(lines[index].text)) {
            index++
            continue
        }
        val start = index
        while (index < lines.size && hasPipe(lines[index].text)) {
            index++
        }
        runs.add(
            PipeRun(
                start = lines[start].start,
                end = lines[index - 1].end,
                lines = lines.subList(start, index).map { it.text },
            ),
        )
    }
    return runs
}

/**
 * Return the operator's table lines as a markdown table.
 *
 * The manuscript bridge reads a table when a delimiter row follows the row
 * that starts it, so the operator's lines are given one. The cells are escaped
 * and the rows are padded to the width of the widest one, so that the table
 * the bridge renders has the grid the operator wrote.
 */
private fun markdownTable(lines: List<String>): String {
    if (lines.isEmpty()) {
        return ""
    }
    val rows = lines.map { tableCells(it) }
    val width = rows.maxOf { it.size }
    val rest = rows.drop(1)
    val hasSeparator = rest.isNotEmpty() && rest.first().all { DELIMITER_CELL.matches(it) }
    val body = if (hasSeparator) rest.drop(1) else rest
    val out = mutableListOf<String>()
    out.add(markdownRow(rows.first(), width))
    out.add(markdownRow(List(width) { "---" }, width))
    for (row in body) {
        out.add(markdownRow(row, width))
    }
    return out.joinToString("\n")
}

/** Return one row of a markdown table, padded to [width] cells. */
private fun markdownRow(cells: List<String>, width: Int): String {
    val padded = cells + List((width - cells.size).coerceAtLeast(0)) { "" }
    return "| " + padded.joinToString(" | ") { cell -> cell.replace("|", "\\|") } + " |"
}

/**
 * Return the cells of a pipe-separated line.
 *
 * The outer pipes are optional, and an escaped pipe belongs to the content of
 * a cell and does not separate two cells. The whitespace around a cell is
 * trimmed, because the padding of a table is layout and not content.
 */
private fun tableCells(line: String): List<String> {
    val stripped = line.trim()
    val cells = mutableListOf<String>()
    val buffer = StringBuilder()
    val leadingPipe = stripped.startsWith("|")
    var trailingPipe = false
    var index = 0
    while (index < stripped.length) {
        val char = stripped[index]
        if (char == '\\' && index + 1 < stripped.length && stripped[index + 1] == '|') {
            buffer.append('|')
            index += 2
            continue
        }
        if (char == '|') {
            cells.add(buffer.toString())
            buffer.setLength(0)
            trailingPipe = true
            index++
            continue
        }
        buffer.append(char)
        trailingPipe = false
        index++
    }
    cells.add(buffer.toString())
    if (leadingPipe && cells.isNotEmpty() && cells[0].isEmpty()) {
        cells.removeAt(0)
    }
    if (trailingPipe && cells.isNotEmpty() && cells.last().isEmpty()) {
        cells.removeAt(cells.size - 1)
    }
    return cells.map { it.trim() }
}

/** Report whether a line carries a pipe that separates two cells. */
private fun hasPipe(line: String): Boolean {
    var index = 0
    while (index < line.length) {
        val char = line[index]
        if (char == '\\') {
            index += 2
            continue
        }
        if (char == '|') {
            return true
        }
        index++
    }
    return false
}

/**
 * One line of a text, with the offsets it covers.
 *
 * @property text the line itself.
 * @property start the offset the line starts at.
 * @property end the offset the line ends at.
 */
private class TextLine(val text: String, val start: Int, val end: Int)

/** Return the lines of [text], with the offsets they cover. */
private fun textLines(text: String): List<TextLine> {
    val lines = mutableListOf<TextLine>()
    var start = 0
    while (start <= text.length) {
        val newline = text.indexOf('\n', start)
        if (newline < 0) {
            if (start < text.length) {
                lines.add(TextLine(text.substring(start), start, text.length))
            }
            break
        }
        lines.add(TextLine(text.substring(start, newline), start, newline))
        start = newline + 1
    }
    return lines
}

/** One change to the operator's text. */
private class TextEdit(val start: Int, val end: Int, val replacement: String)

/**
 * Return the text with its edits applied.
 *
 * The edits are applied from left to right, and an edit that overlaps one that
 * was already applied is dropped: the first claim on a piece of text wins.
 */
private fun applyEdits(text: String, edits: List<TextEdit>): String {
    if (edits.isEmpty()) {
        return text
    }
    val out = StringBuilder()
    var position = 0
    for (edit in edits.sortedBy { it.start }) {
        if (edit.start < position) {
            continue
        }
        out.append(text, position, edit.start)
        out.append(edit.replacement)
        position = edit.end
    }
    out.append(text, position, text.length)
    return out.toString()
}

/** The shape of a markdown table's delimiter cell. */
private val DELIMITER_CELL = Regex("^:?-{1,}:?$")

// --- Reading the stored origin -----------------------------------------------

/** Return the entries of the list [key] of the origin record. */
private fun originList(origin: Map<String, Any?>, key: String): List<Map<String, Any?>> =
    (origin[key] as? List<*>).orEmpty().mapNotNull { entry ->
        @Suppress("UNCHECKED_CAST")
        entry as? Map<String, Any?>
    }

/** Return the document's core properties. */
private fun propertiesFrom(origin: Map<String, Any?>): WordProperties {
    val properties = origin["properties"] as? Map<*, *> ?: return WordProperties()
    return WordProperties(
        title = properties["title"] as? String ?: "",
        author = properties["author"] as? String ?: "",
        subject = properties["subject"] as? String ?: "",
        keywords = properties["keywords"] as? String ?: "",
        comments = properties["comments"] as? String ?: "",
        revision = (properties["revision"] as? Number)?.toInt() ?: 1,
    )
}

/** Return the document's footnotes. */
private fun originFootnotes(origin: Map<String, Any?>): List<WordFootnote> =
    originList(origin, "footnotes").map { note ->
        WordFootnote(
            footnoteId = note["footnote_id"] as? String ?: "",
            text = note["text"] as? String ?: "",
            runs = (note["runs"] as? List<*>).orEmpty().mapNotNull { entry ->
                (entry as? Map<*, *>)?.let { wordRunFromMap(it) }
            },
        )
    }

/** Return the document's endnotes. */
private fun originEndnotes(origin: Map<String, Any?>): List<WordEndnote> =
    originList(origin, "endnotes").map { note ->
        WordEndnote(
            endnoteId = note["endnote_id"] as? String ?: "",
            text = note["text"] as? String ?: "",
            runs = (note["runs"] as? List<*>).orEmpty().mapNotNull { entry ->
                (entry as? Map<*, *>)?.let { wordRunFromMap(it) }
            },
        )
    }

/** Return the document's comments. */
private fun originComments(origin: Map<String, Any?>): List<WordComment> =
    originList(origin, "comments").map { comment ->
        WordComment(
            commentId = comment["comment_id"] as? String ?: "",
            author = comment["author"] as? String ?: "",
            initials = comment["initials"] as? String ?: "",
            text = comment["text"] as? String ?: "",
            anchorBlockId = comment["anchor_block_id"] as? String,
            anchorText = comment["anchor_text"] as? String ?: "",
            runs = (comment["runs"] as? List<*>).orEmpty().mapNotNull { entry ->
                (entry as? Map<*, *>)?.let { wordRunFromMap(it) }
            },
        )
    }

/** Return the document's section properties. */
private fun sectionsFrom(origin: Map<String, Any?>): List<WordSection> =
    originList(origin, "sections").map { section ->
        WordSection(
            pageWidth = (section["page_width"] as? Number)?.toInt() ?: 12240,
            pageHeight = (section["page_height"] as? Number)?.toInt() ?: 15840,
            orientation = section["orientation"] as? String ?: "portrait",
            marginTop = (section["margin_top"] as? Number)?.toInt() ?: 1440,
            marginRight = (section["margin_right"] as? Number)?.toInt() ?: 1440,
            marginBottom = (section["margin_bottom"] as? Number)?.toInt() ?: 1440,
            marginLeft = (section["margin_left"] as? Number)?.toInt() ?: 1440,
            endBlockId = section["end_block_id"] as? String,
        )
    }

/** Return the document's styles. */
private fun stylesFrom(origin: Map<String, Any?>): List<WordStyle> =
    originList(origin, "styles").map { style ->
        WordStyle(
            styleId = style["style_id"] as? String ?: "",
            name = style["name"] as? String ?: "",
            basedOn = style["based_on"] as? String ?: "",
            styleType = style["style_type"] as? String ?: "paragraph",
            numId = style["num_id"] as? String,
            numLevel = (style["num_level"] as? Number)?.toInt() ?: 0,
        )
    }

/** Return the document's media parts. */
private fun mediaFrom(origin: Map<String, Any?>): List<WordMedia> =
    originList(origin, "media").map { media ->
        WordMedia(
            mediaId = media["media_id"] as? String ?: "",
            partName = media["part_name"] as? String ?: "",
            contentType = media["content_type"] as? String ?: "",
            data = decodeBytes(media["data_base64"] as? String ?: ""),
            relationshipId = media["relationship_id"] as? String ?: "",
        )
    }

/** Return the document's package parts. */
private fun partsFrom(origin: Map<String, Any?>): List<WordPart> =
    originList(origin, "parts").map { part ->
        WordPart(
            partName = part["part_name"] as? String ?: "",
            contentType = part["content_type"] as? String ?: "",
            data = decodeBytes(part["data_base64"] as? String ?: ""),
            relationshipId = part["relationship_id"] as? String ?: "",
            relationshipType = part["relationship_type"] as? String ?: "",
        )
    }

/**
 * Return the bytes a base64 string carries, or empty bytes when it does not.
 *
 * A part whose bytes cannot be decoded is lost, and losing it silently would
 * hand the writer a package that is missing an image without saying so. The
 * failure is reported instead.
 */
private fun decodeBytes(encoded: String): ByteArray {
    if (encoded.isEmpty()) {
        return ByteArray(0)
    }
    return try {
        Base64.getDecoder().decode(encoded)
    } catch (exc: IllegalArgumentException) {
        throw LibraryError(
            "a stored package part carries bytes that are not valid base64: ${exc.message}",
            mapOf("reason" to (exc.message ?: "")),
        )
    }
}
