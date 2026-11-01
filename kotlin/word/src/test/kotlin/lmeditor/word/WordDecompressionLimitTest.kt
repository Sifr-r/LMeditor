package lmeditor.word

import lmeditor.core.DecompressionLimits
import lmeditor.core.WordParseError
import kotlin.test.Test
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The DOCX reader holds an untrusted package to the shared decompression
 * bound.
 *
 * The bound is the one in `lmeditor.core`: a `.docx` package part and a PDF
 * content stream are the same threat -- a small file that makes the reader
 * allocate a large one -- so the two readers are refused by the same numbers,
 * and the numbers are written once.
 */
class WordDecompressionLimitTest {

    /**
     * A package part that expands past the shared bound is refused.
     *
     * The part is a member of zeros, so the package on disk is tiny and the
     * reader is the only thing that expands it.
     */
    @Test
    fun `a package part that expands past the shared bound is refused`() {
        val payload = ByteArray(DecompressionLimits.MAX_EXPANDED_BYTES.toInt() + 1)
        val archive = WordFixtures.zipBytes(listOf("word/document.xml" to payload))

        val failure = assertFailsWith<WordParseError> { WordReader().readBytes(archive) }

        assertTrue(
            failure.reason.contains("expands to more than ${DecompressionLimits.MAX_EXPANDED_BYTES} bytes"),
            failure.reason,
        )
    }

    /**
     * A package part that expands far beyond its compressed size is refused.
     *
     * The ratio is the shared ratio bound, and a member of zeros expands a
     * thousand times its stored size.
     */
    @Test
    fun `a package part that expands far beyond its compressed size is refused`() {
        val archive = WordFixtures.zipBytes(listOf("word/document.xml" to ByteArray(1024 * 1024)))

        val failure = assertFailsWith<WordParseError> { WordReader().readBytes(archive) }

        assertTrue(
            failure.reason.contains("more than the ${DecompressionLimits.MAX_COMPRESSION_RATIO} allowed"),
            failure.reason,
        )
    }

    /**
     * A package within the shared bound is still read.
     *
     * The bound is a bound and not a blanket refusal: a document that stays
     * inside it is read as it always was.
     */
    @Test
    fun `a package within the shared bound is still read`() {
        val document = WordReader().readBytes(WordFixtures.buildWord365Fixture())

        assertTrue(
            document.blocks.isNotEmpty(),
            "the fixture package must still be read into the document model",
        )
    }
}
