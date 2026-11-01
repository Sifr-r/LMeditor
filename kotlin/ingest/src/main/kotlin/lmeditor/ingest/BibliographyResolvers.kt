package lmeditor.ingest

import lmeditor.core.BibliographicRecord
import lmeditor.core.BibliographyResolutionError
import lmeditor.core.BibliographyResolverKind
import lmeditor.core.SourceKind
import lmeditor.core.json.Json
import org.w3c.dom.Element
import org.w3c.dom.Node
import java.io.ByteArrayInputStream
import java.time.Duration
import javax.xml.parsers.DocumentBuilderFactory

/**
 * The bibliography resolver chain (PDF header -> CrossRef -> arXiv -> PubMed ->
 * OpenAlex).
 *
 * Each resolver is written against the structure its upstream *actually*
 * serves rather than a convenient synthetic map:
 *
 * * CrossRef, PubMed and OpenAlex answer JSON, decoded once by the shared
 *   [HttpBibliographyResolver] and interpreted by a per-resolver parser.
 * * arXiv answers **Atom XML** from `export.arxiv.org/api/query`, so it opts
 *   into the XML path and parses the document with `javax.xml`.
 *
 * An incomplete or malformed upstream document is an expected outcome rather
 * than an exception: the parsers return null for anything without a non-empty
 * title, and [BibliographyResolutionError] stays reserved for a failed fetch,
 * so [BibliographyResolverChain] can move on to the next resolver.
 */
data class ResolutionRequest(
    /** The source the record is about. */
    val sourceId: String,
    /** The kind of the source. */
    val sourceKind: SourceKind,
    /** The DOI the source declares, when it declares one. */
    val doi: String? = null,
    /** The arXiv id the source declares, when it declares one. */
    val arxivId: String? = null,
    /** The PMID the source declares, when it declares one. */
    val pmid: String? = null,
    /** The `/Info` metadata of the source, when it carries any. */
    val pdfMetadata: Map<String, String>? = null,
)

/**
 * A single-strategy resolver.
 *
 * Returns null when the strategy cannot resolve the source.
 */
interface BibliographyResolver {
    /** The kind of resolver this is. */
    val kind: BibliographyResolverKind

    /**
     * Resolve the record for [request].
     *
     * @throws BibliographyResolutionError the strategy could not be carried
     *   out. The chain treats that as "this strategy cannot resolve" and moves
     *   on to the next one.
     */
    fun resolve(request: ResolutionRequest): BibliographicRecord?
}

/**
 * The base for the resolvers that talk to an upstream over HTTP.
 *
 * The flag [expectsAtom] decides which body the base class decodes: JSON for
 * CrossRef / PubMed / OpenAlex, Atom XML for arXiv. It is a flag rather than
 * per-resolver guesswork so the JSON resolvers cannot be dragged into the XML
 * path by accident.
 */
