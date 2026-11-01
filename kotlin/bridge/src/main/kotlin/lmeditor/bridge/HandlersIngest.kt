package lmeditor.bridge

import lmeditor.core.BibliographicRecord
import lmeditor.core.BibliographyResolutionError
import lmeditor.core.BibliographyResolverKind
import lmeditor.core.SourceKind
import lmeditor.core.ValidationError
import lmeditor.core.bibliographicRecordFromWire
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList
import lmeditor.core.toWireMap
import lmeditor.ingest.ArxivBibliographyResolver
import lmeditor.ingest.BibliographyResolver
import lmeditor.ingest.BibliographyResolverChain
import lmeditor.ingest.CrossRefBibliographyResolver
import lmeditor.ingest.IngestionService
import lmeditor.ingest.MAX_RESOLVER_RESPONSE_BYTES
import lmeditor.ingest.MAX_SOURCE_STORE_BYTES
import lmeditor.ingest.MAX_UPLOAD_BYTES
import lmeditor.ingest.MAX_URL_RESPONSE_BYTES
import lmeditor.ingest.OpenAlexBibliographyResolver
import lmeditor.ingest.PdfHeaderBibliographyResolver
import lmeditor.ingest.PubMedBibliographyResolver
import lmeditor.ingest.ResolutionRequest
import lmeditor.ingest.SourceLoaderRegistry
import lmeditor.ingest.SourceStore
import lmeditor.ingest.UserBibliographyResolver
import lmeditor.ingest.assertPublicUrl
import lmeditor.ingest.fetchLimited
import lmeditor.ingest.resolvePublicAddresses
import lmeditor.ingest.safeUploadPath
import lmeditor.ingest.uploadRoot
import lmeditor.ingest.validateSourceId
import lmeditor.ingest.validateUploadSize
import java.nio.file.Files
import java.nio.file.Paths
import java.util.Base64

/**
 * The ingestion half of the seam: loading a source, resolving its
 * bibliography, the policy that guards a fetched URL, and the bounded store
 * the uploads are persisted in.
 *
 * Everything here is a *door* onto the Kotlin tier's own rules. The Python
 * side keeps its published API and asks these methods for the answers, so
 * there is exactly one implementation of every rule and it is this one.
 */
