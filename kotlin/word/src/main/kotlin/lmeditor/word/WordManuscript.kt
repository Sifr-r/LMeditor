package lmeditor.word

import lmeditor.core.BibliographicRecord
import lmeditor.core.CitationLineage
import lmeditor.core.Citations
import lmeditor.core.CodePoints
import lmeditor.core.DocumentDraft
import lmeditor.core.SectionDraft
import lmeditor.core.SectionStatus

/**
 * Builds a Word document from a manuscript draft.
 *
 * Implements the contract's M1-M6:
 *
 * * **M1** the document title becomes a `TITLE` block; every section becomes a
 *   `HEADING` block at level 1 followed by one block per paragraph, heading,
 *   list, quote or code block of its content.
 * * **M2** sections are ordered by `(order, sectionId)`; with
 *   `verifiedOnly = true` only sections that passed verification survive.
 * * **M3** inline citation markers are consumed from the body text and replaced
 *   by superscript numeric runs.
 * * **M4** markers are numbered in first-appearance order and carry a footnote
 *   holding the cited passage and the id of the chunk it came from.
 * * **M5** a non-empty bibliography is appended as a `References` heading plus
 *   one paragraph per record.
 * * **M6** the inputs are never mutated.
 *
 * The content of a section is read as the markdown-flavoured syntax the editor
 * writes, so a heading, a list, a quote or a code block in the manuscript
 * reaches the document as the structure it stands for.
 */
object WordManuscript {

    /** Section statuses that count as "passed verification" for `verifiedOnly`. */
    private val VERIFIED_STATUSES = setOf(SectionStatus.VERIFIED, SectionStatus.APPROVED)

    /** Paragraph emitted for a section that carries no drafted prose. */
    private const val EMPTY_SECTION_TEXT = "Section has no drafted content."

    /** Heading of the appended reference list. */
    private const val REFERENCES_HEADING = "References"

    /** Author placeholder for a bibliographic record without authors. */
    private const val ANONYMOUS_AUTHOR = "Anon."

    /** Spaces of indentation that make a list item one level deeper. */
    private const val LIST_INDENT_WIDTH = 2

    /** Deepest nesting a list item may carry; the model allows no more. */
    private const val MAX_LIST_LEVEL = 8

    /** Font a code run is rendered in. */
    private const val CODE_FONT = "Consolas"

    /** Colour a highlighted run is marked with. */
    private const val HIGHLIGHT_COLOR = "yellow"

    /**
     * The character class Python's `.` matches: every character but the line
     * feed.
     *
     * Java's `.` refuses the carriage return, the no-break line feed and the
     * Unicode line separators as well, so the class is spelled out: the two
     * tiers have to read the same line the same way, and a heading that ends
     * with a Unicode line separator is still the same heading on both sides.
     */
    private const val ANY = "[^\\n]"

    /**
     * The character class Python's `\S` matches.
     *
     * Python's `\s` reaches the C0 file, group, record and unit separators,
     * which the Unicode `White_Space` property leaves out. The `(?U)` flag
     * alone would therefore still disagree on those four, so the class is
     * spelled out rather than left to the flag.
     */
    private const val NON_SPACE = "[^\\p{IsWhite_Space}\\x1C-\\x1F]"

    private val HEADING_PATTERN = Regex("^[ \\t]{0,3}(#{1,6})[ \\t]+($ANY*)$")
    private val BULLET_PATTERN = Regex("^([ \\t]*)([-*+])[ \\t]+($ANY*$NON_SPACE$ANY*)$")

    /**
     * A numbered list item.
     *
     * The number is a Unicode decimal digit, the way Python's `\d` is:
     * a list written in a script that does not use ASCII digits is still a
     * list.
     */
    private val NUMBERED_PATTERN = Regex("^([ \\t]*)(\\p{Nd}+)\\.[ \\t]+($ANY*$NON_SPACE$ANY*)$")
    private val QUOTE_PATTERN = Regex("^[ \\t]{0,3}>[ \\t]?($ANY*)$")
    private val FENCE_PATTERN = Regex("^[ \\t]{0,3}```")
    private val DELIMITER_CELL_PATTERN = Regex("^:?-{1,}:?$")
    private val TEMPLATE_CITATION_PATTERN = Regex("\\{\\{cite:[^{}]*}}")