abstract class HttpBibliographyResolver(
    /** The transport the request is issued through. */
    val transport: HttpTransport = JdkHttpClientTransport(),
    /** The resolver the host of the request is resolved through. */
    val hostResolver: HostResolver = JdkHostResolver,
    /** The time the request may take. */
    val timeout: Duration = Duration.ofSeconds(10),
    /** The largest response body this resolver will read. */
    val maxResponseBytes: Int = MAX_RESOLVER_RESPONSE_BYTES,
) : BibliographyResolver {
    /** arXiv is the only upstream served over HTTP that answers with Atom XML. */
    open val expectsAtom: Boolean = false

    /**
     * Return the request URL for this resolver, or null to decline.
     *
     * The contract implementations must honour:
     *
     * * null when the identifier this resolver needs is absent -- that is "not
     *   my strategy", and the chain simply moves on.
     * * the matching identifier validation and the [encodeIdentifier] pass
     *   before the value is interpolated, so a well-formed identifier keeps
     *   producing the URL it produced before validation existed while a legacy
     *   SICI DOI's `( ) : ; < >` is percent-encoded rather than rejected.
     */
    abstract fun url(doi: String?, arxivId: String?, pmid: String?): String?

    /** Interpret a JSON payload. Only the JSON resolvers override this. */
    open fun parseJson(payload: Map<String, Any?>, requestedId: String?): BibliographicRecord? =
        throw BibliographyResolutionError(
            resolver = kind.wire,
            origin = "(payload)",
            reason = "${kind.wire} does not parse JSON payloads",
        )

    /** Interpret an Atom document. Only the arXiv resolver overrides this. */
    open fun parseAtom(document: String): BibliographicRecord? =
        throw BibliographyResolutionError(
            resolver = kind.wire,
            origin = "(document)",
            reason = "${kind.wire} does not parse Atom documents",
        )

    final override fun resolve(request: ResolutionRequest): BibliographicRecord? {
        val target = url(request.doi, request.arxivId, request.pmid) ?: return null
        val record = try {
            if (expectsAtom) {
                parseAtom(String(request(target), Charsets.UTF_8))
            } else {
                parseJson(fetchJson(target), request.pmid ?: request.arxivId ?: request.doi)
            }
        } catch (exc: BibliographyResolutionError) {
            throw exc
        } catch (exc: RuntimeException) {
            // A malformed upstream document is an upstream failure, not a bug:
            // report it as the domain error the chain knows how to fall back on.
            throw BibliographyResolutionError(
                resolver = kind.wire,
                origin = target,
                reason = "malformed response: ${exc.message}",
            )
        }
        return record?.withResolver(kind)
    }

    /** Perform the HTTP call and return the raw body. */
    private fun request(url: String): ByteArray = try {
        fetchLimited(
            url = url,
            maxBytes = maxResponseBytes,
            timeout = timeout,
            transport = transport,
            resolver = hostResolver,
        )
    } catch (exc: UnsafeSourceError) {
        throw BibliographyResolutionError(
            resolver = kind.wire,
            origin = url,
            reason = "blocked URL: ${exc.message}",
        )
    } catch (exc: PayloadTooLargeError) {
        throw BibliographyResolutionError(
            resolver = kind.wire,
            origin = url,
            reason = exc.message ?: "the response was too large",
        )
    } catch (exc: HttpSafetyError) {
        throw BibliographyResolutionError(
            resolver = kind.wire,
            origin = url,
            reason = exc.message ?: "the request failed",
        )
    }

    /** Return the decoded JSON object served at [url]. */
    private fun fetchJson(url: String): Map<String, Any?> {
        val body = String(request(url), Charsets.UTF_8)
        val payload = try {
            Json.parse(body)
        } catch (exc: RuntimeException) {
            throw BibliographyResolutionError(
                resolver = kind.wire,
                origin = url,
                reason = "response is not valid JSON: ${exc.message}",
            )
        }
        @Suppress("UNCHECKED_CAST")
        return payload as? Map<String, Any?>
            ?: throw BibliographyResolutionError(
                resolver = kind.wire,
                origin = url,
                reason = "expected a JSON object, got ${payload?.javaClass?.simpleName}",
            )
    }
}

/**
 * Best-effort: extract the title and the author from a PDF's metadata.
 *
 * This resolver is the *offline* one: it never makes a request, it reads the
 * metadata the PDF itself carries.
 *
 * The DOI the metadata carries is put on the record, because the document
 * states it and the record should not hide it. The chain, however, tries the
 * identifier-bearing resolvers *before* this one whenever a DOI or an arXiv id
 * is known, so a PDF with a DOI is resolved by CrossRef (which returns a year, a
 * container and a canonical URL) and this resolver is the fallback.
 */
class PdfHeaderBibliographyResolver : BibliographyResolver {
    override val kind: BibliographyResolverKind = BibliographyResolverKind.PDF_HEADER

    override fun resolve(request: ResolutionRequest): BibliographicRecord? {
        val metadata = request.pdfMetadata
        if (metadata.isNullOrEmpty()) {
            return null
        }
        val title = metadataValue(metadata, "title") ?: return null
        val authorRaw = metadataValue(metadata, "author")
        val authors = if (authorRaw.isNullOrEmpty()) {
            emptyList()
        } else {
            authorRaw.replace(';', ',').split(',').map { it.trim() }.filter { it.isNotEmpty() }
        }
        return BibliographicRecord(
            title = title,
            authors = authors,
            doi = request.doi ?: metadataDoi(metadata),
            resolver = kind,
        )
    }
}

