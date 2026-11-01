package lmeditor.pdf

import lmeditor.core.SourceLoadError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import java.nio.file.Files
import java.nio.file.Path

/**
 * The rules the text of a PDF follows.
 */
class PdfTextTest {

    @Test
    fun `a one-page PDF yields the text its page draws`() {
        val document = PdfDocument.parse(PdfFixtures.onePage())

        assertEquals(1, document.pageCount)
        assertTrue(
            document.pages[0].text.contains("Municipal waste collection rates fell by 30 percent between 2019 and 2023."),
            "the text of the page was: ${document.pages[0].text}",
        )
    }

    @Test
    fun `the lines of a page stay apart`() {
        val document = PdfDocument.parse(PdfFixtures.onePage())

        val lines = document.pages[0].text.lines().filter { it.isNotBlank() }
        assertEquals(2, lines.size, "the text was: ${document.pages[0].text}")
        assertTrue(lines[0].startsWith("Municipal waste collection rates"))
        assertTrue(lines[1].startsWith("The decline was driven by"))
    }

    @Test
    fun `a Flate-compressed content stream is inflated before it is read`() {
        val compressed = PdfDocument.parse(PdfFixtures.onePage(compress = true))
        val plain = PdfDocument.parse(PdfFixtures.onePage())

        assertEquals(plain.pages[0].text, compressed.pages[0].text)
        assertTrue(compressed.pages[0].text.contains("citywide composting programme"))
    }

    @Test
    fun `a PDF with two pages reports two pages`() {
        val document = PdfDocument.parse(
            PdfFixtures.twoPages(
                "BT /F1 12 Tf 72 720 Td (First page.) Tj ET",
                "BT /F1 12 Tf 72 720 Td (Second page.) Tj ET",
            ),
        )

        assertEquals(2, document.pageCount)
        assertEquals(2, PdfText.pageTexts(document).size)
        assertTrue(PdfText.pageTexts(document)[0].contains("First page."))
        assertTrue(PdfText.pageTexts(document)[1].contains("Second page."))
    }

    @Test
    fun `the text of a PDF joins its pages with a blank line`() {
        val document = PdfDocument.parse(
            PdfFixtures.twoPages(
                "BT /F1 12 Tf 72 720 Td (First page.) Tj ET",
                "BT /F1 12 Tf 72 720 Td (Second page.) Tj ET",
            ),
        )

        assertTrue(PdfText.text(document).contains("First page.\n\nSecond page."))
    }

    @Test
    fun `a file that is not a PDF is refused with a typed error`() {
        val failure = assertFailsWith<SourceLoadError> {
            PdfDocument.parse("this is not a pdf".toByteArray(Charsets.ISO_8859_1))
        }

        assertEquals("pdf", failure.kind)
        assertTrue(failure.reason.contains("%PDF-"), failure.reason)
    }

    @Test
    fun `an empty file is refused with a typed error`() {
        assertFailsWith<SourceLoadError> { PdfDocument.parse(ByteArray(0)) }
    }

    @Test
    fun `a missing file is refused with a typed error`() {
        val missing = Path.of("does-not-exist.pdf")

        val failure = assertFailsWith<SourceLoadError> { PdfDocument.open(missing) }

        assertEquals("pdf", failure.kind)
        assertTrue(failure.reason.contains("does not exist"), failure.reason)
    }

    @Test
    fun `an encrypted PDF is refused instead of read as an empty document`() {
        val failure = assertFailsWith<SourceLoadError> {
            PdfDocument.parse(PdfFixtures.encrypted())
        }

        assertTrue(failure.reason.contains("encrypted"), failure.reason)
    }

    @Test
    fun `a PDF without text says so instead of looking empty`() {
        val document = PdfDocument.parse(
            PdfFixtures.onePage("q 1 0 0 1 0 0 cm /Im0 Do Q"),
        )

        assertEquals("", document.pages[0].text)
        assertTrue(
            document.issues.any { it.kind == PdfIssueKind.NO_TEXT },
            "the issues were: ${document.issues}",
        )
    }

    @Test
    fun `a PDF that stores objects in object streams reports it`() {
        val document = PdfDocument.parse(PdfFixtures.withObjectStream())

        assertTrue(
            document.issues.any { it.kind == PdfIssueKind.OBJECT_STREAMS },
            "the issues were: ${document.issues}",
        )
    }

    @Test
    fun `a subsetted font is read through its ToUnicode CMap`() {
        val document = PdfDocument.parse(PdfFixtures.withToUnicodeFont())

        assertEquals("Hi", document.pages[0].text.trim())
    }

    @Test
    fun `the checked-in minimal PDF yields its text and its page count`() {
        val bytes = readMinimalFixture()

        val document = PdfDocument.parse(bytes)

        assertEquals(1, document.pageCount)
        assertTrue(
            document.pages[0].text.contains("Municipal waste collection rates fell by 30 percent"),
            "the text of the fixture was: ${document.pages[0].text}",
        )
        assertEquals("Municipal Waste Audit", document.info["Title"])
    }

    @Test
    fun `the text of the checked-in minimal PDF is the text the Python tier extracts`() {
        val document = PdfDocument.parse(readMinimalFixture())

        // The Python tier reads this file with pypdf and strips the result, so
        // the two tiers must agree on exactly these two lines.
        assertEquals(
            "Municipal waste collection rates fell by 30 percent between 2019 and 2023.\n" +
                "The decline was driven by a citywide composting programme introduced in 2020.",
            document.pages[0].text,
        )
    }

    @Test
    fun `a PDF whose cross-reference table points at the wrong place is still read`() {
        val broken = String(PdfFixtures.onePage(), Charsets.ISO_8859_1)
            .replace(Regex("""\d{10} 00000 n"""), "9999999999 00000 n")
            .toByteArray(Charsets.ISO_8859_1)

        val document = PdfDocument.parse(broken)

        assertTrue(
            document.pages[0].text.contains("Municipal waste collection rates"),
            "the text was: ${document.pages[0].text}",
        )
    }

    @Test
    fun `a wide kerning adjustment in a TJ array leaves a word space`() {
        val pdf = PdfFixtures.onePage(content = "BT /F1 12 Tf 72 720 Td [(First)-300(Second)] TJ ET")

        val text = PdfDocument.parse(pdf).pages[0].text

        assertTrue(text.contains("First Second"), "the text was: $text")
    }

    @Test
    fun `a tight kerning adjustment in a TJ array leaves no word space`() {
        val pdf = PdfFixtures.onePage(content = "BT /F1 12 Tf 72 720 Td [(First)-30(Second)] TJ ET")

        val text = PdfDocument.parse(pdf).pages[0].text

        assertTrue(text.contains("FirstSecond"), "the text was: $text")
    }

    private fun readMinimalFixture(): ByteArray {
        val stream = checkNotNull(
            PdfTextTest::class.java.getResourceAsStream("/fixtures/minimal.pdf"),
        ) { "the minimal.pdf fixture is missing from the test resources" }
        return stream.use { it.readBytes() }
    }

    @Test
    fun `a PDF read from disk yields the same text as the same bytes`() {
        val directory = Files.createTempDirectory("lmeditor-pdf")
        try {
            val path = directory.resolve("paper.pdf")
            Files.write(path, PdfFixtures.onePage())

            assertEquals(PdfText.text(PdfFixtures.onePage()), PdfText.text(path))
        } finally {
            Files.deleteIfExists(directory.resolve("paper.pdf"))
            Files.deleteIfExists(directory)
        }
    }
}
