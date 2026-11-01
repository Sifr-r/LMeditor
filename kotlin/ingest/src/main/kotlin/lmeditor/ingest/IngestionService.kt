package lmeditor.ingest

import lmeditor.core.Chunker
import lmeditor.core.Source
import lmeditor.core.SourceIngestResult
import lmeditor.core.SourceKind
import lmeditor.text.SentenceAwareChunker
import java.time.Instant

/**
 * Orchestrates the full ingestion pipeline for one source.
 *
 * The pipeline is: load -> resolve bibliography -> chunk -> return. Every step
 * is deterministic, and the service owns nothing but the wiring between them.
 */
class IngestionService(
    /** The registry that maps a source kind to its loader. */
    val registry: SourceLoaderRegistry,
    /** The chain that resolves the bibliographic record. */
    val resolvers: BibliographyResolverChain,
    /** The chunker that segments the loaded text. */
    val chunker: Chunker,
) {
    companion object {
        /** Return the service the ingestion pipeline uses by default. */
        fun default(): IngestionService = IngestionService(
            registry = SourceLoaderRegistry.default(),
            resolvers = BibliographyResolverChain.default(),
            chunker = SentenceAwareChunker(chunkSize = 120, overlapSize = 25),
        )
    }

    /**
     * Ingest one source.
     *
     * @param sourceId the id the source is known by.
     * @param kind the kind of the source.
     * @param origin where the source comes from: a path for every kind but
     *   [SourceKind.URL], and the URL itself for that one.
     * @param title the title the operator gave the source.
     * @param doi the DOI the operator declared, when they declared one.
     * @param arxivId the arXiv id the operator declared, when they declared one.
     * @param pmid the PMID the operator declared, when they declared one.
     * @param pdfMetadata the bibliographic metadata the operator supplied; it
     *   wins over the metadata the loader found.
     * @param byteSize the size of the artifact, when the caller knows it.
     * @throws lmeditor.core.SourceLoadError the source could not be loaded.
     * @throws lmeditor.core.BibliographyResolutionError the bibliography could
     *   not be resolved.
     */
    fun ingest(
        sourceId: String,
        kind: SourceKind,
        origin: String,
        title: String,
        doi: String? = null,
        arxivId: String? = null,
        pmid: String? = null,
        pdfMetadata: Map<String, String>? = null,
        byteSize: Long? = null,
    ): SourceIngestResult {
        val loaded = registry.load(kind, origin)

        // The loader's own metadata wins over nothing, and an explicitly
        // supplied one wins over the loader's: the caller knows more than the
        // document does.
        val mergedMetadata = LinkedHashMap(loaded.pdfMetadata)
        if (!pdfMetadata.isNullOrEmpty()) {
            mergedMetadata.putAll(pdfMetadata)
        }

        val bibliography = if (doi != null || arxivId != null || pmid != null || mergedMetadata.isNotEmpty()) {
            resolvers.resolve(
                ResolutionRequest(
                    sourceId = sourceId,
                    sourceKind = kind,
                    doi = doi,
                    arxivId = arxivId,
                    pmid = pmid,
                    pdfMetadata = mergedMetadata,
                ),
            )
        } else {
            null
        }

        val source = Source(
            id = sourceId,
            kind = kind,
            origin = origin,
            title = title,
            bibliography = bibliography,
            pageCount = loaded.pageCount,
            byteSize = (byteSize ?: loaded.byteSize)?.coerceAtMost(Int.MAX_VALUE.toLong())?.toInt(),
            omissions = loaded.omissions,
            // The moment the source was added. It is stamped here so the wire
            // form of an ingested source is complete: a caller on the other
            // side of the seam gets the time the source was created, not an
            // empty field it has to fill in itself.
            addedAt = Instant.now().toString(),
        )

        val chunks = chunker.chunk(
            text = loaded.text,
            documentId = sourceId,
            metadata = mapOf("document_title" to title),
        )

        return SourceIngestResult(
            source = source,
            chunks = chunks,
            omissions = loaded.omissions,
        )
    }
}