/** Resolves a DOI against the CrossRef `/works/{doi}` endpoint. */
class CrossRefBibliographyResolver(
    transport: HttpTransport = JdkHttpClientTransport(),
    hostResolver: HostResolver = JdkHostResolver,
    timeout: Duration = Duration.ofSeconds(10),
    maxResponseBytes: Int = MAX_RESOLVER_RESPONSE_BYTES,
) : HttpBibliographyResolver(transport, hostResolver, timeout, maxResponseBytes) {
    override val kind: BibliographyResolverKind = BibliographyResolverKind.CROSSREF

    override fun url(doi: String?, arxivId: String?, pmid: String?): String? {
        if (doi.isNullOrEmpty()) {
            return null
        }
        val validated = validatedDoi(kind, doi)
        return "https://api.crossref.org/works/${encodeIdentifier(validated)}"
    }

    override fun parseJson(payload: Map<String, Any?>, requestedId: String?): BibliographicRecord? {
        val message = payload["message"] as? Map<String, Any?> ?: emptyMap()
        val titles = message["title"] as? List<*> ?: emptyList<Any?>()
        if (titles.isEmpty()) {
            return null
        }
        val rawAuthors = message["author"] as? List<*> ?: emptyList<Any?>()
        val authors = rawAuthors.mapNotNull { entry ->
            val author = entry as? Map<String, Any?> ?: return@mapNotNull null
            val given = author["given"]?.toString().orEmpty()
            val family = author["family"]?.toString().orEmpty()
            "$given $family".trim().takeIf { it.isNotEmpty() }
        }
        val issued = (message["issued"] as? Map<String, Any?>)?.get("date-parts") as? List<*>
        val year = (issued?.firstOrNull() as? List<*>)?.firstOrNull()?.toString()?.toIntOrNull()
        val containers = message["container-title"] as? List<*>
        return BibliographicRecord(
            title = titles[0].toString(),
            authors = authors,
            year = year,
            containerTitle = containers?.firstOrNull()?.toString(),
            doi = message["DOI"]?.toString(),
            url = message["URL"]?.toString(),
            type = message["type"]?.toString(),
            resolver = kind,
        )
    }
}

/**
 * Resolves an arXiv id against the Atom feed at `export.arxiv.org`.
 *
 * This is the one resolver whose upstream does not speak JSON: the query API
 * answers `application/atom+xml`, which is why it sets [expectsAtom] and
 * implements [parseAtom] instead of [parseJson].
 */
class ArxivBibliographyResolver(
    transport: HttpTransport = JdkHttpClientTransport(),
    hostResolver: HostResolver = JdkHostResolver,
    timeout: Duration = Duration.ofSeconds(10),
    maxResponseBytes: Int = MAX_RESOLVER_RESPONSE_BYTES,
) : HttpBibliographyResolver(transport, hostResolver, timeout, maxResponseBytes) {
    override val kind: BibliographyResolverKind = BibliographyResolverKind.ARXIV
    override val expectsAtom: Boolean = true

    override fun url(doi: String?, arxivId: String?, pmid: String?): String? {
        if (arxivId.isNullOrEmpty()) {
            return null
        }
        val validated = validatedArxivId(kind, arxivId)
        return "http://export.arxiv.org/api/query?id_list=${encodeIdentifier(validated)}"
    }

    override fun parseAtom(document: String): BibliographicRecord? {
        val root = parseXml(document)
        val entry = firstEntry(root) ?: return null
        val title = firstText(entry, "title")
        if (title.isEmpty()) {
            // Validate before constructing: BibliographicRecord requires a
            // non-empty title, so a titleless entry resolves to nothing.
            return null
        }
        val authors = childrenNamed(entry, "author")
            .mapNotNull { firstText(it, "name").takeIf { name -> name.isNotEmpty() } }
        val published = firstText(entry, "published")
        val identifier = firstText(entry, "id")
        return BibliographicRecord(
            title = title,
            authors = authors,
            year = leadingYear(published),
            publicationDate = published.ifEmpty { null },
            url = identifier.ifEmpty { null },
            type = "preprint",
            resolver = kind,
        )
    }

    /** Return the feed's first entry, tolerating a missing namespace. */
    private fun firstEntry(root: Element): Element? {
        val namespaced = root.getElementsByTagNameNS(ATOM_NAMESPACE, "entry")
        if (namespaced.length > 0) {
            return namespaced.item(0) as? Element
        }
        val elements = root.getElementsByTagName("*")
        for (index in 0 until elements.length) {
            val element = elements.item(index) as? Element ?: continue
            if (localName(element.tagName) == "entry") {
                return element
            }
        }
        return null
    }
}