internal fun ingestHandlers(): Map<String, (Map<String, Any?>) -> Map<String, Any?>> = mapOf(
    // --- ingest: loading a source, and the policy that guards it ---------
    "ingest.load" to { params ->
        val kind = SourceKind.fromWire(params.jsonString("kind"))
        val loaded = sourceLoaderRegistry.load(kind, sourceOrigin(params))
        mapOf(
            "text" to loaded.text,
            "page_count" to loaded.pageCount,
            "byte_size" to loaded.byteSize,
            "metadata" to loaded.pdfMetadata,
            "omissions" to loaded.omissions,
        )
    },
    "ingest.ingest" to { params ->
        val result = ingestionService.ingest(
            sourceId = params.jsonString("id"),
            kind = SourceKind.fromWire(params.jsonString("kind")),
            origin = sourceOrigin(params),
            title = params.jsonString("title"),
            doi = params["doi"]?.toString(),
            arxivId = params["arxiv_id"]?.toString(),
            pmid = params["pmid"]?.toString(),
            pdfMetadata = metadataFrom(params),
            byteSize = longOrNull(params, "byte_size"),
        )
        result.toWireMap()
    },
    "ingest.bibliography.resolve" to { params ->
        // One strategy, one answer. The strategy is named by its wire kind, so
        // the Python side can ask for exactly the resolver it holds.
        val resolver = bibliographyResolverFor(params)
        try {
            mapOf("record" to resolver.resolve(resolutionRequestFrom(params))?.toWireMap())
        } catch (exc: BibliographyResolutionError) {
            // A failed strategy is an outcome the caller already knows how to
            // read: the chain treats it as "this strategy cannot resolve". It
            // is reported as such rather than as a broken call.
            mapOf(
                "record" to null,
                "error" to mapOf(
                    "resolver" to exc.resolver,
                    "origin" to exc.origin,
                    "reason" to exc.reason,
                ),
            )
        }
    },
    "ingest.bibliography.resolve_chain" to { params ->
        // The whole chain in one call, so the *order* the tier tries the
        // strategies in -- the rule that puts the identifier-bearing
        // resolvers before the PDF-header one -- is the tier's, not the
        // caller's.
        val chain = BibliographyResolverChain(resolversFrom(params))
        mapOf("record" to chain.resolve(resolutionRequestFrom(params))?.toWireMap())
    },
    "ingest.url_policy.assert_public" to { params ->
        // The rule is the whole point of the method: a URL that resolves
        // to a private address is refused, and the refusal is an error
        // rather than a flag, so no caller can forget to read it.
        assertPublicUrl(params.jsonString("url"))
        mapOf("ok" to true)
    },
    "ingest.url_policy.resolve_public_addresses" to { params ->
        // The addresses a URL resolves to, and the port each may be
        // reached on. The resolver itself is the JDK's, so the answer is
        // the same wherever the tier runs.
        val addresses = resolvePublicAddresses(params.jsonString("url"))
        mapOf(
            "addresses" to addresses.map {
                mapOf("ip" to it.ip, "port" to it.port)
            },
        )
    },
    "ingest.url_policy.validate_source_id" to { params ->
        mapOf("source_id" to validateSourceId(params.jsonString("source_id")))
    },
    "ingest.url_policy.safe_upload_path" to { params ->
        val base = params["base"]?.toString()
        mapOf(
            "path" to safeUploadPath(
                sourceId = params.jsonString("source_id"),
                filename = params.jsonString("filename"),
                base = if (base.isNullOrBlank()) null else Paths.get(base),
            ).toString(),
        )
    },
    "ingest.url_policy.upload_root" to { params ->
        // The configured root travels with the request when the caller has
        // one: the tier's own environment is not necessarily the caller's.
        val base = params["base"]?.toString()
        mapOf("path" to uploadRoot(base).toString())
    },
    "ingest.source_store.save" to { params ->
        val store = sourceStoreFor(params)
        val content = Base64.getDecoder().decode(params.jsonString("bytes_b64"))
        mapOf(
            "path" to store.save(
                params.jsonString("source_id"),
                params.jsonString("filename"),
                content,
            ).toString(),
        )
    },
    "ingest.source_store.list" to { params ->
        val store = sourceStoreFor(params)
        mapOf(
            "files" to store.listFiles().map { it.toString() },
            "total_bytes" to store.totalBytes(),
        )
    },
    "ingest.source_store.evict" to { params ->
        val store = sourceStoreFor(params)
        mapOf("removed" to store.evict().map { it.toString() })
    },
    "ingest.source_store.touch" to { params ->
        val store = sourceStoreFor(params)
        val path = store.touch(params.jsonString("source_id"), params.jsonString("filename"))
        mapOf("path" to path?.toString())
    },
    "ingest.source_store.remove" to { params ->
        val store = sourceStoreFor(params)
        mapOf("removed" to store.remove(params.jsonString("source_id")))
    },
    "ingest.fetch_limited" to { params ->
        val body = fetchLimited(
            url = params.jsonString("url"),
            maxBytes = params.jsonInt("max_bytes", DEFAULT_MAX_BYTES),
        )
        mapOf("body_b64" to Base64.getEncoder().encodeToString(body))
    },
    "ingest.limits" to { params ->
        // The caps on every byte that crosses the ingestion boundary are
        // the tier's, and so is the rule that refuses an upload before it
        // is buffered: the server reads the whole file into memory to
        // persist it, so the only moment a cheap refusal is possible is
        // before that read.
        val size = validateUploadSize(params.jsonInt("size"))
        mapOf(
            "size" to size,
            "max_upload_bytes" to MAX_UPLOAD_BYTES,
            "max_url_response_bytes" to MAX_URL_RESPONSE_BYTES,
            "max_resolver_response_bytes" to MAX_RESOLVER_RESPONSE_BYTES,
            "max_source_store_bytes" to MAX_SOURCE_STORE_BYTES,
        )
    },
)

/**
 * The loader registry the ingestion rules use.
 *
 * The registry maps a source kind to the loader that handles it, so a caller
 * that only knows the kind never has to know the loader.
 */
private val sourceLoaderRegistry: SourceLoaderRegistry = SourceLoaderRegistry.default()

/**
 * The ingestion service the pipeline uses by default.
 */
private val ingestionService: IngestionService = IngestionService.default()

/**
 * The largest response the fetch rule will read when the caller does not say.
 */
private const val DEFAULT_MAX_BYTES: Int = 5_000_000

/**
 * The origin a source is read from.
 *
 * A path or a URL crosses the seam as a string, because the tier runs on the
 * same machine and sees the same filesystem. An uploaded artifact crosses as
 * bytes, and is written to a temporary file, because the loaders read from
 * the filesystem.
 */
