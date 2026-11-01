package lmeditor.pdf

import lmeditor.core.DecompressionLimits
import lmeditor.core.SourceLoadError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The failures a malformed or a hostile PDF must be reported as.
 *
 * A file that cannot be read is refused with a typed error: never with a raw
 * runtime exception out of the reader, and never by quietly dropping what it
 * could not read.
 */
class PdfRobustnessTest {

    /**
     * A cross-reference offset that points before the start of the file is
     * refused with a typed error.
     *
     * The offset used to be used as an index without a bounds check, so a
     * negative one threw an `ArrayIndexOutOfBoundsException` out of the
     * reader instead of being reported.
     */
    @Test
    fun `a PDF whose cross-reference offset is negative is refused with a typed error`() {
        val failure = assertFailsWith<SourceLoadError> {
            PdfDocument.parse(pdfWithNegativeOffset())
        }

        assertEquals("pdf", failure.kind)
        assertTrue(failure.reason.contains("catalog"), failure.reason)
    }

    /**
     * A file that is shorter than the header it must carry is refused with a
     * typed error.
     *
     * The header used to be looked for in a file that was too short to hold
     * it, which threw an `ArrayIndexOutOfBoundsException` out of the reader.
     */
    @Test
    fun `a file shorter than the PDF header is refused with a typed error`() {
        val failure = assertFailsWith<SourceLoadError> {
            PdfDocument.parse("%PD".toByteArray(Charsets.ISO_8859_1))
        }

        assertEquals("pdf", failure.kind)
        assertTrue(failure.reason.contains("%PDF-"), failure.reason)
    }

    /**
     * A content stream that expands far beyond its compressed size is refused.
     *
     * The cap is the shared ratio cap, and the refusal is a typed
     * error: the stream is not inflated at all.
     */
    @Test
    fun `a content stream that expands far beyond its compressed size is refused`() {
        val compressed = PdfFixtures.deflate(ByteArray(1024 * 1024))
        val pdf = pdfWithContent("/Filter /FlateDecode", compressed)

        val failure = assertFailsWith<SourceLoadError> { PdfDocument.parse(pdf) }

        assertEquals("pdf", failure.kind)
        assertTrue(failure.reason.contains("expands more than"), failure.reason)
    }

    /**
     * A content stream that expands past the largest allowed size is refused.
     *
     * The size cap is the shared expansion cap, and it fires before the
     * ratio cap for this file: the bomb expands past the size while staying
     * under the ratio.
     */
    @Test
    fun `a content stream that expands past the largest allowed size is refused`() {
        val compressed = PdfFixtures.deflate(expandingPayload())
        assertTrue(
            compressed.size.toLong() * DecompressionLimits.MAX_COMPRESSION_RATIO >=
                DecompressionLimits.MAX_EXPANDED_BYTES,
            "the bomb must stay under the ratio cap, so the size cap is the one that fires",
        )
        val pdf = pdfWithContent("/Filter /FlateDecode", compressed)

        val failure = assertFailsWith<SourceLoadError> { PdfDocument.parse(pdf) }

        assertEquals("pdf", failure.kind)
        assertTrue(failure.reason.contains("expands to more than"), failure.reason)
    }

    /**
     * A predictor row that claims to be larger than the cap is refused.
     *
     * The row length comes from the file, so a file that claims a row of a
     * hundred megabytes must not make the reader allocate one.
     */
    @Test
    fun `a predictor row larger than the cap is refused with a typed error`() {
        val pdf = pdfWithContent(
            "/Filter /ASCIIHexDecode /DecodeParms << /Predictor 12 /Columns 100000000 >>",
            "4142".toByteArray(Charsets.ISO_8859_1),
        )

        val failure = assertFailsWith<SourceLoadError> { PdfDocument.parse(pdf) }

        assertEquals("pdf", failure.kind)
        assertTrue(failure.reason.contains("predictor row"), failure.reason)
    }

