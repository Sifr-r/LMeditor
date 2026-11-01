package lmeditor.pdf

import lmeditor.core.DecompressionLimits
import lmeditor.core.SourceLoadError
import lmeditor.core.WordParseError
import lmeditor.word.WordReader
import java.io.ByteArrayOutputStream
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.test.Test
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The DOCX reader and the PDF reader are held to the same decompression bound.
 *
 * The bound is written once, in `lmeditor.core`, because the two readers face
 * the same threat: a small file that makes the reader allocate a large one.
 * This test drives both readers with a payload that expands past a shared
 * bound and asks both of them to refuse it, and it asks both of them to cite
 * the same number when they do -- so the two cannot drift apart again without
 * this test saying so.
 */
class SharedDecompressionLimitTest {

    /**
     * A stream that expands past the shared size bound is refused by both
     * readers.
     */
    @Test
    fun `both readers refuse a stream that expands past the shared size bound`() {
        val payload = expandingPayload()
        val compressed = PdfFixtures.deflate(payload)
        assertTrue(
            compressed.size.toLong() * DecompressionLimits.MAX_COMPRESSION_RATIO >=
                DecompressionLimits.MAX_EXPANDED_BYTES,
            "the payload must stay under the ratio bound, so the size bound is the one that fires",
        )

        val docx = assertFailsWith<WordParseError> { WordReader().readBytes(docxWith(payload)) }
        val pdf = assertFailsWith<SourceLoadError> { PdfDocument.parse(pdfWith(compressed)) }

        assertTrue(
            docx.reason.contains("expands to more than ${DecompressionLimits.MAX_EXPANDED_BYTES} bytes"),
            docx.reason,
        )
        assertTrue(
            pdf.reason.contains("expands to more than ${DecompressionLimits.MAX_EXPANDED_BYTES} bytes"),
            pdf.reason,
        )
    }

    /**
     * A stream that expands far beyond its compressed size is refused by both
     * readers.
     */
    @Test
    fun `both readers refuse a stream that expands far beyond its compressed size`() {
        val payload = ByteArray(1024 * 1024)
        val compressed = PdfFixtures.deflate(payload)

        val docx = assertFailsWith<WordParseError> { WordReader().readBytes(docxWith(payload)) }
        val pdf = assertFailsWith<SourceLoadError> { PdfDocument.parse(pdfWith(compressed)) }

        assertTrue(
            docx.reason.contains("more than the ${DecompressionLimits.MAX_COMPRESSION_RATIO} allowed"),
            docx.reason,
        )
        assertTrue(
            pdf.reason.contains("more than ${DecompressionLimits.MAX_COMPRESSION_RATIO} times"),
            pdf.reason,
        )
    }

    /**
     * A `.docx` package whose only part is [payload].
     *
     * The part is written the way a word processor writes one -- the size and
     * the checksum are declared, the compressed size is not -- so the reader
     * sees the package a real producer would hand it.
     */
    private fun docxWith(payload: ByteArray): ByteArray {
        val buffer = ByteArrayOutputStream()
        ZipOutputStream(buffer).use { zip ->
            val entry = ZipEntry("word/document.xml")
            entry.size = payload.size.toLong()
            val crc = CRC32()
            crc.update(payload)
            entry.crc = crc.value
            zip.putNextEntry(entry)
            zip.write(payload)
            zip.closeEntry()
        }
        return buffer.toByteArray()
    }

    /** A PDF whose content stream is [compressed]. */
    private fun pdfWith(compressed: ByteArray): ByteArray = PdfFixtures.build(
        listOf(
            PdfFixtures.Obj("<< ${PdfFixtures.DEFAULT_INFO} >>"),
            PdfFixtures.Obj("<< /Type /Pages /Count 1 /Kids [ 4 0 R ] >>"),
            PdfFixtures.Obj("<< /Type /Catalog /Pages 2 0 R >>"),
            PdfFixtures.Obj(
                "<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 612 792 ] /Resources " +
                    "<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> " +
                    "/Contents 5 0 R >>",
            ),
            PdfFixtures.Obj("/Filter /FlateDecode", compressed),
        ),
    )

    /**
     * A payload that expands past the size bound without expanding past the
     * ratio bound.
     *
     * A block that does not compress is repeated until the payload is larger
     * than the size bound: its compressed form stays a few hundred kilobytes,
     * so the ratio stays under its bound and the size bound is the one that
     * fires.
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
        val payload = ByteArray(DecompressionLimits.MAX_EXPANDED_BYTES.toInt() + 1)
        var written = 0
        while (written < payload.size) {
            val count = minOf(block.size, payload.size - written)
            System.arraycopy(block, 0, payload, written, count)
            written += count
        }
        return payload
    }
}