/** Resolves a PMID against the PubMed esummary endpoint. */
class PubMedBibliographyResolver(
    transport: HttpTransport = JdkHttpClientTransport(),
    hostResolver: HostResolver = JdkHostResolver,
    timeout: Duration = Duration.ofSeconds(10),
    maxResponseBytes: Int = MAX_RESOLVER_RESPONSE_BYTES,
) : HttpBibliographyResolver(transport, hostResolver, timeout, maxResponseBytes) {
    override val kind: BibliographyResolverKind = BibliographyResolverKind.PUBMED

    override fun url(doi: String?, arxivId: String?, pmid: String?): String? {
        if (pmid.isNullOrEmpty()) {
            return null
        }
        val validated = validatedPmid(kind, pmid)
        return "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi" +
            "?db=pubmed&id=${encodeIdentifier(validated)}&retmode=json"
    }

    override fun parseJson(payload: Map<String, Any?>, requestedId: String?): BibliographicRecord? {
        val result = payload["result"] as? Map<String, Any?> ?: return null
        val uid = selectUid(result, requestedId) ?: return null
        val entry = result[uid] as? Map<String, Any?> ?: return null
        val rawTitle = entry["title"] as? String ?: return null
        val title = rawTitle.trim()
        if (title.isEmpty()) {
            return null
        }
        val authors = (entry["authors"] as? List<*>).orEmpty().mapNotNull { author ->
            val name = (author as? Map<String, Any?>)?.get("name") as? String
            name?.trim()?.takeIf { it.isNotEmpty() }
        }
        val pubdate = entry["pubdate"] as? String ?: ""
        val source = entry["source"] as? String
        return BibliographicRecord(
            title = title,
            authors = authors,
            year = leadingYear(pubdate),
            publicationDate = pubdate.ifEmpty { null },
            containerTitle = source,
            url = "https://pubmed.ncbi.nlm.nih.gov/$uid/",
            type = "journal-article",
            resolver = kind,
        )
    }

    /** Return the uid to read: the requested one when listed, else the first. */
    private fun selectUid(result: Map<String, Any?>, requestedId: String?): String? {
        val uids = result["uids"] as? List<*> ?: emptyList<Any?>()
        val candidates = uids.filterIsInstance<String>().filter { it.isNotEmpty() }
        if (requestedId != null && requestedId in candidates) {
            return requestedId
        }
        if (candidates.isNotEmpty()) {
            return candidates[0]
        }
        if (requestedId != null) {
            // Some esummary responses omit ``uids`` and key the article
            // directly; the requested id is then the only safe lookup.
            return if (requestedId in result) requestedId else null
        }
        return null
    }
}

/** Resolves a DOI against the OpenAlex `/works/doi:{doi}` endpoint. */
class OpenAlexBibliographyResolver(
    transport: HttpTransport = JdkHttpClientTransport(),
    hostResolver: HostResolver = JdkHostResolver,
    timeout: Duration = Duration.ofSeconds(10),
    maxResponseBytes: Int = MAX_RESOLVER_RESPONSE_BYTES,
) : HttpBibliographyResolver(transport, hostResolver, timeout, maxResponseBytes) {
    override val kind: BibliographyResolverKind = BibliographyResolverKind.OPENALEX

    override fun url(doi: String?, arxivId: String?, pmid: String?): String? {
        if (doi.isNullOrEmpty()) {
            return null
        }
        val validated = validatedDoi(kind, doi)
        return "https://api.openalex.org/works/doi:${encodeIdentifier(validated)}"
    }

    override fun parseJson(payload: Map<String, Any?>, requestedId: String?): BibliographicRecord? {
        val title = (payload["title"] ?: payload["display_name"])?.toString()
        if (title.isNullOrEmpty()) {
            return null
        }
        val authorships = payload["authorships"] as? List<*> ?: emptyList<Any?>()
        val authors = authorships.mapNotNull { entry ->
            val authorship = entry as? Map<String, Any?> ?: return@mapNotNull null
            val author = authorship["author"] as? Map<String, Any?> ?: return@mapNotNull null
            author["display_name"]?.toString()?.takeIf { it.isNotEmpty() }
        }
        val year = (payload["publication_year"] as? Number)?.toInt()
        val location = payload["primary_location"] as? Map<String, Any?>
        val source = location?.get("source") as? Map<String, Any?>
        return BibliographicRecord(
            title = title,
            authors = authors,
            year = year,
            containerTitle = source?.get("display_name")?.toString(),
            doi = payload["doi"]?.toString(),
            url = payload["id"]?.toString(),
            type = payload["type"]?.toString(),
            resolver = kind,
        )
    }
}