    /**
     * Build a Word document from a manuscript draft.
     *
     * @param document the manuscript draft to render
     * @param bibliography bibliographic records for the reference list, in the
     *   order they should appear
     * @param verifiedOnly when true, drop every section that did not pass
     *   verification -- heading included
     * @return a document ready for the writer
     */
    fun buildManuscriptDocument(
        document: DocumentDraft,
        bibliography: List<BibliographicRecord> = emptyList(),
        verifiedOnly: Boolean = false,
    ): WordDocument {
        val ledger = CitationLedger()
        val groups = ListGroups()
        val blocks = mutableListOf<WordBlock>()

        fun add(
            kind: WordBlockKind,
            runs: List<WordRun>,
            level: Int = 0,
            listMarker: ListMarker? = null,
            listGroup: String? = null,
        ) {
            blocks.add(
                makeBlock(
                    nextBlockId(blocks.size),
                    kind,
                    runs,
                    level = level,
                    listMarker = listMarker,
                    listGroup = listGroup,
                ),
            )
        }

        add(WordBlockKind.TITLE, listOf(WordRun(text = WordText.stripControlCharacters(document.title))))

        for (section in selectedSections(document.sections, verifiedOnly)) {
            add(
                WordBlockKind.HEADING,
                listOf(WordRun(text = WordText.stripControlCharacters(section.title))),
                level = 1,
            )
            val quotes = sectionQuotes(section)
            val specs = splitBlocks(section.content, groups)
            if (specs.isEmpty()) {
                add(WordBlockKind.PARAGRAPH, listOf(WordRun(text = EMPTY_SECTION_TEXT)))
                continue
            }
            for (spec in specs) {
                if (spec is TableSpec) {
                    blocks.add(
                        WordBlock(
                            blockId = nextBlockId(blocks.size),
                            kind = WordBlockKind.TABLE,
                            table = buildTable(spec, quotes, ledger),
                        ),
                    )
                    continue
                }
                val runs = if (spec.kind == WordBlockKind.CODE) {
                    codeRuns(spec.text)
                } else {
                    paragraphRuns(spec.text, quotes, ledger)
                }
                if (runs.isEmpty()) continue
                add(
                    spec.kind,
                    runs,
                    level = spec.level,
                    listMarker = spec.listMarker,
                    listGroup = spec.listGroup,
                )
            }
        }

        if (bibliography.isNotEmpty()) {
            add(WordBlockKind.HEADING, listOf(WordRun(text = REFERENCES_HEADING)), level = 1)
            for (record in bibliography) {
                val reference = formatReference(record)
                add(WordBlockKind.PARAGRAPH, listOf(WordRun(text = WordText.stripControlCharacters(reference))))
            }
        }

        return WordDocument(
            blocks = blocks,
            properties = WordProperties(title = document.title),
            footnotes = ledger.footnotes(),
        )
    }

    // ------------------------------------------------------------------
    // Sections
    // ------------------------------------------------------------------

    /**
     * Return the sections to render, filtered and ordered.
     *
     * Ordering is `(order, sectionId)`; the input list is copied, so the
     * draft's own list is never reordered in place.
     */
    private fun selectedSections(sections: List<SectionDraft>, verifiedOnly: Boolean): List<SectionDraft> =
        sections
            .filter { !verifiedOnly || hasPassedVerification(it) }
            .sortedWith(compareBy({ it.order }, { it.sectionId }))

    private fun hasPassedVerification(section: SectionDraft): Boolean =
        section.isVerified || section.status in VERIFIED_STATUSES

    /**
     * Map chunk id to cited passage for one section's lineages.
     *
     * The first non-empty `exactQuote` wins when a chunk is cited by more than
     * one lineage of the same section.
     */
    private fun sectionQuotes(section: SectionDraft): Map<String, String> {
        val quotes = linkedMapOf<String, String>()
        for (lineage in section.citations) {
            val quote = lineage.exactQuote ?: continue
            val cleaned = quote.trim()
            if (cleaned.isEmpty()) continue
            quotes.putIfAbsent(lineage.chunkId, cleaned)
        }
        return quotes
    }

    // ------------------------------------------------------------------
    // Block splitting
    // ------------------------------------------------------------------

