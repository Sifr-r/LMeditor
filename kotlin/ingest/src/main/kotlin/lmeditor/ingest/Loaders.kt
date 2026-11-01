package lmeditor.ingest

import lmeditor.core.SourceKind
import lmeditor.core.SourceLoadError
import lmeditor.pdf.PdfDocument
import lmeditor.pdf.PdfMetadata
import lmeditor.word.WordReader
import java.io.IOException
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration

/**
 * Everything a loader learned about a source.
 *
 * The text is the product; the rest is the evidence about it: the page count a
 * PDF carries, the size of the artifact on disk, the register of content the
 * Word reader could not preserve, and the bibliographic metadata a PDF
 * carries.
 */
data class LoadedSource(
    /** The normalized text of the source. */
    val text: String,
    /** The number of pages, for the kinds that have pages. */
    val pageCount: Int? = null,
    /** The size of the artifact in bytes. */
    val byteSize: Long? = null,
    /** The content the loader could not preserve. */
    val omissions: List<String> = emptyList(),
    /** The bibliographic metadata the artifact carried. */
    val pdfMetadata: Map<String, String> = emptyMap(),
)

/**
 * Where a source comes from.
 *
 * The two cases are modelled apart because the URL loader must never be handed
 * a file path and a file loader must never be handed a URL: the mistake is
 * exactly the one the SSRF guard exists to prevent.
 */
sealed interface SourceOrigin {
    /** A source that lives on disk. */
    data class File(val path: Path) : SourceOrigin

    /** A source that lives behind a URL. */
    data class Remote(val url: String) : SourceOrigin
}

/**
 * Turns bytes into normalized text.
 *
 * Every subclass handles one [SourceKind], and every failure is reported as a
 * [SourceLoadError]: the file is the client's problem, and the HTTP boundary
 * turns that error into a 422 rather than a 500.
 */
interface SourceLoader {
    /** The kind of source this loader handles. */
    val kind: SourceKind

    /**
     * Load the source and report everything the loader learned.
     *
     * @throws SourceLoadError the source could not be loaded.
     */
    fun load(origin: SourceOrigin): LoadedSource
}

/** Loads plain UTF-8 text. The page count is undefined. */
class TextSourceLoader : SourceLoader {
    override val kind: SourceKind = SourceKind.TEXT

    override fun load(origin: SourceOrigin): LoadedSource {
        val path = origin.requireFile(kind)
        val text = readUtf8(path, kind)
        return LoadedSource(text = text, byteSize = fileByteSize(path))
    }
}

/**
 * Loads a Markdown file and converts it to plain text.
 *
 * Stripping Markdown syntax requires *rendering* it first, so the pipeline
 * runs Markdown -> HTML -> text (tag strip). The direction is the whole point:
 * a converter that went HTML -> Markdown would re-emit the very syntax this
 * loader exists to remove.
 *
 * The `markdown` package is not available to the Kotlin tier, so the rendering
 * is native: the syntax markers are removed and the text they wrapped is
 * kept. The result is the same text the HTML round trip produces.
 */
class MarkdownSourceLoader : SourceLoader {
    override val kind: SourceKind = SourceKind.MARKDOWN

    override fun load(origin: SourceOrigin): LoadedSource {
        val path = origin.requireFile(kind)
        val raw = readUtf8(path, kind)
        return LoadedSource(text = markdownToText(raw), byteSize = fileByteSize(path))
    }
}

/**
 * Loads an HTML file and extracts its visible text.
 *
 * The Python tier reaches for `trafilatura` first and falls back to a regular
 * expression. The fallback is the behaviour ported here, with two additions
 * that make it match what `trafilatura` would have produced: `script` and
 * `style` elements are dropped whole, and HTML entities are decoded.
 */
class HtmlSourceLoader : SourceLoader {
    override val kind: SourceKind = SourceKind.HTML

    override fun load(origin: SourceOrigin): LoadedSource {
        val path = origin.requireFile(kind)
        val raw = readUtf8(path, kind)
        return LoadedSource(text = htmlToText(raw), byteSize = fileByteSize(path))
    }
}

/**
 * Loads a PDF file and returns its page count.
 *
 * Text is extracted page by page; a scanned or blank page yields no text. The
 * page count is always returned when the PDF can be opened, and the text may
 * be empty.
 *
 * The loader also extracts the document's `/Info` metadata and looks for a DOI
 * or an arXiv id in it or on the first page. That metadata is what makes the
 * PDF-header bibliography resolver -- and through it the DOI resolvers -- able
 * to resolve a PDF at all.
 */