/**
 * Returns a record the operator supplied by hand.
 *
 * Some sources have no DOI, no arXiv id and no metadata: a working paper that
 * never got a DOI, a report, a slide deck. The operator is the only source of
 * a bibliographic record for those, so this resolver carries the record they
 * typed.
 *
 * It is deliberately **not** part of [BibliographyResolverChain.default]: the
 * chain resolves what it can from the document, and a hand-entered record is an
 * assertion about a document that no upstream can confirm.
 */
class UserBibliographyResolver(
    private val record: BibliographicRecord? = null,
    /** The sources the record applies to; null means every source. */
    private val sourceIds: Set<String>? = null,
) : BibliographyResolver {
    override val kind: BibliographyResolverKind = BibliographyResolverKind.USER

    override fun resolve(request: ResolutionRequest): BibliographicRecord? {
        val supplied = record ?: return null
        if (sourceIds != null && request.sourceId !in sourceIds) {
            return null
        }
        return supplied.withResolver(kind)
    }
}

/**
 * Return the record with its resolver replaced.
 *
 * The record is rebuilt rather than copied because the model's own `copy()`
 * exposes a constructor the model keeps to itself.
 */
private fun BibliographicRecord.withResolver(kind: BibliographyResolverKind): BibliographicRecord =
    BibliographicRecord(
        title = title,
        authors = authors,
        year = year,
        publicationDate = publicationDate,
        containerTitle = containerTitle,
        volume = volume,
        issue = issue,
        pages = pages,
        doi = doi,
        url = url,
        type = type,
        resolver = kind,
    )

/**
 * Runs the resolvers in order and returns the first result, or null.
 *
 * The order is the one the resolvers were declared in, with one exception:
 * when an identifier (a DOI, an arXiv id or a PMID) is known, the resolver
 * that can *verify* it is tried before the PDF-header resolver, which can only
 * report what the document says about itself. A PDF whose metadata carries a
 * DOI is therefore resolved by CrossRef -- with a year, a container and a
 * canonical URL -- and falls back to the header resolver only when no upstream
 * answers.
 */
class BibliographyResolverChain(
    private val resolvers: List<BibliographyResolver>,
) {
    companion object {
        /** Return the chain the ingestion service uses by default. */
        fun default(): BibliographyResolverChain = BibliographyResolverChain(
            listOf(
                PdfHeaderBibliographyResolver(),
                CrossRefBibliographyResolver(),
                ArxivBibliographyResolver(),
                PubMedBibliographyResolver(),
                OpenAlexBibliographyResolver(),
            ),
        )
    }

    /**
     * Resolve the record for [request].
     *
     * A resolver that fails is skipped: the chain moves on to the next one.
     */
    fun resolve(request: ResolutionRequest): BibliographicRecord? {
        val enriched = enrichWithMetadataIdentifiers(request)
        for (resolver in ordered(enriched)) {
            val record = try {
                resolver.resolve(enriched)
            } catch (exc: BibliographyResolutionError) {
                // Move to the next resolver on failure.
                continue
            }
            if (record != null) {
                return record
            }
        }
        return null
    }

    /** Return the resolvers in the order they should be tried. */
    private fun ordered(request: ResolutionRequest): List<BibliographyResolver> {
        if (!hasIdentifier(request)) {
            return resolvers
        }
        val headers = resolvers.filter { it.kind == BibliographyResolverKind.PDF_HEADER }
        val others = resolvers.filter { it.kind != BibliographyResolverKind.PDF_HEADER }
        return others + headers
    }
}

// --- Request enrichment ------------------------------------------------------

/** Return true when a DOI, an arXiv id or a PMID is known. */
internal fun hasIdentifier(request: ResolutionRequest): Boolean =
    !request.doi.isNullOrEmpty() || !request.arxivId.isNullOrEmpty() || !request.pmid.isNullOrEmpty()