    /**
     * Split section content into the blocks the manuscript carries.
     *
     * The content is read as the markdown-flavoured syntax the editor writes: a
     * heading, a list item, a quote and a fenced code block are blocks of their
     * own, and a line that carries none of them continues the prose above it.
     * A pipe table is a block of its own as well, but only when a delimiter row
     * follows the line that starts it. Blank lines still separate paragraphs.
     */
    private fun splitBlocks(content: String, groups: ListGroups): List<BlockSpec> {
        val specs = mutableListOf<BlockSpec>()
        val paragraph = mutableListOf<String>()
        val lines = content.split("\n")
        var index = 0
        while (index < lines.size) {
            val line = lines[index]
            if (isFenceLine(line)) {
                flushParagraph(paragraph, specs)
                val collected = collectCodeBlock(lines, index)
                index = collected.first
                specs.add(BlockSpec(kind = WordBlockKind.CODE, text = collected.second))
                continue
            }
            val heading = matchHeading(line)
            if (heading != null) {
                flushParagraph(paragraph, specs)
                specs.add(heading)
                index++
                continue
            }
            val item = matchListItem(line)
            if (item != null) {
                flushParagraph(paragraph, specs)
                specs.add(listBlock(item, specs, groups))
                index++
                continue
            }
            val quote = matchQuote(line)
            if (quote != null) {
                flushParagraph(paragraph, specs)
                specs.add(quote)
                index++
                continue
            }
            val table = matchTable(lines, index)
            if (table != null) {
                flushParagraph(paragraph, specs)
                specs.add(table.first)
                index = table.second
                continue
            }
            if (line.isBlank()) {
                flushParagraph(paragraph, specs)
                index++
                continue
            }
            paragraph.add(line)
            index++
        }
        flushParagraph(paragraph, specs)
        return specs
    }

    /**
     * Emit the prose collected so far as one paragraph block.
     *
     * The lines are joined with a newline, so that a soft line break inside a
     * paragraph survives the way it does today.
     */
    private fun flushParagraph(lines: MutableList<String>, specs: MutableList<BlockSpec>) {
        if (lines.isEmpty()) return
        specs.add(BlockSpec(kind = WordBlockKind.PARAGRAPH, text = lines.joinToString("\n")))
        lines.clear()
    }

    /**
     * Return the heading a line carries, or `null` when it carries none.
     *
     * The level of the heading is the number of its `#` signs; a heading inside
     * a section therefore keeps the level the author wrote.
     */
    private fun matchHeading(line: String): BlockSpec? {
        val match = HEADING_PATTERN.find(line) ?: return null
        val marks = match.groupValues[1]
        return BlockSpec(kind = WordBlockKind.HEADING, text = match.groupValues[2], level = marks.length)
    }

    /**
     * Return the list item a line carries, or `null` when it carries none.
     *
     * The nesting level of the item is taken from its indentation: two spaces
     * of indentation make one level deeper, and the depth is capped at the
     * deepest level the model allows.
     */
    private fun matchListItem(line: String): ListItem? {
        val bullet = BULLET_PATTERN.find(line)
        if (bullet != null) {
            return ListItem(
                marker = ListMarker.BULLET,
                text = bullet.groupValues[3],
                level = listLevel(bullet.groupValues[1]),
            )
        }
        val numbered = NUMBERED_PATTERN.find(line)
        if (numbered != null) {
            return ListItem(
                marker = ListMarker.NUMBER,
                text = numbered.groupValues[3],
                level = listLevel(numbered.groupValues[1]),
                number = numbered.groupValues[2].toIntOrNull(),
            )
        }
        return null
    }

    /** Return the block quote a line carries, or `null` when it carries none. */
    private fun matchQuote(line: String): BlockSpec? {
        val match = QUOTE_PATTERN.find(line) ?: return null
        return BlockSpec(kind = WordBlockKind.QUOTE, text = match.groupValues[1])
    }

    /**
     * Return the table a line starts and the index after it, or `null`.
     *
     * A table is recognised only when a delimiter row follows the line that
     * starts it: a line that merely carries a pipe is prose, and so is a header
     * row without a delimiter row. The recognition is deliberately conservative,
     * because prose turned into a table loses the author's structure, while a
     * table that stays prose merely loses its grid.
     */
    private fun matchTable(lines: List<String>, index: Int): Pair<TableSpec, Int>? {
        if (!hasTableSeparator(lines[index])) return null
        if (index + 1 >= lines.size || !isDelimiterRow(lines[index + 1])) return null
        val rows = mutableListOf<List<String>>()
        rows.add(splitTableCells(lines[index]))
        var cursor = index + 2
        while (cursor < lines.size && hasTableSeparator(lines[cursor])) {
            rows.add(splitTableCells(lines[cursor]))
            cursor++
        }
        return TableSpec(rows = rectangularRows(rows)) to cursor
    }

    /**
     * Report whether a line is the delimiter row of a markdown table.
     *
     * A delimiter row is made of cells whose only content is hyphens,
     * optionally framed by colons.
     */
    private fun isDelimiterRow(line: String): Boolean {
        val cells = splitTableCells(line)
        if (cells.isEmpty()) return false
        return cells.all { DELIMITER_CELL_PATTERN.matches(it) }
    }

