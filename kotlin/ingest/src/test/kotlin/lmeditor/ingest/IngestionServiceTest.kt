package lmeditor.ingest

import lmeditor.core.BibliographyResolverKind
import lmeditor.core.SourceKind
import lmeditor.text.SentenceAwareChunker
import java.io.ByteArrayInputStream
import java.time.Duration
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The ingestion pipeline, ported from
 * `backend/tests/test_ingestion_service.py`.
 */
class IngestionServiceTest {

    /**
     * A text file is loaded, chunked and returned as a source.
     */
    @Test
    fun aTextFileIsIngested() {
        val path = writeFile(
            "notes.txt",
            "The Stanford STORM paper presents multi-perspective discovery. " +
                "It builds an outline from the retrieved sources.",
        )
        val service = IngestionService.default()
        val result = service.ingest(
            sourceId = "doc_01",
            kind = SourceKind.TEXT,
            origin = path.toString(),
            title = "A note",
        )
        assertEquals("doc_01", result.source.id)
        assertEquals(SourceKind.TEXT, result.source.kind)
        assertEquals("A note", result.source.title)
        assertTrue(result.chunks.isNotEmpty())
        assertTrue(result.chunks.all { it.documentId == "doc_01" })
        // The chunk ids are the shared digest the two tiers must agree on.
        assertTrue(result.chunks.all { it.chunkId.matches(Regex("^[a-f0-9]{64}$")) })
    }

    /**
     * The chunks of a source are the ones the chunker produces, with the
     * document's title in their metadata.
     */
    @Test
    fun theChunksCarryTheDocumentTitle() {
        val path = writeFile("notes.txt", "A short note about a subject.")
        val chunker = SentenceAwareChunker(chunkSize = 120, overlapSize = 25)
        val service = IngestionService.default().copy(chunker = chunker)
        val result = service.ingest(
            sourceId = "doc_01",
            kind = SourceKind.TEXT,
            origin = path.toString(),
            title = "A note",
        )
        assertTrue(result.chunks.isNotEmpty())
        assertEquals("A note", result.chunks.first().metadata["document_title"])
    }

    /**
     * The bibliography of a source is resolved from the identifiers the caller
     * declared.
     */
    @Test
    fun theBibliographyIsResolved() {
        val path = writeFile("notes.txt", "A note about a paper.")
        val service = IngestionService.default()
        val result = service.ingest(
            sourceId = "doc_01",
            kind = SourceKind.TEXT,
            origin = path.toString(),
            title = "A note",
            pdfMetadata = mapOf("title" to "A paper"),
        )
        assertEquals("A paper", result.source.bibliography?.title)
    }

    /**
     * A DOI the caller declared is resolved by the resolver that can verify
     * it, and the record is attached to the source.
     *
     * Ported from `test_service_attaches_bibliography_when_doi_resolves`.
     */
    @Test
    fun theServiceAttachesTheBibliographyWhenTheDoiResolves() {
        val path = writeFile("notes.txt", "A note about a paper.")
        val service = IngestionService(
            registry = SourceLoaderRegistry.default(),
            resolvers = BibliographyResolverChain(
                listOf(
                    PdfHeaderBibliographyResolver(),
                    CrossRefBibliographyResolver(transport = crossRefTransport()),
                ),
            ),
            chunker = SentenceAwareChunker(chunkSize = 120, overlapSize = 25),
        )
        val result = service.ingest(
            sourceId = "doc_01",
            kind = SourceKind.TEXT,
            origin = path.toString(),
            title = "A note",
            doi = "10.1234/svc",
        )
        val bibliography = result.source.bibliography
        assertEquals("Service Layer Title", bibliography?.title)
        assertEquals(BibliographyResolverKind.CROSSREF, bibliography?.resolver)
    }

    /**
     * A source that states its own DOI is resolved from that DOI.
     *
     * Ported from `test_service_resolves_a_pdf_from_the_doi_in_its_own_metadata`.
     */
    @Test
    fun theServiceResolvesASourceFromTheDoiInItsOwnMetadata() {
        val path = writeFile("notes.txt", "A note about a paper.")
        val service = IngestionService(
            registry = SourceLoaderRegistry.default(),
            resolvers = BibliographyResolverChain(
                listOf(
                    PdfHeaderBibliographyResolver(),
                    CrossRefBibliographyResolver(transport = crossRefTransport()),
                ),
            ),
            chunker = SentenceAwareChunker(chunkSize = 120, overlapSize = 25),
        )
        val result = service.ingest(
            sourceId = "doc_01",
            kind = SourceKind.TEXT,
            origin = path.toString(),
            title = "A note",
            pdfMetadata = mapOf("title" to "A paper", "subject" to "doi:10.1234/harness"),
        )
        val bibliography = result.source.bibliography
        assertEquals("10.1234/harness", bibliography?.doi)
        assertEquals(BibliographyResolverKind.CROSSREF, bibliography?.resolver)
    }
}

/** A transport that answers a CrossRef request with a verified record. */
private fun crossRefTransport(): HttpTransport = object : HttpTransport {
    override fun send(
        url: String,
        addresses: List<VettedAddress>,
        timeout: Duration,
    ): HttpExchange = HttpExchange(
        statusCode = 200,
        headers = mapOf("Content-Type" to listOf("application/json")),
        peerAddress = addresses.firstOrNull()?.ip,
        body = ByteArrayInputStream(
            """
            {"message": {"title": ["Service Layer Title"], "DOI": "${url.substringAfter("works/")}"}}
            """.trimIndent().toByteArray(),
        ),
    )
}

/** Return a copy of the service with another chunker. */
private fun IngestionService.copy(chunker: lmeditor.core.Chunker): IngestionService =
    IngestionService(registry, resolvers, chunker)