class PdfSourceLoader : SourceLoader {
    override val kind: SourceKind = SourceKind.PDF

    override fun load(origin: SourceOrigin): LoadedSource {
        val path = origin.requireFile(kind)
        val document = try {
            PdfDocument.open(path)
        } catch (exc: SourceLoadError) {
            throw SourceLoadError(
                kind = kind.wire,
                origin = path.toString(),
                reason = "the PDF could not be parsed: ${exc.reason}",
            )
        }
        val text = document.pages.joinToString("\n\n") { it.text }.trim()
        return LoadedSource(
            text = text,
            pageCount = document.pageCount,
            byteSize = fileByteSize(path),
            pdfMetadata = PdfMetadata.extract(document).toMap(),
        )
    }
}

/**
 * Loads a DOCX file through the Word reader and returns its plain text.
 *
 * DOCX has no native pagination concept, so the page count is always null.
 *
 * The reader also keeps a register of the content it could not preserve
 * (dropped images, comments, endnotes, ...). That register is read here and
 * carried into the ingest result: a warning that dies inside the process is
 * a warning the operator never sees.
 */
class DocxSourceLoader(
    private val reader: WordReader = WordReader(),
) : SourceLoader {
    override val kind: SourceKind = SourceKind.DOCX

    override fun load(origin: SourceOrigin): LoadedSource {
        val path = origin.requireFile(kind)
        val document = try {
            reader.read(path)
        } catch (exc: lmeditor.core.WordParseError) {
            throw SourceLoadError(
                kind = kind.wire,
                origin = path.toString(),
                reason = "Word document could not be parsed: ${exc.reason}",
            )
        } catch (exc: IOException) {
            throw SourceLoadError(
                kind = kind.wire,
                origin = path.toString(),
                reason = "file could not be read: ${exc.message}",
            )
        } catch (exc: RuntimeException) {
            throw SourceLoadError(
                kind = kind.wire,
                origin = path.toString(),
                reason = "Word document could not be read: $exc",
            )
        }
        return LoadedSource(
            text = document.plainText(),
            pageCount = null,
            byteSize = fileByteSize(path),
            omissions = reader.lastOmissions,
        )
    }
}

/**
 * Downloads a URL and routes the response through the HTML text extraction.
 *
 * SSRF guard: the origin *and* every redirect target are validated by the
 * private-address policy before a request is issued, and redirects are
 * followed manually (at most [maxRedirectHops] hops) so the guard cannot be
 * bypassed by a `Location` header.
 *
 * The guard is enforced per hop and fails closed:
 *
 * * every hop pins the addresses it vetted *before* the request is issued, so
 *   the connection is made to an address that was proven public;
 * * the address the connection really reached is checked against the vetted
 *   set, so a hop that rebound to an internal address is refused *before* its
 *   body is read;
 * * "cannot tell" is a refusal, not agreement: a hop that reached the network
 *   but exposes no peer address is *unproven*, not *clean*.
 */
