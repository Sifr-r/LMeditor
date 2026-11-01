package lmeditor.word

/**
 * A string addressed by code-point index.
 *
 * The citation grammar reports offsets in code points, so the inline parser has
 * to address the text the same way; a Java string is addressed in UTF-16 units,
 * and the two differ as soon as the text carries an astral character. An
 * offset taken from one and applied to the other lands in the middle of a
 * surrogate pair, which is how a marker ends up half consumed.
 */
internal class CodePointText private constructor(private val points: IntArray) {

    /** The number of code points in the text. */
    val length: Int get() = points.size

    /** Return the code point at [index]. */
    fun at(index: Int): Int = points[index]

    /** Return the text between two code-point offsets. */
    fun substring(start: Int, end: Int): String {
        val builder = StringBuilder()
        for (index in start until minOf(end, points.size)) {
            builder.appendCodePoint(points[index])
        }
        return builder.toString()
    }

    companion object {
        /** Return [text] addressed by code point. */
        fun of(text: String): CodePointText = CodePointText(text.codePoints().toArray())
    }
}