/**
 * Return the request with any identifier the PDF metadata carries.
 *
 * A PDF's `/Info` dictionary frequently states the paper's own DOI. The chain
 * turns that into a real identifier so the upstream that can verify it is
 * consulted -- which is what makes the PDF header resolver reachable *and*
 * useful rather than the only thing that ever answers.
 */
internal fun enrichWithMetadataIdentifiers(request: ResolutionRequest): ResolutionRequest {
    val metadata = request.pdfMetadata ?: return request
    var enriched = request
    if (enriched.doi.isNullOrEmpty()) {
        metadataDoi(metadata)?.let { enriched = enriched.copy(doi = it) }
    }
    if (enriched.arxivId.isNullOrEmpty()) {
        metadataArxivId(metadata)?.let { enriched = enriched.copy(arxivId = it) }
    }
    return enriched
}

// --- Identifier validation ---------------------------------------------------

/** Printable US-ASCII, the range a DOI is drawn from. */
private const val PRINTABLE_ASCII_START = 0x20
private const val PRINTABLE_ASCII_END = 0x7F

/**
 * The complete reject-list for a DOI: printable ASCII minus exactly these five
 * characters, each of which can change the *structure* of the request.
 */
private val DOI_UNSAFE_CHARS: Set<Char> = setOf(' ', '?', '#', '%', '\\')

/** A DOI: all printable ASCII except the structurally unsafe characters. */
private val DOI_ID_CHARS: Set<Char> = buildSet {
    for (code in PRINTABLE_ASCII_START until PRINTABLE_ASCII_END) {
        add(code.toChar())
    }
    removeAll(DOI_UNSAFE_CHARS)
}

/**
 * An arXiv id keeps a narrow charset: ASCII letters, digits, the three
 * punctuation characters real ids use (`.`, `-`, `_`) and the `/` of the legacy
 * category form.
 */
private val ARXIV_ID_CHARS: Set<Char> =
    ('A'..'Z').toSet() + ('a'..'z').toSet() + ('0'..'9').toSet() + setOf('.', '-', '_', '/')

/** A PMID is a bare decimal number, with no prefix, separator or suffix. */
private val PMID_CHARS: Set<Char> = ('0'..'9').toSet()

/** NCBI assigns PMIDs of at most eight decimal digits. */
private const val MAX_PMID_DIGITS: Int = 8

/** No real DOI, arXiv id or PMID approaches this. */
private const val MAX_IDENTIFIER_LENGTH: Int = 255

/** How many offending characters an error message names before summarising. */
private const val MAX_REPORTED_CHARS: Int = 8

/** How much of an identifier an error message echoes before truncating. */
private const val MAX_ECHOED_IDENTIFIER: Int = 80

/**
 * Return [value] percent-encoded for interpolation into a request URL.
 *
 * The safe set keeps the `/` a real DOI and a legacy arXiv id both carry
 * literal -- encoding it would change the request for every one of them -- and
 * leaves the always-safe set (ASCII letters, digits, `_`, `.`, `-`, `~`)
 * untouched. Every other character the allow-lists admit is emitted as `%XX`.
 */
fun encodeIdentifier(value: String): String {
    val out = StringBuilder(value.length)
    for (byte in value.toByteArray(Charsets.UTF_8)) {
        val char = (byte.toInt() and 0xff).toChar()
        val safe = char in 'A'..'Z' || char in 'a'..'z' || char in '0'..'9' ||
            char == '_' || char == '.' || char == '-' || char == '~' || char == '/'
        if (safe) {
            out.append(char)
        } else {
            out.append('%').append("%02X".format(byte.toInt() and 0xff))
        }
    }
    return out.toString()
}

/**
 * Return a short, escaped rendering of [value] for an error message.
 *
 * The CR / LF and NUL a request-line injection carries are escaped, so the
 * value can be echoed back without planting a line in the error body or the
 * log it reaches.
 */
internal fun renderIdentifier(value: String): String {
    val clipped = if (value.length > MAX_ECHOED_IDENTIFIER) {
        value.take(MAX_ECHOED_IDENTIFIER) + "..."
    } else {
        value
    }
    return "'" + clipped
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace(" ", "\\x00") + "'"
}