private fun sourceOrigin(params: Map<String, Any?>): String {
    val encoded = params["bytes_b64"]?.toString()
    if (encoded.isNullOrEmpty()) {
        return params.jsonString("origin")
    }
    val file = Files.createTempFile("lmeditor-source-", ".bin")
    file.toFile().deleteOnExit()
    Files.write(file, Base64.getDecoder().decode(encoded))
    return file.toString()
}

/**
 * The store for the root a request names.
 *
 * The root is a parameter of the store, not a setting of the process: a
 * caller that keeps its uploads in its own directory must get *those*
 * uploads, so the root travels with the request.
 */
private fun sourceStoreFor(params: Map<String, Any?>): SourceStore {
    val root = params["root"]?.toString()
    val path = if (root.isNullOrBlank()) uploadRoot() else Paths.get(root)
    val budget = longOrNull(params, "max_bytes") ?: MAX_SOURCE_STORE_BYTES
    return SourceStore(path, budget)
}

/** Read a long field, or null when the request does not carry one. */
private fun longOrNull(params: Map<String, Any?>, key: String): Long? = when (val value = params[key]) {
    null -> null
    is Number -> value.toLong()
    is String -> value.toLongOrNull()
    else -> null
}

/**
 * The bibliographic metadata a request carries.
 *
 * Only the string entries are kept: the rule that reads them looks for text,
 * so an entry that is not text is not metadata the resolver can use.
 */
private fun metadataFrom(params: Map<String, Any?>): Map<String, String>? {
    val metadata = params.jsonObject("pdf_metadata")
    if (metadata.isEmpty()) {
        return null
    }
    val entries = LinkedHashMap<String, String>()
    for ((key, value) in metadata) {
        if (value is String) {
            entries[key] = value
        }
    }
    return entries
}

/** The resolution request a bibliography call carries. */
private fun resolutionRequestFrom(params: Map<String, Any?>): ResolutionRequest = ResolutionRequest(
    sourceId = params.jsonString("source_id"),
    sourceKind = SourceKind.fromWire(params.jsonString("source_kind")),
    doi = params["doi"]?.toString(),
    arxivId = params["arxiv_id"]?.toString(),
    pmid = params["pmid"]?.toString(),
    pdfMetadata = metadataFrom(params),
)

/**
 * The single resolver a request names.
 *
 * The strategy is named by its wire kind. The user resolver is the one that
 * needs more than a kind: it carries the record the operator typed and the
 * sources that record applies to.
 */
private fun bibliographyResolverFor(params: Map<String, Any?>): BibliographyResolver {
    val kind = BibliographyResolverKind.fromWire(params.jsonString("resolver"))
    val record = params.jsonObject("record").takeIf { it.isNotEmpty() }?.let {
        bibliographicRecordFromWire(it)
    }
    val sourceIds = params.jsonStringList("source_ids").takeIf { it.isNotEmpty() }?.toSet()
    return bibliographyResolverOf(kind, record, sourceIds)
}

/**
 * The resolvers a chain request names.
 *
 * Each entry names its strategy and carries whatever that strategy needs, so
 * a chain that includes the user resolver keeps the record it was given.
 */
private fun resolversFrom(params: Map<String, Any?>): List<BibliographyResolver> =
    params.jsonObjectList("resolvers").map { spec ->
        val kind = BibliographyResolverKind.fromWire(spec.jsonString("kind"))
        val record = spec.jsonObject("record").takeIf { it.isNotEmpty() }?.let {
            bibliographicRecordFromWire(it)
        }
        val sourceIds = spec.jsonStringList("source_ids").takeIf { it.isNotEmpty() }?.toSet()
        bibliographyResolverOf(kind, record, sourceIds)
    }

/** The resolver for one strategy. */
private fun bibliographyResolverOf(
    kind: BibliographyResolverKind,
    record: BibliographicRecord?,
    sourceIds: Set<String>?,
): BibliographyResolver = when (kind) {
    BibliographyResolverKind.PDF_HEADER -> PdfHeaderBibliographyResolver()
    BibliographyResolverKind.CROSSREF -> CrossRefBibliographyResolver()
    BibliographyResolverKind.ARXIV -> ArxivBibliographyResolver()
    BibliographyResolverKind.PUBMED -> PubMedBibliographyResolver()
    BibliographyResolverKind.OPENALEX -> OpenAlexBibliographyResolver()
    BibliographyResolverKind.USER -> UserBibliographyResolver(record, sourceIds)
    BibliographyResolverKind.NONE -> throw ValidationError(
        "Unknown bibliography resolver '${kind.wire}'",
        field = "bibliography_resolver",
        value = kind.wire,
    )
}
