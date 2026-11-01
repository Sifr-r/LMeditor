package lmeditor.core

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The Python character classes, as the rules that port Python's regular
 * expressions need them.
 */
class CodePointsTest {

    @Test
    fun anAsciiLetterIsAWordCharacter() {
        assertTrue(CodePoints.isPythonWordChar('a'.code))
        assertTrue(CodePoints.isPythonWordChar('Z'.code))
    }

    @Test
    fun aUnicodeLetterIsAWordCharacter() {
        // Java's ASCII `\w` misses these; Python's `\w` does not.
        assertTrue(CodePoints.isPythonWordChar('é'.code))
        assertTrue(CodePoints.isPythonWordChar('文'.code))
        assertTrue(CodePoints.isPythonWordChar('Δ'.code))
    }

    @Test
    fun aDigitIsAWordCharacter() {
        assertTrue(CodePoints.isPythonWordChar('0'.code))
        assertTrue(CodePoints.isPythonWordChar('7'.code))
        assertTrue(CodePoints.isPythonWordChar('٣'.code))
    }

    @Test
    fun anUnderscoreIsAWordCharacter() {
        assertTrue(CodePoints.isPythonWordChar('_'.code))
    }

    @Test
    fun aCombiningMarkIsNotAWordCharacter() {
        // Python's `str.isalnum()` is false for a combining mark, and
        // `(?U)\w` -- which would admit it -- is therefore not a drop-in
        // replacement for Python's `\w`.
        assertFalse(CodePoints.isPythonWordChar(0x0301))
        assertFalse(CodePoints.isPythonWordChar(0x0303))
    }

    @Test
    fun punctuationIsNotAWordCharacter() {
        assertFalse(CodePoints.isPythonWordChar('-'.code))
        assertFalse(CodePoints.isPythonWordChar(' '.code))
        assertFalse(CodePoints.isPythonWordChar('.'.code))
    }

    @Test
    fun theNonBreakingSpacesAreWhitespace() {
        // Python's `str.isspace()` says they are; Java's
        // `Character.isWhitespace` does not.
        assertTrue(CodePoints.isPythonWhitespace(0x00A0))
        assertTrue(CodePoints.isPythonWhitespace(0x2007))
        assertTrue(CodePoints.isPythonWhitespace(0x202F))
        assertTrue(CodePoints.isPythonWhitespace(0x0085))
    }

    @Test
    fun theOrdinarySpacesAreWhitespace() {
        assertTrue(CodePoints.isPythonWhitespace(' '.code))
        assertTrue(CodePoints.isPythonWhitespace('\t'.code))
        assertTrue(CodePoints.isPythonWhitespace('\n'.code))
        assertTrue(CodePoints.isPythonWhitespace(0x3000))
        assertTrue(CodePoints.isPythonWhitespace(0x2028))
    }

    @Test
    fun aLetterIsNotWhitespace() {
        assertFalse(CodePoints.isPythonWhitespace('a'.code))
        assertFalse(CodePoints.isPythonWhitespace('文'.code))
    }
}