/**
 * Reject [field] with the typed domain error the chain falls back on.
 *
 * [origin] carries the offending value rather than a URL, because no request
 * is made: there is no endpoint to blame, only the input.
 */
internal fun rejectIdentifier(
    kind: BibliographyResolverKind,
    field: String,
    value: String,
    reason: String,
): Nothing = throw BibliographyResolutionError(
    resolver = kind.wire,
    origin = "$field=${renderIdentifier(value)}",
    reason = reason,
)

/**
 * Return [value] stripped of surrounding whitespace, or reject it.
 *
 * Surrounding whitespace is stripped because these identifiers are routinely
 * pasted with a trailing space or newline. *Interior* whitespace is rejected
 * by the allow-list, because a space in the middle of a DOI or a PMID is always
 * a paste accident or an attempt to inject -- never a real identifier.
 */
internal fun normaliseIdentifier(
    kind: BibliographyResolverKind,
    field: String,
    value: String,
    allowed: Set<Char>,
): String {
    val candidate = value.trim()
    if (candidate.isEmpty()) {
        rejectIdentifier(kind, field, value, "identifier is empty once stripped")
    }
    if (candidate.length > MAX_IDENTIFIER_LENGTH) {
        rejectIdentifier(
            kind,
            field,
            value,
            "identifier is longer than $MAX_IDENTIFIER_LENGTH characters",
        )
    }
    val illegal = candidate.toSet().filterNot { it in allowed }.sorted()
    if (illegal.isNotEmpty()) {
        val shown = illegal.take(MAX_REPORTED_CHARS).map { "'$it'" }.toMutableList()
        if (illegal.size > MAX_REPORTED_CHARS) {
            shown.add("...+${illegal.size - MAX_REPORTED_CHARS} more")
        }
        rejectIdentifier(
            kind,
            field,
            value,
            "identifier contains characters outside the allowed set: ${shown.joinToString(", ")}",
        )
    }
    return candidate
}

/**
 * Reject a hierarchical identifier with an empty, `.` or `..` segment.
 *
 * `/` is a legal DOI and arXiv character, so the traversal check is on whole
 * segments rather than on a `..` substring: `10.1234/a..b` is a real DOI,
 * while `10.1234/../works/W1` is not.
 */
internal fun rejectUnsafeSegments(
    kind: BibliographyResolverKind,
    field: String,
    value: String,
) {
    val segments = value.split("/")
    if (segments.any { it == "." || it == ".." }) {
        rejectIdentifier(kind, field, value, "identifier contains a '.' or '..' path segment")
    }
    if (segments.any { it.isEmpty() }) {
        rejectIdentifier(kind, field, value, "identifier contains an empty path segment")
    }
}

/**
 * Return [doi] normalised, or raise if it could not be requested safely.
 *
 * Legal: any printable US-ASCII character except the five structurally unsafe
 * ones, at least one `/` separating a non-empty prefix from a non-empty
 * suffix, no empty or `.` / `..` segment, no interior whitespace, and at most
 * 255 characters. The `10.` stem is *not* required: a DOI is routinely pasted
 * as `1000/jrp.2024.001`.
 */
fun validatedDoi(kind: BibliographyResolverKind, doi: String): String {
    val candidate = normaliseIdentifier(kind, "doi", doi, DOI_ID_CHARS)
    if (!candidate.contains('/')) {
        rejectIdentifier(
            kind,
            "doi",
            doi,
            "a DOI must contain the '/' that separates its prefix from its suffix",
        )
    }
    rejectUnsafeSegments(kind, "doi", candidate)
    return candidate
}

/**
 * Return [arxivId] normalised, or raise if it could not be requested safely.
 *
 * Legal: ASCII letters, digits, `.`, `-`, `_` and `/`, with no empty or
 * `.` / `..` segment and no interior whitespace.
 */
fun validatedArxivId(kind: BibliographyResolverKind, arxivId: String): String {
    val candidate = normaliseIdentifier(kind, "arxiv_id", arxivId, ARXIV_ID_CHARS)
    rejectUnsafeSegments(kind, "arxiv_id", candidate)
    return candidate
}

/**
 * Return [pmid] normalised, or raise if it could not be requested safely.
 *
 * Legal: decimal digits only, at most eight of them, with surrounding
 * whitespace stripped.
 */
