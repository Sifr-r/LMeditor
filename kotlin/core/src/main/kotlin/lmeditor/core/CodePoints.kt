package lmeditor.core

/**
 * Code-point aware string offsets.
 *
 * The Python tier indexes strings by Unicode code point; Kotlin indexes them
 * by UTF-16 code unit. The two disagree as soon as the text holds a character
 * outside the Basic Multilingual Plane -- an emoji, a rare CJK ideograph, a
 * musical symbol -- so every *character* offset the tier reports is computed
 * in code points, exactly as the Python tier computes it.
 *
 * This matters beyond consistency: a chunk's `char_start`, a claim's
 * `char_start` and a citation marker's span all describe positions in the
 * same text. If some of them counted code points and others counted UTF-16
 * units, a span would point at the wrong place as soon as the text held an
 * astral character.
 *
 * Byte offsets are a separate matter and are always UTF-8 byte counts.
 */
object CodePoints {

    /** The number of code points in [text]. */
    fun length(text: String): Int = text.codePointCount(0, text.length)

    /** The code-point offset of the UTF-16 offset [utf16Index]. */
    fun offsetOf(text: String, utf16Index: Int): Int {
        if (utf16Index <= 0) return 0
        var index = 0
        var count = 0
        while (index < utf16Index && index < text.length) {
            val codePoint = text.codePointAt(index)
            index += Character.charCount(codePoint)
            count++
        }
        return count
    }

    /** The UTF-16 offset of the code-point offset [codePointIndex]. */
    fun utf16Offset(text: String, codePointIndex: Int): Int {
        if (codePointIndex <= 0) return 0
        var index = 0
        var count = 0
        while (count < codePointIndex && index < text.length) {
            val codePoint = text.codePointAt(index)
            index += Character.charCount(codePoint)
            count++
        }
        return index
    }

    /**
     * The sub-string of [text] between the code-point offsets [start] and [end].
     *
     * Offsets outside the text are clamped, as Python's slicing clamps them.
     */
    fun slice(text: String, start: Int, end: Int): String {
        val from = utf16Offset(text, start)
        val to = utf16Offset(text, end)
        if (from >= to) return ""
        return text.substring(from, to)
    }

    /** The number of UTF-8 bytes [text] occupies. */
    fun utf8Size(text: String): Int = text.toByteArray(Charsets.UTF_8).size

    /** The number of UTF-8 bytes the first [codePointEnd] code points of [text] occupy. */
    fun utf8Length(text: String, codePointEnd: Int): Int {
        if (codePointEnd <= 0) return 0
        var bytes = 0
        var index = 0
        var count = 0
        while (count < codePointEnd && index < text.length) {
            val codePoint = text.codePointAt(index)
            bytes += utf8SizeOf(codePoint)
            index += Character.charCount(codePoint)
            count++
        }
        return bytes
    }

    private fun utf8SizeOf(codePoint: Int): Int = when {
        codePoint < 0x80 -> 1
        codePoint < 0x800 -> 2
        codePoint < 0x10000 -> 3
        else -> 4
    }

    /**
     * Python's `\w`: a character that is alphanumeric or an underscore.
     *
     * Python's `str.isalnum()` is true for the letters, the decimal digits,
     * the letter numbers and the other numbers; Java's `isLetterOrDigit`
     * misses the last two, so they are asked for by category.
     *
     * Java's own `\w` is not Python's `\w` either: the ASCII one misses every
     * Unicode letter, and `(?U)\w` is not a drop-in replacement, because it
     * also admits the combining marks (`Mn`, `Mc`, `Me`) that Python's leaves
     * out. The predicate is spelled out here so that every rule that ports a
     * Python regular expression has exactly one answer to it.
     */
    fun isPythonWordChar(codePoint: Int): Boolean =
        codePoint == '_'.code ||
            Character.isLetter(codePoint) ||
            Character.isDigit(codePoint) ||
            Character.getType(codePoint) == Character.LETTER_NUMBER.toInt() ||
            Character.getType(codePoint) == Character.OTHER_NUMBER.toInt()

    /**
     * Python's `\s`: the characters `str.isspace()` calls whitespace.
     *
     * This is **not** `Character.isWhitespace`, which leaves the non-breaking
     * spaces (U+00A0, U+2007) and the narrow no-break space (U+202F) alone,
     * and does not know the next-line control (U+0085). Python treats all of
     * them as whitespace, so a rule that splits words on Python's whitespace
     * has to split them on the same one.
     */
    fun isPythonWhitespace(codePoint: Int): Boolean = when (codePoint) {
        // Tab, line feed, vertical tab, form feed, carriage return.
        0x0009, 0x000A, 0x000B, 0x000C, 0x000D -> true
        // File, group, record and unit separators.
        0x001C, 0x001D, 0x001E, 0x001F -> true
        // Space.
        0x0020 -> true
        // Next line: whitespace in Python, not in Java.
        0x0085 -> true
        // No-break space: whitespace in Python, not in Java.
        0x00A0 -> true
        // Ogham space mark.
        0x1680 -> true
        // En quad through hair space.
        in 0x2000..0x200A -> true
        // Line and paragraph separators.
        0x2028, 0x2029 -> true
        // Narrow no-break space: whitespace in Python, not in Java.
        0x202F -> true
        // Medium mathematical space.
        0x205F -> true
        // Ideographic space.
        0x3000 -> true
        else -> false
    }

    /** Python's `\s` for a single UTF-16 code unit. */
    fun isPythonWhitespace(ch: Char): Boolean = isPythonWhitespace(ch.code)
}