    /**
     * Split one table row into its cells.
     *
     * The outer pipes are optional, so `a | b` and `| a | b |` are the same
     * row. An escaped pipe (`\|`) is a literal pipe inside a cell and never
     * separates two cells. The whitespace around a cell's content is trimmed,
     * because the padding of a table is layout and not content.
     */
    private fun splitTableCells(line: String): List<String> {
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
        if (cells.isEmpty()) {
            // A row that carries nothing but pipes still is a row: dropping it
            // would take a line of the manuscript with it.
            return listOf("")
        }
        return cells.map { it.trim() }
    }

    /**
     * Report whether a line carries a pipe that separates two cells.
     *
     * A pipe that is escaped belongs to the content of a cell and does not make
     * the line a table row.
     */
    private fun hasTableSeparator(line: String): Boolean {
        val stripped = line.trim()
        var index = 0
        while (index < stripped.length) {
            if (stripped[index] == '\\') {
                index += 2
                continue
            }
            if (stripped[index] == '|') return true
            index++
        }
        return false
    }

    /**
     * Return the rows of a table, padded so that every row is the same width.
     *
     * A Word table's grid is rectangular, and its width is the number of cells
     * of its widest row.
     */
    private fun rectangularRows(rows: List<List<String>>): List<List<String>> {
        val width = rows.maxOfOrNull { it.size } ?: 0
        return rows.map { row -> row + List(width - row.size) { "" } }
    }

    /**
     * Build the Word table of a markdown table block.
     *
     * The cells of the first row are the header row: they are marked as such
     * and the table repeats them on every page, because a table that runs over
     * a page break without its header cannot be read.
     *
     * The cells are parsed with the same inline machinery as a paragraph, so
     * that emphasis and citation markers inside a cell behave exactly as they
     * do in the prose around the table.
     */
    private fun buildTable(
        spec: TableSpec,
        quotes: Map<String, String>,
        ledger: CitationLedger,
    ): WordTable {
        val rows = mutableListOf<WordTableRow>()
        for ((rowIndex, cells) in spec.rows.withIndex()) {
            val rowCells = cells.map { cellText ->
                val runs = paragraphRuns(cellText, quotes, ledger)
                WordTableCell(
                    text = WordText.normalizeWhitespace(runs.joinToString("") { it.text }),
                    runs = runs,
                    isHeader = rowIndex == 0,
                )
            }
            rows.add(WordTableRow(cells = rowCells))
        }
        return WordTable(rows = rows, headerRows = if (rows.isEmpty()) 0 else 1)
    }

    /** Return the nesting level an indented list item asks for. */
    private fun listLevel(indent: String): Int {
        val width = expandTabs(indent).length
        return minOf(width / LIST_INDENT_WIDTH, MAX_LIST_LEVEL)
    }

    /**
     * Return the block of a list item, with the group its number continues.
     *
     * Two numbered items continue one list when nothing else came between them
     * and the numbering did not restart at one; anything else starts a new
     * group, so that every list numbers from one.
     */
    private fun listBlock(item: ListItem, specs: List<BlockSpec>, groups: ListGroups): BlockSpec {
        var group: String? = null
        if (item.marker == ListMarker.NUMBER) {
            group = numberGroup(item.number, specs, groups)
        }
        return BlockSpec(
            kind = WordBlockKind.LIST_ITEM,
            text = item.text,
            level = item.level,
            listMarker = item.marker,
            listGroup = group,
        )
    }

    /** Return the continuity group a numbered list item belongs to. */
    private fun numberGroup(number: Int?, specs: List<BlockSpec>, groups: ListGroups): String {
        val previous = specs.lastOrNull()
        val continues = previous != null &&
            previous.kind == WordBlockKind.LIST_ITEM &&
            previous.listMarker == ListMarker.NUMBER &&
            number != 1
        if (continues && previous.listGroup != null) return previous.listGroup
        return groups.allocate()
    }

    /**
     * Return the index after a fenced code block and the block's content.
     *
     * The fence markers are removed and the content is kept verbatim: a code
     * block is code, not prose, and its `**` is not emphasis.
     */
    private fun collectCodeBlock(lines: List<String>, index: Int): Pair<Int, String> {
        var cursor = index + 1
        val content = mutableListOf<String>()
        while (cursor < lines.size && !isFenceLine(lines[cursor])) {
            content.add(lines[cursor])
            cursor++
        }
        if (cursor < lines.size) cursor++
        return cursor to content.joinToString("\n")
    }

    private fun isFenceLine(line: String): Boolean = FENCE_PATTERN.find(line) != null

    /** Return the runs of a code block, whose content is kept verbatim. */
    private fun codeRuns(text: String): List<WordRun> {
        val cleaned = WordText.stripControlCharacters(text)
        if (cleaned.isEmpty()) return emptyList()
        return listOf(WordRun(text = cleaned, font = CODE_FONT))
    }

