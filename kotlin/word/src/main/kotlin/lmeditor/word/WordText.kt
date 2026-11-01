package lmeditor.word

import lmeditor.core.CodePoints

/**
 * Whitespace and text normalisation rules shared by the Word reader and writer.
 *
 * The rules implemented here are the contract's section 4 (T1-T9). They exist as a
 * single object because both sides of the round trip must agree on them: a reader
 * that normalises differently from the writer's expectation produces documents
 * that change on every round trip, which makes the format untestable.
 *
 * The Python tier carries the same rules in `lmeditor.word.text`. The two are
 * held together by a shared corpus -- one file, read by both sides -- rather than
 * by one side calling the other: the rules are consulted on every block of every
 * document, so a seam round trip per block would cost more than the rules are
 * worth.
 */
object WordText {

    /**
     * Characters Word uses for non-breaking or invisible spacing, mapped to a plain
     * space. A no-break space inside a citation marker would otherwise split the
     * marker across two words and defeat the citation grammar.
     */
    val REPLACED_SPACES: Map<Char, String> = mapOf(
        0x00A0.toChar() to " ", // no-break space
        0x2007.toChar() to " ", // figure space
        0x202F.toChar() to " ", // narrow no-break space
        0x2009.toChar() to " ", // thin space
        0x200B.toChar() to " ", // zero-width space
        0x00AD.toChar() to " ", // soft hyphen
        0x3000.toChar() to " ", // ideographic space
    )

    /**
     * Report whether [ch] is whitespace in the sense the Python tier uses.
     *
     * The answer is [CodePoints.isPythonWhitespace], and there is only one answer
     * in the house: Python's `re` module and `str.strip()` agree on a slightly
     * wider set than Java's `\s` -- the C0 file, group, record and unit
     * separators count as whitespace there, and so does the next-line control
     * (U+0085), which Java calls a control character. A second spelling of the
     * rule is how the two tiers drift apart, so this one is only a name for the
     * canonical predicate.
     */
    fun isWhitespace(ch: Char): Boolean = CodePoints.isPythonWhitespace(ch)

    /**
     * Collapse a text fragment to single-spaced, end-stripped text.
     *
     * Applies the contract's T3 and T4: exotic Word spaces become plain spaces and
     * every whitespace run collapses to a single space. The function is idempotent,
     * so applying it to already-normalised text changes nothing.
     *
     * The three steps are the ones Python's `normalize_whitespace` takes -- replace
     * the exotic spaces, collapse the whitespace runs, strip the ends -- and they
     * are taken in that order, because the order is part of the rule: a space that
     * only becomes whitespace once it has been replaced must be collapsed after
     * the replacement, not before it.
     *
     * This is the twin of `lmeditor.word.text.normalize_whitespace`. The shared
     * corpus is what says the two are the same rule.
     */
    fun normalizeWhitespace(text: String): String {
        if (text.isEmpty()) return ""
        val translated = buildString(text.length) {
            for (ch in text) {
                val replacement = REPLACED_SPACES[ch]
                if (replacement != null) append(replacement) else append(ch)
            }
        }
        val collapsed = buildString(translated.length) {
            var pendingSpace = false
            for (ch in translated) {
                if (isWhitespace(ch)) {
                    pendingSpace = this.isNotEmpty()
                } else {
                    if (pendingSpace) {
                        append(' ')
                        pendingSpace = false
                    }
                    append(ch)
                }
            }
        }
        return collapsed.trim { isWhitespace(it) }
    }

    /**
     * Join text fragments with [separator] and normalise the result.
     *
     * Empty fragments are dropped so that a paragraph with an empty first run does
     * not begin with a stray separator.
     */
    fun joinTexts(parts: Iterable<String>, separator: String = " "): String {
        val kept = parts.filter { it.isNotEmpty() }
        if (kept.isEmpty()) return ""
        return normalizeWhitespace(kept.joinToString(separator))
    }

    /**
     * Remove XML-illegal control characters that Word sometimes leaves behind.
     *
     * Characters below `U+0020` other than tab, newline and carriage return are
     * not permitted in XML 1.0 content and cannot be written back out, so they are
     * removed rather than carried into a document model that could never be saved.
     */
    fun stripControlCharacters(text: String): String {
        val out = StringBuilder(text.length)
        for (ch in text) {
            if (ch == '\t' || ch == '\n' || ch == '\r' || ch >= ' ') out.append(ch)
        }
        return out.toString()
    }
}
