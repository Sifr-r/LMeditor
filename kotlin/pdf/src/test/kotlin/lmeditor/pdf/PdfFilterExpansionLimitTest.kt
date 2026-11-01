package lmeditor.pdf

import lmeditor.core.DecompressionLimits
import lmeditor.core.SourceLoadError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * Every PDF filter holds its expansion to the shared bound.
 *
 * The bound is written once, in `lmeditor.core.DecompressionLimits`. The Flate
 * decoder is held to it, and so are the three filters that expand a stream by
 * another route -- the ASCII85, the hexadecimal and the run-length decoders.
 *
 * They refuse in the same way as well: with the same typed error, and before
 * the bytes are written, so a stream that expands past the bound is refused
 * rather than truncated and the reader never allocates the expansion it
 * refuses.
 */
class PdfFilterExpansionLimitTest {

    /**
     * The reason the Flate path gives when a stream expands past the bound.
     *
     * Every filter gives this same reason, so a filter that drifted away from
     * the Flate path -- a bound of its own, or a refusal of another kind --
     * fails one of the tests below.
     */
    private val tooLargeReason =
        "the stream expands to more than ${DecompressionLimits.MAX_EXPANDED_BYTES} bytes"

    /**
     * The Flate path holds its expansion to the shared bound.
     *
     * The Flate path is the one that was already held to the bound. The tests
     * that follow are the ones that say the other filters are held to it in
     * the same way, and the same bound: a stream that expands to exactly the
     * shared size is decoded, and one that expands to a byte more is refused.
     */
    @Test
    fun `the Flate path holds its expansion to the shared bound`() {
        val atBound = deflate(payloadOfSize(DecompressionLimits.MAX_EXPANDED_BYTES.toInt()))
        assertTrue(
            atBound.size.toLong() * DecompressionLimits.MAX_COMPRESSION_RATIO >=
                DecompressionLimits.MAX_EXPANDED_BYTES,
            "the payload must stay under the ratio bound, so the size bound is the one that fires",
        )
        assertEquals(
            DecompressionLimits.MAX_EXPANDED_BYTES,
            decodedSize("FlateDecode", atBound),
            "a stream that expands to exactly the shared bound must be decoded",
        )

        val overBound = deflate(payloadOfSize(DecompressionLimits.MAX_EXPANDED_BYTES.toInt() + 1))
        val failure = refusalOf("FlateDecode", overBound)
        assertEquals(tooLargeReason, failure.reason)
    }

    /**
     * The ASCII85 path holds its expansion to the shared bound.
     *
     * The `z` shortcut is the cheapest way to make a stream expand: one
     * character decodes to four bytes, so a stream of a sixteenth of the
     * shared bound expands to the whole of it.
     */
    @Test
    fun `the ASCII85 path holds its expansion to the shared bound`() {
        val groups = (DecompressionLimits.MAX_EXPANDED_BYTES / 4).toInt()
        assertEquals(
            DecompressionLimits.MAX_EXPANDED_BYTES,
            decodedSize("ASCII85Decode", ascii85OfGroups(groups)),
            "a stream that expands to exactly the shared bound must be decoded",
        )

        val failure = refusalOf("ASCII85Decode", ascii85OfGroups(groups + 1))
        assertEquals(tooLargeReason, failure.reason)
    }

    /**
     * The hexadecimal path holds its expansion to the shared bound.
     *
     * Two hexadecimal digits decode to one byte, so a stream of twice the
     * shared bound expands to the whole of it.
     */
    @Test
    fun `the hexadecimal path holds its expansion to the shared bound`() {
        val digits = (DecompressionLimits.MAX_EXPANDED_BYTES * 2).toInt()
        assertEquals(
            DecompressionLimits.MAX_EXPANDED_BYTES,
            decodedSize("ASCIIHexDecode", hexOfDigits(digits)),
            "a stream that expands to exactly the shared bound must be decoded",
        )

        val failure = refusalOf("ASCIIHexDecode", hexOfDigits(digits + 2))
        assertEquals(tooLargeReason, failure.reason)
    }