    // ------------------------------------------------------------------
    // Inline content
    // ------------------------------------------------------------------

    /**
     * Build the runs of one paragraph, consuming its citation markers.
     *
     * The paragraph is first parsed into pieces that carry their formatting;
     * the citation markers are pieces of their own, so that an emphasised span
     * may hold a marker without the marker splitting the span.
     */
    private fun paragraphRuns(
        text: String,
        quotes: Map<String, String>,
        ledger: CitationLedger,
    ): List<WordRun> {
        val source = CodePointText.of(text)
        val pieces = parseInline(source)
        val runs = mutableListOf<WordRun>()
        var pending = ""
        for (piece in pieces) {
            if (piece is TextPiece) {
                val content = pending + piece.text
                pending = ""
                val cleaned = WordText.stripControlCharacters(content)
                if (cleaned.isNotEmpty()) {
                    runs.add(runFor(piece.formatting, cleaned))
                }
                continue
            }
            val citation = piece as CitationPiece
            if (citation.start > 0 && isWordChar(source.at(citation.start - 1))) {
                appendSpace(runs)
            }
            for ((position, chunkId) in citation.chunkIds.withIndex()) {
                if (position > 0) {
                    // Keep two adjacent markers apart so that their numbers are
                    // not read as one larger number.
                    runs.add(WordRun(text = " "))
                }
                val number = ledger.numberFor(chunkId)
                val footnoteRef = ledger.footnoteRefFor(chunkId, quotes[chunkId])
                runs.add(
                    runFor(piece.formatting, number.toString(), superscript = true, footnoteRef = footnoteRef),
                )
            }
            if (citation.end < source.length && isWordChar(source.at(citation.end))) {
                pending = " "
            }
        }
        return runs
    }

    /** Return a run that carries [formatting]. */
    private fun runFor(
        formatting: Formatting,
        text: String,
        superscript: Boolean = false,
        footnoteRef: String? = null,
    ): WordRun = WordRun(
        text = text,
        bold = formatting.bold,
        italic = formatting.italic,
        underline = formatting.underline,
        strike = formatting.strike,
        superscript = superscript,
        font = if (formatting.code) CODE_FONT else null,
        highlight = if (formatting.highlight) HIGHLIGHT_COLOR else null,
        footnoteRef = footnoteRef,
    )

    /** Append a separating space to the last run, or add a run carrying it. */
    private fun appendSpace(runs: MutableList<WordRun>) {
        if (runs.isEmpty() || runs.last().superscript) {
            runs.add(WordRun(text = " "))
            return
        }
        val last = runs.removeAt(runs.size - 1)
        runs.add(last.copy(text = last.text + " "))
    }

    /**
     * Parse a paragraph's text into the pieces that carry its formatting.
     *
     * The citation markers are opaque: they are located first and handed to
     * the citation machinery untouched, so that a marker is never split and
     * never has emphasis applied to it.
     */
    private fun parseInline(source: CodePointText): List<Piece> {
        val pieces = mutableListOf<Piece>()
        parseInlineRange(source, 0, source.length, Formatting(), opaqueSpans(source), pieces)
        return pieces
    }

    /**
     * Return the spans the inline formatter must not split, by their start.
     *
     * Two kinds of span are opaque: a citation marker, which the citation
     * machinery owns, and the editor's `{{cite:...}}` marker, which is not a
     * citation of the shared grammar and therefore survives as it was written.
     */
    private fun opaqueSpans(source: CodePointText): Map<Int, OpaqueSpan> {
        val spans = mutableMapOf<Int, OpaqueSpan>()
        for ((start, end, chunkIds) in citationGroups(source)) {
            spans[start] = OpaqueSpan(start, end, chunkIds)
        }
        for (match in TEMPLATE_CITATION_PATTERN.findAll(source.substring(0, source.length))) {
            val start = source.substring(0, source.length).codePointCount(0, match.range.first)
            val end = source.substring(0, source.length).codePointCount(0, match.range.last + 1)
            spans.putIfAbsent(start, OpaqueSpan(start, end, emptyList()))
        }
        return spans
    }