    /**
     * A PDF whose offsets no longer resolve still yields its metadata.
     *
     * The file is the checked-in fixture with its line endings converted, so
     * every offset it records points at the wrong place. The objects are found
     * by the scan, and the information dictionary is found by the keys it
     * holds -- it carries no `/Type` -- so the document still answers with
     * what the file says.
     */
    @Test
    fun `a PDF whose offsets no longer resolve still yields its metadata`() {
        val converted = String(readMinimalFixture(), Charsets.ISO_8859_1)
            .replace("\n", "\r\n")
            .toByteArray(Charsets.ISO_8859_1)

        val document = PdfDocument.parse(converted)

        assertEquals("Municipal Waste Audit", document.info["Title"])
        assertTrue(
            document.pages[0].text.contains("Municipal waste collection rates fell by 30 percent"),
            "the text was: ${document.pages[0].text}",
        )
    }

    /**
     * A PDF whose cross-reference table points at the wrong place is still read.
     *
     * A file that a reader can open must not be refused over a broken table.
     */
    @Test
    fun `a PDF whose offsets do not resolve is still read through the scan`() {
        val broken = String(PdfFixtures.onePage(), Charsets.ISO_8859_1)
            .replace(Regex("""\d{10} 00000 n"""), "9999999999 00000 n")
            .toByteArray(Charsets.ISO_8859_1)

        val document = PdfDocument.parse(broken)

        assertEquals("Municipal Waste Audit", document.info["Title"])
        assertTrue(
            document.pages[0].text.contains("Municipal waste collection rates"),
            "the text was: ${document.pages[0].text}",
        )
    }

    /**
     * A PDF whose cross-reference table names no catalog at all is refused.
     *
     * The scan finds no catalog either, so the file cannot be read and the
     * refusal is typed.
     */
    @Test
    fun `a PDF without a catalog is refused with a typed error`() {
        val failure = assertFailsWith<SourceLoadError> {
            PdfDocument.parse("%PDF-1.4\nnothing here\n".toByteArray(Charsets.ISO_8859_1))
        }

        assertEquals("pdf", failure.kind)
        assertTrue(failure.reason.contains("catalog"), failure.reason)
    }

    private fun readMinimalFixture(): ByteArray {
        val stream = checkNotNull(
            PdfRobustnessTest::class.java.getResourceAsStream("/fixtures/minimal.pdf"),
        ) { "the minimal.pdf fixture is missing from the test resources" }
        return stream.use { it.readBytes() }
    }

    /** A PDF whose cross-reference table points its only object before the file. */
    private fun pdfWithNegativeOffset(): ByteArray {
        val head = "%PDF-1.4\n"
        val table = "xref\n0 2\n0000000000 65535 f \n-000000001 00000 n \n"
        val trailer = "trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n${head.length}\n%%EOF\n"
        return (head + table + trailer).toByteArray(Charsets.ISO_8859_1)
    }

    /** A one-page PDF whose content stream carries [content] under [dictionary]. */
    private fun pdfWithContent(dictionary: String, content: ByteArray): ByteArray = PdfFixtures.build(
        listOf(
            PdfFixtures.Obj("<< ${PdfFixtures.DEFAULT_INFO} >>"),
            PdfFixtures.Obj("<< /Type /Pages /Count 1 /Kids [ 4 0 R ] >>"),
            PdfFixtures.Obj("<< /Type /Catalog /Pages 2 0 R >>"),
            PdfFixtures.Obj(
                "<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 612 792 ] /Resources " +
                    "<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> " +
                    "/Contents 5 0 R >>",
            ),
            PdfFixtures.Obj(dictionary, content),
        ),
    )

    /**
     * A payload that expands past the size cap without expanding past the ratio cap.
     *
     * A block that does not compress is repeated until the payload is larger
     * than the size cap: its compressed form stays a few hundred kilobytes, so
     * the ratio stays under its cap and the size cap is the one that fires.
     */
    private fun expandingPayload(): ByteArray {
        val block = ByteArray(4096)
        var seed = 0x9e3779b9.toInt()
        for (index in block.indices) {
            seed = seed xor (seed shl 13)
            seed = seed xor (seed ushr 17)
            seed = seed xor (seed shl 5)
            block[index] = (seed shr 16).toByte()
        }
        val payload = ByteArray(66 * 1024 * 1024)
        var written = 0
        while (written < payload.size) {
            val count = minOf(block.size, payload.size - written)
            System.arraycopy(block, 0, payload, written, count)
            written += count
        }
        return payload
    }
}