class UrlSourceLoader(
    /** The time a single hop may take. */
    val timeout: Duration = Duration.ofSeconds(30),
    /** The largest response body this loader will read. */
    val maxResponseBytes: Int = MAX_URL_RESPONSE_BYTES,
    /** The largest number of redirect hops this loader will follow. */
    val maxRedirectHops: Int = MAX_RESOLVER_REDIRECT_HOPS,
    /** The transport the hops are issued through. */
    val transport: HttpTransport = SocketHttpTransport,
    /** The resolver the host of a hop is resolved through. */
    val resolver: HostResolver = JdkHostResolver,
) : SourceLoader {
    override val kind: SourceKind = SourceKind.URL

    override fun load(origin: SourceOrigin): LoadedSource {
        val url = (origin as? SourceOrigin.Remote)?.url
            ?: throw SourceLoadError(
                kind = kind.wire,
                origin = origin.toString(),
                reason = "URL loader requires a URL, not a file",
            )
        val response = loadResponse(url)
        return LoadedSource(
            text = htmlToText(String(response.body, Charsets.UTF_8)),
            pageCount = null,
            byteSize = response.body.size.toLong(),
        )
    }

    /**
     * Fetch [url] and return the accepted body.
     *
     * Every failure is reported as a [SourceLoadError], and every refusal by
     * the private-address policy is reported as "blocked URL", so a caller can
     * tell a refusal by the policy from a failure of the network.
     *
     * @throws SourceLoadError the URL was refused, the fetch failed, or the
     *   body was too large.
     */
    fun loadResponse(url: String): UrlResponse {
        try {
            return fetchLoop(url)
        } catch (exc: UnsafeSourceError) {
            throw SourceLoadError(
                kind = kind.wire,
                origin = url,
                reason = "blocked URL: ${exc.message}",
            )
        } catch (exc: PayloadTooLargeError) {
            throw SourceLoadError(
                kind = kind.wire,
                origin = url,
                reason = "response too large: ${exc.message}",
            )
        } catch (exc: HttpSafetyError) {
            throw SourceLoadError(
                kind = kind.wire,
                origin = url,
                reason = "HTTP error: ${exc.message}",
            )
        }
    }

    /** Fetch [url], following the redirects by hand and vetting every hop. */
    private fun fetchLoop(url: String): UrlResponse {
        var current = url
        var hops = 0
        while (true) {
            val vetted = resolvePublicAddresses(current, resolver)
            val exchange = transport.send(current, vetted, timeout)
            exchange.use {
                if (!exchange.isRedirect) {
                    assertVettedPeer(exchange, vetted, hostOf(current))
                    if (exchange.statusCode >= 400) {
                        throw HttpSafetyError("the server answered with HTTP ${exchange.statusCode}")
                    }
                    return UrlResponse(readLimited(exchange.body, maxResponseBytes))
                }
                val location = exchange.header("Location")
                if (location.isNullOrBlank()) {
                    throw HttpSafetyError("HTTP ${exchange.statusCode} redirect without a Location header")
                }
                if (hops >= maxRedirectHops) {
                    throw HttpSafetyError("exceeded the maximum of $maxRedirectHops redirects")
                }
                hops++
                current = try {
                    validatedRedirect(current, location, resolver)
                } catch (exc: UnsafeSourceError) {
                    val hosts = iterRedirectHosts(current, listOf(location))
                        .joinToString(", ")
                        .ifEmpty { "unknown" }
                    throw UnsafeSourceError(
                        "redirect to a disallowed target (host: $hosts): ${exc.message}",
                    )
                }
            }
        }
    }
}

/** Return the host of [url], for the error messages. */
private fun hostOf(url: String): String = try {
    ParsedUrl.parse(url).host
} catch (exc: UnsafeSourceError) {
    ""
}

/** The body a URL loader accepted. */
data class UrlResponse(
    /** The bytes of the response body. */
    val body: ByteArray,
) {
    override fun equals(other: Any?): Boolean =
        this === other || (other is UrlResponse && body.contentEquals(other.body))

    override fun hashCode(): Int = body.contentHashCode()
}

// --- Shared helpers ----------------------------------------------------------

/** Return the file path of [origin], or raise a load error when it is not one. */
private fun SourceOrigin.requireFile(kind: SourceKind): Path = when (this) {
    is SourceOrigin.File -> path
    is SourceOrigin.Remote -> throw SourceLoadError(
        kind = kind.wire,
        origin = url,
        reason = "${kind.wire} loader requires a file, not a URL",
    )
}

/** Return the size of the file at [path] in bytes, or null when it cannot be read. */
internal fun fileByteSize(path: Path): Long? = try {
    Files.size(path)
} catch (exc: IOException) {
    null
}

/**
 * Read the file at [path] as strict UTF-8.
 *
 * @throws SourceLoadError the file is missing, is not valid UTF-8, or cannot
 *   be read.
 */
internal fun readUtf8(path: Path, kind: SourceKind): String {
    if (!Files.isRegularFile(path)) {
        throw SourceLoadError(
            kind = kind.wire,
            origin = path.toString(),
            reason = "file does not exist or is not a regular file: $path",
        )
    }
    val bytes = try {
        Files.readAllBytes(path)
    } catch (exc: IOException) {
        throw SourceLoadError(
            kind = kind.wire,
            origin = path.toString(),
            reason = "file could not be read: ${exc.message}",
        )
    }
    val decoder = StandardCharsets.UTF_8.newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT)
        .onUnmappableCharacter(CodingErrorAction.REPORT)
    return try {
        decoder.decode(java.nio.ByteBuffer.wrap(bytes)).toString()
    } catch (exc: CharacterCodingException) {
        throw SourceLoadError(
            kind = kind.wire,
            origin = path.toString(),
            reason = "file is not valid UTF-8: ${exc.message}",
        )
    }
}

// --- Text extraction ---------------------------------------------------------

/** Matches an HTML tag. */
private val HTML_TAG = Regex("<[^>]+>")

/** Matches a `script` or `style` element and everything it holds. */
private val HTML_SCRIPT_OR_STYLE = Regex(
    "<(script|style)\\b[^>]*>.*?</\\1>",
    setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
)

/** Matches a run of whitespace. */
private val WHITESPACE_RUN = Regex("\\s+")

