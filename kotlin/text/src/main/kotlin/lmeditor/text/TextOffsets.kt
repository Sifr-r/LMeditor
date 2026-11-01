package lmeditor.text

import lmeditor.core.CodePoints
import lmeditor.core.Citations

/**
 * Code-point aware string offsets.
 *
 * The Python tier indexes strings by Unicode code point; Kotlin indexes them
 * by UTF-16 code unit. The two disagree as soon as the text holds a character
 * outside the Basic Multilingual Plane, so every character offset this module
 * reports -- and every byte offset derived from one -- is computed in code
 * points, exactly as the Python tier computes it. A chunk id minted on one
 * side of the seam therefore resolves on the other, astral characters and all.
 *
 * The rule itself lives in `lmeditor.core.CodePoints`, so that the core and
 * this module cannot drift apart about where a character boundary is.
 */
internal object TextOffsets {

    /** The number of code points in [text]. */
    fun length(text: String): Int = CodePoints.length(text)

    /**
     * The sub-string of [text] between the code-point offsets [start] and [end].
     *
     * Offsets outside the text are clamped, as Python's slicing clamps them.
     */
    fun slice(text: String, start: Int, end: Int): String = CodePoints.slice(text, start, end)

    /** The UTF-16 offset of the code-point offset [codePointIndex]. */
    fun utf16Offset(text: String, codePointIndex: Int): Int =
        CodePoints.utf16Offset(text, codePointIndex)

    /** The code-point offset of the UTF-16 offset [utf16Index]. */
    fun codePointOffset(text: String, utf16Index: Int): Int =
        CodePoints.offsetOf(text, utf16Index)

    /**
     * The code-point offset of the first occurrence of [needle] at or after the
     * code-point offset [fromCodePoint], or -1 when there is none.
     */
    fun indexOf(text: String, needle: String, fromCodePoint: Int): Int {
        if (fromCodePoint < 0) return -1
        val start = utf16Offset(text, fromCodePoint)
        if (start > text.length) return -1
        val found = text.indexOf(needle, start)
        if (found < 0) return -1
        return codePointOffset(text, found)
    }

    /** The number of UTF-8 bytes the first [codePointEnd] code points of [text] occupy. */
    fun utf8Length(text: String, codePointEnd: Int): Int =
        CodePoints.utf8Length(text, codePointEnd)

    /** The number of UTF-8 bytes [text] occupies. */
    fun utf8Size(text: String): Int = CodePoints.utf8Size(text)

    /**
     * The citation markers in [text], with their offsets in code points.
     *
     * The marker grammar *and* the offset space are the canonical ones from
     * `lmeditor.core`: the core reports code-point offsets, which is the
     * space the Python tier uses, so there is nothing to translate here.
     */
    fun citationSpans(text: String): List<Citations.CitationSpan> = Citations.iterCitationSpans(text)
}
