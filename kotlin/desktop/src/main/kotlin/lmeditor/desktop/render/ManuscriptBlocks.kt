package lmeditor.desktop.render

import lmeditor.core.Citations

/**
 * The kind of a block of manuscript text.
 *
 * The renderer is a *display* concern, so these are the shapes a reader sees,
 * not the shapes the exporter writes. The one rule the renderer borrows
 * rather than reimplements is the citation grammar, which lives in
 * `lmeditor.core`.
 */
enum class BlockKind {
    HEADING,
    PARAGRAPH,
    LIST_ITEM,
    QUOTE,
    CODE,
}

/** The emphasis a run of text carries. */
enum class Emphasis {
    PLAIN,
    BOLD,
    ITALIC,
    CODE,
}

/** One piece of a block. */
sealed interface InlineSpan {

    /** A run of text with one emphasis. */
    data class Text(val text: String, val style: Emphasis) : InlineSpan

    /**
     * A citation marker.
     *
     * It carries every chunk id the marker names, because a marker may name
     * more than one: `[#a, #b]` cites two passages, and a chip that showed
     * only the first would hide half of what the claim rests on.
     */
    data class Citation(val chunkIds: List<String>, val text: String) : InlineSpan
}

/** One block of the manuscript. */
data class ManuscriptBlock(
    val kind: BlockKind,
    val level: Int,
    val spans: List<InlineSpan>,
)

/**
 * Turns a section's text into the blocks a reader sees.
 *
 * The rule this borrows from the tier is the inline-citation grammar: the
 * markers are found with `lmeditor.core.Citations`, the same pattern the
 * drafter wrote them against and the verifier reads them with. A client that
 * recognised a different set of markers would draw a document whose citations
 * do not match the ones the run actually made.
 */
object ManuscriptBlocks {

    private val HEADING_PATTERN = Regex("""^(#{1,6})\s+(.*)$""")
    private val LIST_PATTERN = Regex("""^\s*(?:[-*+]|\d+[.)])\s+(.*)$""")
    private val QUOTE_PATTERN = Regex("""^\s*>\s?(.*)$""")
    private val FENCE_PATTERN = Regex("""^\s*(?:```|~~~)""")

    private val BOLD_PATTERN = Regex("""\*\*(.+?)\*\*|__(.+?)__""")
    private val ITALIC_PATTERN = Regex("""(?<![*_])[*_]([^*_\n]+?)(?![*_])""")
    private val CODE_PATTERN = Regex("""`([^`\n]+)`""")

    /**
     * Parse a section's text into its blocks.
     *
     * A blank line separates the blocks. Lines that are neither a heading, a
     * list item, a quote nor a fenced code block are one paragraph, joined
     * with a space: a paragraph broken across lines is one paragraph, and
     * showing the line breaks would be a formatting artefact of the drafter,
     * not something the reader wrote.
     */
    fun parse(text: String): List<ManuscriptBlock> {
        val blocks = mutableListOf<ManuscriptBlock>()
        val lines = text.lines()
        val paragraph = StringBuilder()
        var index = 0

        fun flushParagraph() {
            if (paragraph.isNotEmpty()) {
                blocks += ManuscriptBlock(BlockKind.PARAGRAPH, 0, inlineSpans(paragraph.toString()))
                paragraph.setLength(0)
            }
        }

        while (index < lines.size) {
            val line = lines[index]
            when {
                FENCE_PATTERN.containsMatchIn(line) -> {
                    flushParagraph()
                    val fence = line.trim()
                    val code = StringBuilder()
                    index++
                    while (index < lines.size && !lines[index].trim().startsWith(fence)) {
                        code.appendLine(lines[index])
                        index++
                    }
                    if (index < lines.size) index++
                    blocks += ManuscriptBlock(
                        kind = BlockKind.CODE,
                        level = 0,
                        spans = listOf(InlineSpan.Text(code.toString().trimEnd('\n'), Emphasis.CODE)),
                    )
                }

                line.isBlank() -> {
                    flushParagraph()
                    index++
                }

                HEADING_PATTERN.matches(line) -> {
                    flushParagraph()
                    val match = HEADING_PATTERN.find(line)!!
                    blocks += ManuscriptBlock(
                        kind = BlockKind.HEADING,
                        level = match.groupValues[1].length,
                        spans = inlineSpans(match.groupValues[2]),
                    )
                    index++
                }

                LIST_PATTERN.matches(line) -> {
                    flushParagraph()
                    val match = LIST_PATTERN.find(line)!!
                    blocks += ManuscriptBlock(BlockKind.LIST_ITEM, 0, inlineSpans(match.groupValues[1]))
                    index++
                }

                QUOTE_PATTERN.matches(line) -> {
                    flushParagraph()
                    val match = QUOTE_PATTERN.find(line)!!
                    blocks += ManuscriptBlock(BlockKind.QUOTE, 0, inlineSpans(match.groupValues[1]))
                    index++
                }

                else -> {
                    if (paragraph.isNotEmpty()) paragraph.append(' ')
                    paragraph.append(line.trim())
                    index++
                }
            }
        }
        flushParagraph()
        return blocks
    }

    /**
     * Split one run of text into its spans.
     *
     * A citation marker and an emphasis never overlap in practice, but if
     * they did the marker wins: a chip that hid inside a bold run would be a
     * citation the reader could not see, and an unseen citation is a
     * citation the reader cannot check.
     */
    fun inlineSpans(text: String): List<InlineSpan> {
        if (text.isEmpty()) return emptyList()

        val marks = mutableListOf<Mark>()
        for (match in Citations.CITATION_PATTERN.findAll(text)) {
            val body = match.groups["citeBody"]?.value ?: continue
            marks += Mark(match.range.first, match.range.last + 1, 0, InlineSpan.Citation(
                chunkIds = Citations.chunkIdsOf(body),
                text = match.value,
            ))
        }
        for (match in BOLD_PATTERN.findAll(text)) {
            val body = match.groupValues[1].ifEmpty { match.groupValues[2] }
            marks += Mark(match.range.first, match.range.last + 1, 1, InlineSpan.Text(body, Emphasis.BOLD))
        }
        for (match in ITALIC_PATTERN.findAll(text)) {
            marks += Mark(
                match.range.first,
                match.range.last + 1,
                2,
                InlineSpan.Text(match.groupValues[1], Emphasis.ITALIC),
            )
        }
        for (match in CODE_PATTERN.findAll(text)) {
            marks += Mark(
                match.range.first,
                match.range.last + 1,
                3,
                InlineSpan.Text(match.groupValues[1], Emphasis.CODE),
            )
        }

        val accepted = mutableListOf<Mark>()
        for (mark in marks.sortedWith(compareBy({ it.start }, { it.priority }))) {
            val clashes = accepted.any { mark.start < it.end && it.start < mark.end }
            if (!clashes) accepted += mark
        }

        val spans = mutableListOf<InlineSpan>()
        var cursor = 0
        for (mark in accepted.sortedBy { it.start }) {
            if (mark.start > cursor) {
                spans += InlineSpan.Text(text.substring(cursor, mark.start), Emphasis.PLAIN)
            }
            spans += mark.span
            cursor = mark.end
        }
        if (cursor < text.length) {
            spans += InlineSpan.Text(text.substring(cursor), Emphasis.PLAIN)
        }
        return spans
    }

    /** One candidate span, with the priority that decides a clash. */
    private class Mark(
        val start: Int,
        val end: Int,
        val priority: Int,
        val span: InlineSpan,
    )
}