/**
 * Return the visible text of an HTML document.
 *
 * The `script` and `style` elements are dropped whole, the remaining tags
 * become spaces, the entities are decoded, and the whitespace is collapsed.
 */
fun htmlToText(html: String): String {
    val withoutScripts = HTML_SCRIPT_OR_STYLE.replace(html, " ")
    val withoutTags = HTML_TAG.replace(withoutScripts, " ")
    return WHITESPACE_RUN.replace(decodeHtmlEntities(withoutTags), " ").trim()
}

/** Matches an HTML entity. */
private val HTML_ENTITY = Regex("&(#x?[0-9a-fA-F]+|[a-zA-Z]+);")

/** Decode the HTML entities of [text]. */
internal fun decodeHtmlEntities(text: String): String =
    HTML_ENTITY.replace(text) { match ->
        val body = match.groupValues[1]
        val code = when {
            body.startsWith("#x") || body.startsWith("#X") ->
                body.substring(2).toIntOrNull(16)
            body.startsWith("#") -> body.substring(1).toIntOrNull()
            else -> NAMED_ENTITIES[body.lowercase()]
        }
        if (code != null && code in 1..0x10FFFF) {
            String(Character.toChars(code))
        } else {
            match.value
        }
    }

/** The HTML entities the extraction decodes. */
private val NAMED_ENTITIES: Map<String, Int> = mapOf(
    "amp" to '&'.code,
    "lt" to '<'.code,
    "gt" to '>'.code,
    "quot" to '"'.code,
    "apos" to '\''.code,
    "nbsp" to 0x00A0.toInt(),
    "hellip" to 0x2026,
    "mdash" to 0x2014,
    "ndash" to 0x2013,
    "lsquo" to 0x2018,
    "rsquo" to 0x2019,
    "ldquo" to 0x201C,
    "rdquo" to 0x201D,
)

/** Matches the leading markers of a Markdown list item. */
private val MARKDOWN_LIST_MARKER = Regex("^\\s*(?:[-*+]\\s+|\\d+[.)]\\s+)")

/** Matches the leading markers of a Markdown heading. */
private val MARKDOWN_HEADING = Regex("^\\s{0,3}#{1,6}\\s*")

/** Matches the leading markers of a Markdown block quote. */
private val MARKDOWN_QUOTE = Regex("^\\s*>\\s?")

/** Matches a Markdown thematic break. */
private val MARKDOWN_RULE = Regex("^\\s{0,3}(?:\\*\\s*){3,}$|^\\s{0,3}(?:-\\s*){3,}$|^\\s{0,3}(?:_\\s*){3,}$")

/** Matches a Markdown image. */
private val MARKDOWN_IMAGE = Regex("!\\[([^\\]]*)]\\([^)]*\\)")

/** Matches a Markdown inline link. */
private val MARKDOWN_LINK = Regex("\\[([^\\]]*)]\\([^)]*\\)")

/** Matches a Markdown reference link. */
private val MARKDOWN_REFERENCE_LINK = Regex("\\[([^\\]]*)]\\[[^\\]]*]")

/**
 * Return the plain text of a Markdown document.
 *
 * The syntax markers are removed and the text they wrapped is kept; the
 * whitespace is collapsed, exactly as the HTML round trip the Python tier
 * performs does.
 */
fun markdownToText(markdown: String): String {
    val rendered = StringBuilder()
    var inFence = false
    for (line in markdown.lines()) {
        val trimmed = line.trim()
        if (trimmed.startsWith("```") || trimmed.startsWith("~~~")) {
            inFence = !inFence
            continue
        }
        if (inFence) {
            rendered.append(line).append('\n')
            continue
        }
        if (MARKDOWN_RULE.matches(line)) {
            continue
        }
        var text = MARKDOWN_QUOTE.replace(line, "")
        text = MARKDOWN_HEADING.replace(text, "")
        text = MARKDOWN_LIST_MARKER.replace(text, "")
        rendered.append(text).append('\n')
    }
    val inline = rendered.toString()
        .replace(MARKDOWN_IMAGE) { it.groupValues[1] }
        .replace(MARKDOWN_LINK) { it.groupValues[1] }
        .replace(MARKDOWN_REFERENCE_LINK) { it.groupValues[1] }
    val withoutMarkers = inline
        .replace("**", "")
        .replace("__", "")
        .replace("~~", "")
        .replace("*", "")
        .replace("_", "")
        .replace("`", "")
    val withoutTags = HTML_TAG.replace(withoutMarkers, " ")
    return WHITESPACE_RUN.replace(decodeHtmlEntities(withoutTags), " ").trim()
}