    /** Parse `text[start:end]` into `pieces` under [formatting]. */
    private fun parseInlineRange(
        source: CodePointText,
        start: Int,
        end: Int,
        formatting: Formatting,
        opaque: Map<Int, OpaqueSpan>,
        pieces: MutableList<Piece>,
    ) {
        val literal = StringBuilder()
        var index = start
        while (index < end) {
            val span = opaque[index]
            if (span != null) {
                flushLiteral(literal, formatting, pieces)
                if (span.chunkIds.isNotEmpty()) {
                    pieces.add(CitationPiece(span.chunkIds, span.start, span.end, formatting))
                } else {
                    pieces.add(TextPiece(source.substring(span.start, span.end), formatting))
                }
                index = span.end
                continue
            }
            val marker = markerAt(source, index, end)
            if (marker != null) {
                val contentStart = index + marker.text.length
                val closing = findClosing(source, marker, contentStart, end, opaque)
                if (closing != null) {
                    flushLiteral(literal, formatting, pieces)
                    parseInlineRange(
                        source,
                        contentStart,
                        closing,
                        mergeFormatting(formatting, marker.style),
                        opaque,
                        pieces,
                    )
                    index = closing + marker.text.length
                    continue
                }
            }
            literal.appendCodePoint(source.at(index))
            index++
        }
        flushLiteral(literal, formatting, pieces)
    }

    /** Emit the text collected so far as one piece. */
    private fun flushLiteral(literal: StringBuilder, formatting: Formatting, pieces: MutableList<Piece>) {
        if (literal.isEmpty()) return
        pieces.add(TextPiece(literal.toString(), formatting))
        literal.setLength(0)
    }

    /** Return the combination of two formatting sets. */
    private fun mergeFormatting(base: Formatting, extra: Formatting): Formatting = Formatting(
        bold = base.bold || extra.bold,
        italic = base.italic || extra.italic,
        underline = base.underline || extra.underline,
        strike = base.strike || extra.strike,
        code = base.code || extra.code,
        highlight = base.highlight || extra.highlight,
    )

    /**
     * Return the inline marker that opens at [index], if there is one.
     *
     * A marker opens only on a run of its own character of exactly its own
     * length, so that `***` stays text instead of becoming a bold marker with a
     * stray star, and only where the text around it can carry it: the
     * character after the marker must not be whitespace, and an underscore may
     * not open in the middle of a word, where it belongs to an identifier
     * rather than to the formatting.
     */
    private fun markerAt(source: CodePointText, index: Int, end: Int): InlineMarker? {
        val character = source.at(index)
        val run = characterRun(source, index, end)
        for (marker in inlineMarkers) {
            if (marker.text[0].code != character || marker.text.length != run) continue
            if (!opensCleanly(source, index, end, marker)) continue
            return marker
        }
        return null
    }

    /** Report whether a marker may open at [index]. */
    private fun opensCleanly(source: CodePointText, index: Int, end: Int, marker: InlineMarker): Boolean {
        val after = index + marker.text.length
        if (after >= end || isSpace(source.at(after))) return false
        if (marker.text[0] == '_' && index > 0 && isAlnum(source.at(index - 1))) return false
        return true
    }

    /** Report whether a marker may close at [index]. */
    private fun closesCleanly(source: CodePointText, index: Int, end: Int, marker: InlineMarker): Boolean {
        if (index == 0 || isSpace(source.at(index - 1))) return false
        val after = index + marker.text.length
        if (marker.text[0] == '_' && after < end && isAlnum(source.at(after))) return false
        return true
    }

    /**
     * Return the index at which [marker] closes, or `null`.
     *
     * The search steps over the opaque spans, so that a marker inside a
     * citation marker can never close an emphasised span, and it only accepts a
     * closing marker whose run is as long as the opening one.
     */
    private fun findClosing(
        source: CodePointText,
        marker: InlineMarker,
        contentStart: Int,
        end: Int,
        opaque: Map<Int, OpaqueSpan>,
    ): Int? {
        var index = contentStart
        while (index < end) {
            val span = opaque[index]
            if (span != null) {
                index = span.end
                continue
            }
            if (source.at(index) == marker.text[0].code) {
                val run = characterRun(source, index, end)
                if (run == marker.text.length && index > contentStart && closesCleanly(source, index, end, marker)) {
                    return index
                }
                index += run
                continue
            }
            index++
        }
        return null
    }

    /** Return the length of the run of equal characters starting at [index]. */
    private fun characterRun(source: CodePointText, index: Int, end: Int): Int {
        val character = source.at(index)
        var length = 0
        while (index + length < end && source.at(index + length) == character) {
            length++
        }
        return length
    }

    /**
     * Group the citation markers of [text] by their bracketed span.
     *
     * [Citations.iterCitationSpans] reports one entry per cited chunk id, with
     * the span of the whole bracketed marker; a comma list such as `[#a, #b]`
     * therefore yields several entries sharing one span and becomes one group.
     */
    private fun citationGroups(source: CodePointText): List<Triple<Int, Int, List<String>>> {
        val groups = mutableListOf<Triple<Int, Int, MutableList<String>>>()
        for (span in Citations.iterCitationSpans(source.substring(0, source.length))) {
            val last = groups.lastOrNull()
            if (last != null && last.first == span.start && last.second == span.end) {
                last.third.add(span.chunkId)
                continue
            }
            groups.add(Triple(span.start, span.end, mutableListOf(span.chunkId)))
        }
        return groups.map { Triple(it.first, it.second, it.third.toList()) }
    }

