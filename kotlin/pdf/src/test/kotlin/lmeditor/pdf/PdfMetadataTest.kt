package lmeditor.pdf

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The rules the metadata of a PDF follows.
 */
class PdfMetadataTest {

    @Test
    fun `the Info dictionary is read into plain keys`() {
        val metadata = PdfMetadata.extract(PdfFixtures.onePage())

        assertEquals("Municipal Waste Audit", metadata.title)
        assertEquals("LMeditor", metadata.author)
        assertEquals("Collection rates", metadata.subject)
        assertEquals("waste, composting, municipal", metadata.keywords)
        assertEquals("lmeditor test fixture", metadata.producer)
    }

    @Test
    fun `the metadata is returned as the plain-keyed mapping the Python tier returns`() {
        val map = PdfMetadata.extract(PdfFixtures.onePage()).toMap()

        assertEquals("Municipal Waste Audit", map["title"])
        assertEquals("LMeditor", map["author"])
        assertEquals("Collection rates", map["subject"])
        assertEquals("waste, composting, municipal", map["keywords"])
        assertEquals("lmeditor test fixture", map["producer"])
    }

    @Test
    fun `a DOI in the metadata is found`() {
        val pdf = PdfFixtures.onePage(
            info = "/Title (A Verifiable Writing Harness) /Author (Jane Doe) /Subject (doi:10.1234/harness)",
        )

        val metadata = PdfMetadata.extract(pdf)

        assertEquals("10.1234/harness", metadata.doi)
        assertEquals("doi:10.1234/harness", metadata.subject)
    }

    @Test
    fun `a DOI on the first page is found`() {
        val pdf = PdfFixtures.onePage(
            content = "BT /F1 12 Tf 72 720 Td (doi:10.5555/harness) Tj ET",
        )

        assertEquals("10.5555/harness", PdfMetadata.extract(pdf).doi)
    }

    @Test
    fun `the trailing punctuation of a DOI is trimmed`() {
        val pdf = PdfFixtures.onePage(
            content = "BT /F1 12 Tf 72 720 Td (See doi:10.5555/harness.) Tj ET",
        )

        assertEquals("10.5555/harness", PdfMetadata.extract(pdf).doi)
    }

    @Test
    fun `a DOI further down the first page is not the document's own`() {
        val padding = "x".repeat(3100)
        val pdf = PdfFixtures.onePage(
            content = "BT /F1 12 Tf 72 720 Td ($padding doi:10.5555/late) Tj ET",
        )

        assertNull(PdfMetadata.extract(pdf).doi)
    }

    @Test
    fun `an arXiv id on the first page is found`() {
        val pdf = PdfFixtures.onePage(
            content = "BT /F1 12 Tf 72 720 Td (arXiv:2401.01234v2) Tj ET",
        )

        assertEquals("2401.01234v2", PdfMetadata.extract(pdf).arxivId)
    }

    @Test
    fun `a legacy arXiv id is found`() {
        val pdf = PdfFixtures.onePage(
            content = "BT /F1 12 Tf 72 720 Td (arXiv: hep-th/9901001) Tj ET",
        )

        assertEquals("hep-th/9901001", PdfMetadata.extract(pdf).arxivId)
    }

    @Test
    fun `a PDF without an Info dictionary has no metadata but keeps its identifiers`() {
        val pdf = PdfFixtures.onePage(
            content = "BT /F1 12 Tf 72 720 Td (doi:10.7777/none) Tj ET",
            info = "",
        )

        val metadata = PdfMetadata.extract(pdf)

        assertNull(metadata.title)
        assertNull(metadata.author)
        assertEquals("10.7777/none", metadata.doi)
        assertEquals(mapOf("doi" to "10.7777/none"), metadata.toMap())
    }

    @Test
    fun `the identifiers of the metadata win over the ones on the first page`() {
        val pdf = PdfFixtures.onePage(
            content = "BT /F1 12 Tf 72 720 Td (doi:10.5555/page) Tj ET",
            info = "/Subject (doi:10.1234/info)",
        )

        assertEquals("10.1234/info", PdfMetadata.extract(pdf).doi)
    }
}