fun validatedPmid(kind: BibliographyResolverKind, pmid: String): String {
    val candidate = normaliseIdentifier(kind, "pmid", pmid, PMID_CHARS)
    if (candidate.length > MAX_PMID_DIGITS) {
        rejectIdentifier(
            kind,
            "pmid",
            pmid,
            "a PMID is at most $MAX_PMID_DIGITS decimal digits",
        )
    }
    return candidate
}

// --- Metadata helpers --------------------------------------------------------

/** A DOI as it appears inside free text. */
private val DOI_IN_TEXT = Regex("""10\.\d{4,9}/[^\s"'<>\]]+""")

/**
 * Return a metadata entry, accepting the plain and the `/Info` spellings.
 *
 * A PDF's `/Info` dictionary spells its keys `/Title`, `/Author` and so on;
 * the extraction normalises them to `title` and `author`. Both shapes are
 * accepted so a caller that hands the raw `/Info` dictionary to the chain
 * still works.
 */
internal fun metadataValue(metadata: Map<String, String>, key: String): String? {
    val candidates = listOf(
        key,
        "/${key.first().uppercase()}${key.drop(1)}",
        "/${key.replaceFirstChar { it.uppercase() }}",
    )
    for (candidate in candidates) {
        val value = metadata[candidate]
        if (!value.isNullOrBlank()) {
            return value.trim()
        }
    }
    return null
}

/**
 * Return the DOI a PDF's metadata declares, or null.
 *
 * The declaration is either an explicit `doi` entry or a DOI written into one
 * of the free-text entries (`/Subject`, `/Keywords`, ...), which is where a
 * publisher usually puts it.
 */
internal fun metadataDoi(metadata: Map<String, String>): String? {
    val declared = metadataValue(metadata, "doi")
    if (declared != null) {
        return declared
    }
    for (value in metadata.values) {
        val match = DOI_IN_TEXT.find(value) ?: continue
        val cleaned = match.value.trimEnd('.', ',', ';', ':')
        if (cleaned.isNotEmpty()) {
            return cleaned
        }
    }
    return null
}

/** Return the arXiv id a PDF's metadata declares, or null. */
internal fun metadataArxivId(metadata: Map<String, String>): String? {
    val declared = metadataValue(metadata, "arxiv_id")
    if (declared != null) {
        return declared
    }
    for (value in metadata.values) {
        if (!value.contains("arXiv:")) {
            continue
        }
        val tail = value.substringAfter("arXiv:").trim()
        if (tail.isNotEmpty()) {
            return tail.split(Regex("\\s+")).first()
        }
    }
    return null
}

// --- XML helpers -------------------------------------------------------------

/** The namespace arXiv declares on its Atom feed. */
private const val ATOM_NAMESPACE: String = "http://www.w3.org/2005/Atom"

/** Parse an XML document, refusing external entities. */
internal fun parseXml(document: String): Element {
    val factory = DocumentBuilderFactory.newInstance()
    factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
    factory.setFeature("http://xml.org/sax/features/external-general-entities", false)
    factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false)
    factory.isXIncludeAware = false
    factory.isExpandEntityReferences = false
    val builder = factory.newDocumentBuilder()
    return builder.parse(ByteArrayInputStream(document.toByteArray(Charsets.UTF_8))).documentElement
}

/** Return an element tag with its `{namespace}` prefix stripped. */
internal fun localName(tag: String): String = tag.substringAfterLast('}')

/** Return the direct children whose local name is [name]. */
internal fun childrenNamed(element: Element, name: String): List<Element> {
    val result = mutableListOf<Element>()
    val nodes = element.childNodes
    for (index in 0 until nodes.length) {
        val node = nodes.item(index)
        if (node.nodeType == Node.ELEMENT_NODE && localName(node.nodeName) == name) {
            result.add(node as Element)
        }
    }
    return result
}

/** Return the whitespace-collapsed text of the first child named [name]. */
internal fun firstText(element: Element, name: String): String {
    for (child in childrenNamed(element, name)) {
        return child.textContent.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
    }
    return ""
}

/** Return the four-digit year prefix of [value], or null. */
internal fun leadingYear(value: String): Int? {
    val prefix = value.take(4)
    return if (prefix.length == 4 && prefix.all { it.isDigit() }) prefix.toInt() else null
}