    /**
     * The run-length path holds its expansion to the shared bound.
     *
     * A two-byte run decodes to a hundred and twenty-eight bytes, so a
     * stream of a hundred and twenty-eighth of the shared bound expands to
     * the whole of it.
     */
    @Test
    fun `the run-length path holds its expansion to the shared bound`() {
        val runs = (DecompressionLimits.MAX_EXPANDED_BYTES / 128).toInt()
        assertEquals(
            DecompressionLimits.MAX_EXPANDED_BYTES,
            decodedSize("RunLengthDecode", runLengthOfRuns(runs)),
            "a stream that expands to exactly the shared bound must be decoded",
        )

        val failure = refusalOf("RunLengthDecode", runLengthOfRuns(runs + 1))
        assertEquals(tooLargeReason, failure.reason)
    }

    /**
     * A document whose content stream expands past the bound is refused with
     * the same typed error the Flate path gives.
     *
     * The refusal is the tier's, not the filter's: a document that cannot be
     * read within the cap is a refused document, not a document with a page
     * missing from it.
     */
    @Test
    fun `a document whose content stream expands past the bound is refused`() {
        val runs = (DecompressionLimits.MAX_EXPANDED_BYTES / 128).toInt() + 1
        val pdf = PdfFixtures.build(
            listOf(
                PdfFixtures.Obj("<< ${PdfFixtures.DEFAULT_INFO} >>"),
                PdfFixtures.Obj("<< /Type /Pages /Count 1 /Kids [ 4 0 R ] >>"),
                PdfFixtures.Obj("<< /Type /Catalog /Pages 2 0 R >>"),
                PdfFixtures.Obj(
                    "<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 612 792 ] /Resources " +
                        "<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> " +
                        "/Contents 5 0 R >>",
                ),
                PdfFixtures.Obj("/Filter /RunLengthDecode", runLengthOfRuns(runs)),
            ),
        )

        val failure = assertFailsWith<SourceLoadError> {
            PdfDocument.parse(pdf)
            Unit
        }

        assertEquals("pdf", failure.kind)
        assertEquals(tooLargeReason, failure.reason)
    }

    /** A stream that carries [data] under the filter [filter]. */
    private fun stream(filter: String, data: ByteArray): PdfStream =
        PdfStream(PdfDictionary(mapOf("Filter" to PdfName(filter))), data)

    /**
     * The refusal the filter [filter] raises for [data].
     *
     * The block yields nothing, so a failure never has to render the decoded
     * stream -- which is the very thing that is too large to render.
     */
    private fun refusalOf(filter: String, data: ByteArray): PdfStreamTooLargeError =
        assertFailsWith<PdfStreamTooLargeError> {
            PdfFilters.decode(stream(filter, data))
            Unit
        }

    /** The size of the data the filter [filter] decodes [data] to. */
    private fun decodedSize(filter: String, data: ByteArray): Long =
        when (val outcome = PdfFilters.decode(stream(filter, data))) {
            is PdfFilters.Outcome.Decoded -> outcome.data.size.toLong()
            is PdfFilters.Outcome.Failed -> fail("the stream was not decoded: ${outcome.reason}")
        }

    /**
     * A payload of [size] bytes that deflates to about a hundred and
     * twenty-eighth of its size.
     *
     * The bound is checked on the expansion, so a payload that compressed far
     * better would trip the ratio rule before the size rule, and the test
     * would be measuring the wrong bound.
     */
    private fun payloadOfSize(size: Int): ByteArray {
        val payload = ByteArray(size)
        var seed = 0x9e3779b9.toInt()
        for (index in payload.indices step 128) {
            seed = seed xor (seed shl 13)
            seed = seed xor (seed ushr 17)
            seed = seed xor (seed shl 5)
            payload[index] = (seed shr 16).toByte()
        }
        return payload
    }

    /** Compress [data] the way a PDF producer compresses a content stream. */
    private fun deflate(data: ByteArray): ByteArray = PdfFixtures.deflate(data)

    /**
     * An ASCII85 stream of [groups] `z` groups, each of which decodes to
     * four zero bytes.
     */
    private fun ascii85OfGroups(groups: Int): ByteArray =
        ByteArray(groups) { 'z'.code.toByte() }

    /** A hexadecimal stream of [digits] digits, each of which is a `4`. */
    private fun hexOfDigits(digits: Int): ByteArray =
        ByteArray(digits) { '4'.code.toByte() }

    /**
     * A run-length stream of [runs] runs, each of which decodes to a hundred
     * and twenty-eight `A` bytes.
     */
    private fun runLengthOfRuns(runs: Int): ByteArray {
        val data = ByteArray(runs * 2)
        for (index in data.indices step 2) {
            data[index] = 129.toByte()
            data[index + 1] = 0x41.toByte()
        }
        return data
    }
}