    // ------------------------------------------------------------------
    // References
    // ------------------------------------------------------------------

    /**
     * Render one bibliographic record as a single plain-text line.
     *
     * Fields are rendered in the contract's order -- authors, title, container
     * title, volume, issue, year, pages, DOI, URL -- and joined by single
     * spaces. Absent fields are omitted rather than rendered as a placeholder.
     */
    private fun formatReference(record: BibliographicRecord): String {
        val authors = record.authors.map { it.trim() }.filter { it.isNotEmpty() }
        val parts = mutableListOf(if (authors.isEmpty()) ANONYMOUS_AUTHOR else authors.joinToString(", "))
        parts.add(record.title.trim())
        for (optional in listOf(record.containerTitle, record.volume, record.issue)) {
            if (optional != null && optional.isNotBlank()) parts.add(optional.trim())
        }
        record.year?.let { parts.add(it.toString()) }
        for (optional in listOf(record.pages, record.doi, record.url)) {
            if (optional != null && optional.isNotBlank()) parts.add(optional.trim())
        }
        return parts.filter { it.isNotEmpty() }.joinToString(" ")
    }

    // ------------------------------------------------------------------
    // Blocks
    // ------------------------------------------------------------------

    /**
     * Build a block whose text is derived from its runs.
     *
     * The shared model enforces that a block's text equals the normalised
     * concatenation of its runs; deriving the text here keeps the two from
     * drifting apart.
     */
    private fun makeBlock(
        blockId: String,
        kind: WordBlockKind,
        runs: List<WordRun>,
        level: Int = 0,
        listMarker: ListMarker? = null,
        listGroup: String? = null,
    ): WordBlock = WordBlock(
        blockId = blockId,
        kind = kind,
        text = WordText.normalizeWhitespace(runs.joinToString("") { it.text }),
        level = level,
        listMarker = listMarker,
        listGroup = listGroup,
        runs = runs,
    )

    /** Return the deterministic block id for the block at [index]. */
    private fun nextBlockId(index: Int): String = "m%04d".format(index + 1)

    // ------------------------------------------------------------------
    // Internal types
    // ------------------------------------------------------------------

    /** The character formatting that applies to a piece of inline text. */
    private data class Formatting(
        val bold: Boolean = false,
        val italic: Boolean = false,
        val underline: Boolean = false,
        val strike: Boolean = false,
        val code: Boolean = false,
        val highlight: Boolean = false,
    )

    /** One inline marker of the manuscript syntax and the style it carries. */
    private data class InlineMarker(val text: String, val style: Formatting)

    /**
     * A span of the paragraph that the inline formatter must not split.
     *
     * `chunkIds` is empty for a span that is not a citation -- the editor's
     * `{{cite:...}}` marker, which survives the export as it was written.
     */
    private data class OpaqueSpan(val start: Int, val end: Int, val chunkIds: List<String>)

    /** A piece of literal text with the formatting that applies to it. */
    private data class TextPiece(val text: String, val formatting: Formatting) : Piece

    /** A citation marker: one or more chunk ids that share one bracketed span. */
    private data class CitationPiece(
        val chunkIds: List<String>,
        val start: Int,
        val end: Int,
        val formatting: Formatting,
    ) : Piece

    /** A piece of a parsed paragraph: either literal text or a citation marker. */
    private sealed interface Piece

    /** One list item of the manuscript syntax. */
    private data class ListItem(
        val marker: ListMarker,
        val text: String,
        val level: Int,
        val number: Int? = null,
    )

    /** One block of the manuscript, before it becomes a model block. */
    private open class BlockSpec(
        val kind: WordBlockKind,
        val text: String,
        val level: Int = 0,
        val listMarker: ListMarker? = null,
        val listGroup: String? = null,
    )

    /**
     * A markdown table, before its cells become Word runs.
     *
     * The cells keep their raw text here and are turned into runs later, so
     * that a citation marker inside a cell is consumed by the same ledger as a
     * marker in a paragraph: the bridge must not have two grammars for one
     * marker.
     */
    private class TableSpec(val rows: List<List<String>>) :
        BlockSpec(WordBlockKind.TABLE, "")

    /**
     * Assigns citation numbers and footnotes in first-appearance order.
     *
     * The ledger is the document-wide state of the bridge: it numbers chunk ids
     * as they first appear, creates one footnote per chunk that carries a cited
     * passage, and hands out the resulting footnote ids to the runs.
     */
    private class CitationLedger {
        private val numbers = mutableMapOf<String, Int>()
        private val footnoteIds = mutableMapOf<String, String>()
        private val footnoteTexts = mutableListOf<String>()

        /** Return the citation number of [chunkId], assigning it on first use. */
        fun numberFor(chunkId: String): Int {
            numbers[chunkId]?.let { return it }
            val number = numbers.size + 1
            numbers[chunkId] = number
            return number
        }

        /**
         * Return the footnote id for [chunkId], creating it when a quote exists.
         *
         * @return the footnote id to attach to the marker run, or `null` when
         *   the chunk carries no passage
         */
        fun footnoteRefFor(chunkId: String, quote: String?): String? {
            footnoteIds[chunkId]?.let { return it }
            if (quote.isNullOrBlank()) return null
            val footnoteId = "f${footnoteTexts.size + 1}"
            footnoteIds[chunkId] = footnoteId
            footnoteTexts.add(footnoteText(chunkId, quote))
            return footnoteId
        }

        /** Return the footnotes created so far, in creation order. */
        fun footnotes(): List<WordFootnote> = footnoteTexts.mapIndexed { index, text ->
            WordFootnote(
                footnoteId = "f${index + 1}",
                text = text,
                runs = listOf(WordRun(text = text)),
            )
        }
    }

    /**
     * Allocates the continuity groups of the numbered lists of a document.
     *
     * A group is what tells the writer that two numbered items continue one
     * sequence. The ids are allocated per document, because the writer groups
     * every numbered block of the document by its group id: two lists of
     * different sections must not share one number sequence.
     */
    private class ListGroups {
        private var allocated = 0

        /** Return a group id that no other list of the document carries. */
        fun allocate(): String {
            allocated += 1
            return "ol$allocated"
        }
    }

    /**
     * The inline markers of the manuscript syntax.
     *
     * Two-character markers come first so that `**` is matched before `*`, and
     * the underscore pair before the single underscore.
     */
    private val inlineMarkers = listOf(
        InlineMarker("**", Formatting(bold = true)),
        InlineMarker("__", Formatting(underline = true)),
        InlineMarker("~~", Formatting(strike = true)),
        InlineMarker("==", Formatting(highlight = true)),
        InlineMarker("*", Formatting(italic = true)),
        InlineMarker("_", Formatting(italic = true)),
        InlineMarker("`", Formatting(code = true)),
    )
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/**
 * Return the text of the footnote that carries a cited passage.
 *
 * The passage comes first and the id of the chunk it was taken from follows it
 * in brackets. The id is written verbatim, so that a search of the exported
 * package finds the passage it belongs to, and the format is fixed, so that the
 * same chunk always produces the same footnote.
 */
private fun footnoteText(chunkId: String, quote: String): String =
    "${WordText.normalizeWhitespace(quote)} [$chunkId]"



/**
 * Report whether a character belongs to a word.
 *
 * Word characters are the ones that must not be glued to a marker that
 * consumed the text between them.
 */
private fun isWordChar(codePoint: Int): Boolean = isAlnum(codePoint)

/** Report whether a code point is alphanumeric, the way Python's `isalnum` is. */
private fun isAlnum(codePoint: Int): Boolean {
    if (Character.isLetterOrDigit(codePoint)) return true
    return when (Character.getType(codePoint).toInt()) {
        Character.LETTER_NUMBER.toInt(),
        Character.OTHER_NUMBER.toInt(),
        -> true

        else -> false
    }
}

/**
 * Report whether a code point is whitespace, the way Python's `isspace` is.
 *
 * The answer is [CodePoints.isPythonWhitespace], the one predicate the house
 * uses for Python's whitespace: a marker may not open or close against a space
 * that Python would have called a space, or the two tiers would disagree about
 * where a marker ends.
 */
private fun isSpace(codePoint: Int): Boolean = CodePoints.isPythonWhitespace(codePoint)

/** Width of a tab when the indentation of a list item is measured. */
private const val TAB_WIDTH = 4

/** Expand the tabs of [indent] the way Python's `str.expandtabs` does. */
private fun expandTabs(indent: String): String {
    val builder = StringBuilder(indent.length)
    for (ch in indent) {
        if (ch == '\t') {
            val width = TAB_WIDTH - (builder.length % TAB_WIDTH)
            repeat(width) { builder.append(' ') }
        } else {
            builder.append(ch)
        }
    }
    return builder.toString()
}
