package lmeditor.factcheck

import lmeditor.core.WebEvidenceError
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.URI
import java.net.URISyntaxException
import java.net.UnknownHostException
import java.time.Duration

/**
 * Default timeout for a web request, in seconds.
 */
const val DEFAULT_WEB_TIMEOUT_SECONDS: Double = 8.0

/**
 * Largest web response body that will be read, in bytes.
 *
 * A search result page or a single article is far below this; anything larger
 * is refused rather than buffered.
 */
const val DEFAULT_WEB_MAX_BYTES: Int = 262_144

/**
 * One hit returned by a web search.
 *
 * @property url the address of the page. It is the address the search engine
 *   reported, not a URL this module has opened.
 * @property title the page's title, when the engine reported one.
 * @property snippet the excerpt the engine reported for the page.
 */
data class WebResult(
    val url: String,
    val title: String = "",
    val snippet: String = "",
)

/**
 * The contract every web-search provider satisfies.
 *
 * A provider returns *candidates*. It does not judge them: whether a
 * candidate supports a claim is decided by the NLI evaluator, on the
 * provider's text, in `FactCheckService`.
 *
 * The search itself is the tier's rule: it needs an HTTP client and a parser
 * for markup that is not a contract, and both of those live here. The Python
 * tier asks for the results over the NDJSON seam.
 */
interface WebSearchProvider {

    /**
     * Return at most [limit] candidate pages for [query].
     *
     * @param query the text to search for.
     * @param limit how many results to return.
     */
    fun search(query: String, limit: Int = 5): List<WebResult>
}

/**
 * The provider used when no web access is configured.
 *
 * It consults nothing and returns nothing, so a fact-check that asks for web
 * evidence degrades to a corpus-only check instead of failing.
 */
class NullWebSearchProvider : WebSearchProvider {

    override fun search(query: String, limit: Int): List<WebResult> = emptyList()
}

/**
 * Why a web request did not produce a body.
 *
 * The four kinds are the ones the Python module named, so the message that
 * reaches the caller says the same thing whichever side of the seam built it.
 */
enum class WebFailure {
    /** The address itself was refused by the address policy. */
    ADDRESS_REFUSED,

    /** A redirect target was refused by the address policy. */
    REDIRECT_REFUSED,

    /** The body was larger than the cap the caller set. */
    TOO_LARGE,

    /** The request itself failed. */
    FAILED,
}

/**
 * A web request that did not produce a body.
 *
 * The transport reports its failure this way so that the rule -- the wording
 * of the failure, the refusal, the cap -- stays in one place.
 */
class WebRequestError(
    /** Why the request did not produce a body. */
    val failure: WebFailure,
    message: String,
) : RuntimeException(message)

/**
 * How a web request is carried out.
 *
 * The seam exists because the HTTP client and the address policy live in the
 * module that owns them: the tier's rule says *what* is fetched and *how* the
 * answer is read, and the transport is what carries it out. The transport is
 * responsible for the address policy too -- it must refuse an address that
 * names an internal service before it opens the connection.
 */
fun interface WebFetcher {
    /**
     * Return the body at [url].
     *
     * @throws WebRequestError the request did not produce a body.
     */
    fun fetch(url: String, timeout: Duration, maxBytes: Int): ByteArray
}

/**
 * Search the open web through DuckDuckGo's HTML endpoint.
 *
 * The provider is deliberately dull about the network: the request URL is
 * assembled from a literal host and a percent-encoded query string, the
 * response is read through the shared, redirect-validating fetch, and the
 * markup is parsed defensively.
 *
 * Every failure -- a blocked URL, a transport error, an oversized body, a body
 * that is not text at all -- is reported as a [WebEvidenceError] so the caller
 * can contain it. The provider never raises a bare exception.
 *
 * @property fetcher the transport the request is carried by.
 * @property timeout how long a single request may take.
 * @property maxBytes the largest response body that will be read.
 */
class DuckDuckGoSearchProvider(
    val fetcher: WebFetcher,
    val timeout: Duration = Duration.ofMillis((DEFAULT_WEB_TIMEOUT_SECONDS * 1000).toLong()),
    val maxBytes: Int = DEFAULT_WEB_MAX_BYTES,
) : WebSearchProvider {

    override fun search(query: String, limit: Int): List<WebResult> {
        if (query.isBlank()) {
            throw WebEvidenceError(
                reason = "the search query must be a non-empty string",
                query = query,
            )
        }
        if (limit <= 0) {
            return emptyList()
        }
        val body = fetchWebBody(
            url = WebSearchEndpoints.buildSearchUrl(query),
            query = query,
            fetcher = fetcher,
            timeout = timeout,
            maxBytes = maxBytes,
            refused = "the search URL was refused",
            tooLarge = "the response was too large",
            failed = "the search request failed",
        )
        return WebSearchEndpoints.parseSearchResults(body).take(limit)
    }
}

/**
 * The seam that reads a page whose search snippet is too thin to judge.
 *
 * The read belongs to the tier -- it needs an HTTP client -- and the
 * implementation must apply the address policy before it opens the connection,
 * so a result that names an internal service is refused rather than fetched.
 */
interface WebPageReader {

    /**
     * Return the visible text of the page at [url].
     *
     * @throws WebEvidenceError the address was refused, the request failed, or
     *   the body was too large.
     */
    fun readPageText(url: String): String
}

/**
 * The page reader that opens a page over HTTP.
 *
 * It is the tier's answer to the [WebPageReader] seam: the page is read here,
 * under the same address policy and the same body cap as the search.
 */
class HttpWebPageReader(
    val fetcher: WebFetcher,
    val timeout: Duration = Duration.ofMillis((DEFAULT_WEB_TIMEOUT_SECONDS * 1000).toLong()),
    val maxBytes: Int = DEFAULT_WEB_MAX_BYTES,
) : WebPageReader {

    override fun readPageText(url: String): String =
        fetchPageText(url, fetcher, timeout, maxBytes)
}

/**
 * Return the visible text of the page at [url].
 *
 * The address is vetted against the private-IP policy before it is opened, so a
 * search result that points at an internal service is refused rather than
 * fetched.
 *
 * @throws WebEvidenceError the address was refused, the request failed, or the
 *   body was too large.
 */
fun fetchPageText(
    url: String,
    fetcher: WebFetcher,
    timeout: Duration = Duration.ofMillis((DEFAULT_WEB_TIMEOUT_SECONDS * 1000).toLong()),
    maxBytes: Int = DEFAULT_WEB_MAX_BYTES,
): String {
    if (url.isBlank()) {
        throw WebEvidenceError(reason = "the page URL must be a non-empty string", query = url)
    }
    val body = fetchWebBody(
        url = url,
        query = url,
        fetcher = fetcher,
        timeout = timeout,
        maxBytes = maxBytes,
        refused = "the page URL was refused",
        tooLarge = "the page was too large",
        failed = "the page could not be fetched",
    )
    return extractPageText(body)
}

/**
 * Return the visible text of an HTML document.
 *
 * The tags are replaced by spaces and the whitespace that is left is collapsed
 * to single spaces, so the text of a document reads as one line. The tier has
 * no third-party library to strip the boilerplate a page carries, so this is
 * the fallback the Python module used when `trafilatura` was not installed.
 */
fun extractPageText(body: ByteArray): String {
    val raw = String(body, Charsets.UTF_8)
    return TAG.replace(raw, " ").split(WHITESPACE).filter { it.isNotEmpty() }.joinToString(" ")
}

/**
 * Return the body at [url], or raise a typed web error.
 *
 * The four failures are named the way the Python module named them: the
 * address itself was refused, a redirect target was refused, the body was too
 * large, and the request failed.
 */
private fun fetchWebBody(
    url: String,
    query: String,
    fetcher: WebFetcher,
    timeout: Duration,
    maxBytes: Int,
    refused: String,
    tooLarge: String,
    failed: String,
): ByteArray {
    return try {
        fetcher.fetch(url, timeout, maxBytes)
    } catch (exc: WebRequestError) {
        val reason = when (exc.failure) {
            WebFailure.ADDRESS_REFUSED -> "$refused: ${exc.message}"
            WebFailure.REDIRECT_REFUSED -> "a redirect target was refused: ${exc.message}"
            WebFailure.TOO_LARGE -> "$tooLarge: ${exc.message}"
            WebFailure.FAILED -> "$failed: ${exc.message}"
        }
        throw WebEvidenceError(reason = reason, query = query)
    }
}

/**
 * The trust boundary every fetched address passes through.
 *
 * A URL that resolves to an internal address is a server-side request
 * forgery waiting to happen: the page could name `169.254.169.254` and reach
 * a cloud metadata service, or `127.0.0.1` and reach the machine's own
 * admin port. The policy therefore refuses a URL whose host resolves *only*
 * to loopback, private, link-local, reserved, multicast or unspecified
 * addresses.
 *
 * A URL may legitimately resolve to a mix of public and internal addresses
 * (split-horizon DNS, a CDN with an internal origin), so this guard only
 * rejects an all-internal resolution. A caller that goes on to open a
 * connection must connect to one of the vetted addresses it returns, rather
 * than resolving the name a second time and trusting the second answer.
 *
 * The policy is the JDK's: `java.net.URI` parses the address and
 * `java.net.InetAddress` resolves it. There is no DNS library and no HTTP
 * client involved.
 */
object UrlPolicy {

    /** The schemes a fetched address may use. */
    private val ALLOWED_SCHEMES: Set<String> = setOf("http", "https")

    /**
     * Return [url] unchanged, or refuse it when it must not be fetched.
     *
     * @throws WebEvidenceError the URL is malformed, cannot be resolved, or
     *   resolves only to internal addresses.
     */
    fun assertPublicUrl(url: String): String {
        resolvePublicAddresses(url)
        return url
    }

    /**
     * Return the vetted addresses [url] is allowed to connect to.
     *
     * This is the pinning primitive of the guard: the host is resolved
     * **once**, and only the addresses that are not internal are returned.
     * Returning the vetted addresses -- rather than a bare "is this URL
     * allowed?" answer -- is what lets a caller bind its *connection* to the
     * addresses it actually inspected, instead of trusting the HTTP client to
     * re-resolve the name to the same answer a moment later.
     *
     * @throws WebEvidenceError the URL is malformed, cannot be resolved, or
     *   resolves only to internal addresses.
     */
    fun resolvePublicAddresses(url: String): List<InetSocketAddress> {
        if (url.isBlank()) {
            throw WebEvidenceError(reason = "url must be a non-empty string", query = url)
        }

        val uri = try {
            URI(url.trim())
        } catch (e: URISyntaxException) {
            throw WebEvidenceError(reason = "url is malformed: ${e.message}", query = url)
        }

        val scheme = (uri.scheme ?: "").lowercase()
        if (scheme !in ALLOWED_SCHEMES) {
            throw WebEvidenceError(
                reason = "url scheme '${uri.scheme}' is not allowed (http/https only): '$url'",
                query = url,
            )
        }

        val host = hostOf(uri)
        if (host.isEmpty()) {
            throw WebEvidenceError(reason = "url has no host: '$url'", query = url)
        }

        val port = if (uri.port != -1) uri.port else if (scheme == "https") 443 else 80

        val resolved = try {
            InetAddress.getAllByName(host).map { InetSocketAddress(it, port) }
        } catch (e: UnknownHostException) {
            throw WebEvidenceError(
                reason = "could not resolve host '$host': ${e.message}",
                query = url,
            )
        }

        if (resolved.isEmpty()) {
            throw WebEvidenceError(
                reason = "could not resolve host '$host': no addresses returned",
                query = url,
            )
        }

        val vetted = resolved.filter { !isBlockedAddress(it.address) }
        if (vetted.isEmpty()) {
            throw WebEvidenceError(
                reason = "host '$host' resolves only to internal address(es): " +
                    resolved.joinToString(", ") { it.address.hostAddress },
                query = url,
            )
        }
        return vetted
    }

    /**
     * Whether [address] must not be fetched.
     *
     * The ranges are the ones the Python tier's `ipaddress` module calls
     * private, reserved, loopback, link-local, multicast or unspecified. An
     * address that cannot be parsed at all is one this module will not
     * connect to either.
     */
    fun isBlockedAddress(address: InetAddress): Boolean {
        val bytes = address.address
        if (bytes.size == 4) {
            return inAnyNetwork(bytes, IPV4_BLOCKED)
        }
        if (bytes.size == 16) {
            val mapped = ipv4Mapped(bytes)
            if (mapped != null) {
                return inAnyNetwork(mapped, IPV4_BLOCKED)
            }
            return inAnyNetwork(bytes, IPV6_BLOCKED)
        }
        return true
    }

    /** The host of [uri], without the brackets an IPv6 literal carries. */
    private fun hostOf(uri: URI): String {
        val host = uri.host
        if (host != null) {
            return if (host.startsWith("[") && host.endsWith("]")) {
                host.substring(1, host.length - 1)
            } else {
                host
            }
        }
        val authority = uri.rawAuthority ?: return ""
        val withoutUserInfo = authority.substringAfterLast('@')
        return withoutUserInfo.substringBefore(':')
    }

    /**
     * The IPv4 address an IPv4-mapped IPv6 address carries, or `null` when
     * [bytes] is not IPv4-mapped.
     */
    private fun ipv4Mapped(bytes: ByteArray): ByteArray? {
        for (index in 0..9) {
            if (bytes[index].toInt() != 0) return null
        }
        if ((bytes[10].toInt() and 0xFF) != 0xFF) return null
        if ((bytes[11].toInt() and 0xFF) != 0xFF) return null
        return bytes.copyOfRange(12, 16)
    }

    /** Whether [bytes] falls inside any of [networks]. */
    private fun inAnyNetwork(bytes: ByteArray, networks: List<Network>): Boolean =
        networks.any { inNetwork(bytes, it) }

    /** Whether [bytes] falls inside [network]. */
    private fun inNetwork(bytes: ByteArray, network: Network): Boolean {
        var compared = 0
        while (compared < network.bits) {
            val byteIndex = compared / 8
            if (byteIndex >= network.prefix.size) return false
            val mask = 0x80 shr (compared % 8)
            val actual = bytes[byteIndex].toInt() and 0xFF
            val expected = network.prefix[byteIndex].toInt() and 0xFF
            if ((actual and mask) != (expected and mask)) return false
            compared++
        }
        return true
    }

    /** A network the guard refuses. */
    private class Network(val prefix: ByteArray, val bits: Int)

    /** Build a network from its dotted-quad or colon-hex form. */
    private fun network(text: String): Network {
        val slash = text.lastIndexOf('/')
        val address = text.substring(0, slash)
        val bits = text.substring(slash + 1).toInt()
        val bytes = parseAddress(address)
        return Network(bytes, bits)
    }

    /** Parse a dotted-quad or a colon-hex address into its bytes. */
    private fun parseAddress(text: String): ByteArray =
        if (text.contains(':')) {
            parseIpv6(text)
        } else {
            text.split('.').map { it.toInt().toByte() }.toByteArray()
        }

    /** Parse a colon-hex address into its sixteen bytes. */
    private fun parseIpv6(text: String): ByteArray {
        val out = ByteArray(16)
        val head = text.substringBefore("::")
        val tail = text.substringAfter("::", "")
        val headGroups = if (head.isEmpty()) emptyList() else head.split(':')
        val tailGroups = if (tail.isEmpty()) emptyList() else tail.split(':')
        var index = 0
        for (group in headGroups) {
            val value = group.toInt(16)
            out[index++] = (value shr 8).toByte()
            out[index++] = (value and 0xFF).toByte()
        }
        index = 16 - tailGroups.size * 2
        for (group in tailGroups) {
            val value = group.toInt(16)
            out[index++] = (value shr 8).toByte()
            out[index++] = (value and 0xFF).toByte()
        }
        return out
    }

    /**
     * The IPv4 ranges the guard refuses: the private networks, the reserved
     * block and the multicast range.
     */
    private val IPV4_BLOCKED: List<Network> = listOf(
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.0.170/31",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
    ).map { network(it) }

    /**
     * The IPv6 ranges the guard refuses: the private networks, the reserved
     * blocks and the multicast range.
     */
    private val IPV6_BLOCKED: List<Network> = listOf(
        // Private.
        "::1/128",
        "::/128",
        "::ffff:0:0/96",
        "64:ff9b:1::/48",
        "100::/64",
        "2001::/23",
        "2001:db8::/32",
        "2002::/16",
        "3fff::/20",
        "fc00::/7",
        "fe80::/10",
        // Reserved.
        "::/8",
        "100::/8",
        "200::/7",
        "400::/6",
        "800::/5",
        "1000::/4",
        "4000::/3",
        "6000::/3",
        "8000::/3",
        "a000::/3",
        "c000::/3",
        "e000::/4",
        "f000::/5",
        "f800::/6",
        "fe00::/9",
        // Multicast.
        "ff00::/8",
    ).map { network(it) }
}

/**
 * The search endpoint and the rules that shape a request to it.
 *
 * The host is a constant rather than a parameter because the host is *not*
 * part of the caller's authority: only the query is. Every URL built from it
 * therefore carries a host no caller can influence, and the caller's query
 * only ever reaches the server as a query-string value.
 */
object WebSearchEndpoints {

    /** The literal host the search request is sent to. */
    const val HOST: String = "html.duckduckgo.com"

    /** The path of the HTML endpoint. */
    const val SEARCH_PATH: String = "/html/"

    /** The base the search request is built from. */
    val SEARCH_BASE: String = "https://$HOST$SEARCH_PATH"

    /**
     * Return the request URL for [query].
     *
     * The query is percent-encoded into the query string, so a query can
     * never point the request at a host of its own choosing.
     *
     * @throws WebEvidenceError the query is empty.
     */
    fun buildSearchUrl(query: String): String {
        if (query.isBlank()) {
            throw WebEvidenceError(
                reason = "the search query must be a non-empty string",
                query = query,
            )
        }
        return "$SEARCH_BASE?q=${quotePlus(query)}"
    }

    /**
     * Return the address a search result actually points at.
     *
     * The endpoint wraps its results in a redirect that carries the real
     * address in the `uddg` query parameter; a result whose link is already
     * absolute is returned unchanged.
     */
    fun normaliseResultUrl(href: String): String {
        var candidate = href.trim()
        if (candidate.isEmpty()) {
            return ""
        }
        if (candidate.startsWith("//")) {
            candidate = "https:$candidate"
        }
        if (!candidate.startsWith("http://") && !candidate.startsWith("https://")) {
            candidate = joinUrl(SEARCH_BASE, candidate)
        }
        val targets = parseQuery(queryOf(candidate))["uddg"]
        if (targets != null && targets.isNotEmpty() && targets[0].isNotBlank()) {
            return targets[0].trim()
        }
        return candidate
    }

    /**
     * Return the results parsed out of a search response.
     *
     * A body that is not text at all yields no results rather than an error:
     * the endpoint's markup is not a contract, and an empty result is the
     * honest answer to "this page held nothing we recognise".
     */
    fun parseSearchResults(body: ByteArray): List<WebResult> {
        val document = String(body, Charsets.UTF_8)
        val results = mutableListOf<WebResult>()
        var pending: WebResult? = null
        var index = 0

        while (index < document.length) {
            val anchor = findAnchor(document, index) ?: break
            val classes = anchor.classes
            val inner = document.substring(anchor.bodyStart, anchor.bodyEnd)
            when {
                "result__a" in classes -> {
                    // A title that never gets its snippet is still a result.
                    pending?.let { results.add(it) }
                    pending = WebResult(
                        url = normaliseResultUrl(anchor.attributes["href"] ?: ""),
                        title = visibleText(inner),
                    )
                }

                "result__snippet" in classes -> {
                    val current = pending
                    if (current != null) {
                        results.add(current.copy(snippet = visibleText(inner)))
                        pending = null
                    }
                }
            }
            index = anchor.bodyEnd
        }
        pending?.let { results.add(it) }
        return results.filter { it.url.isNotEmpty() }
    }

    /** The query string of [url]. */
    private fun queryOf(url: String): String {
        val start = url.indexOf('?')
        if (start < 0) return ""
        val end = url.indexOf('#', start)
        return if (end < 0) url.substring(start + 1) else url.substring(start + 1, end)
    }

    /** The parameters of [query], decoded, in the order they appeared. */
    private fun parseQuery(query: String): Map<String, List<String>> {
        val parameters = LinkedHashMap<String, MutableList<String>>()
        for (part in query.split('&')) {
            if (part.isEmpty()) continue
            val name = part.substringBefore('=')
            val value = if (part.contains('=')) part.substringAfter('=') else ""
            if (value.isEmpty()) continue
            parameters.getOrPut(percentDecode(name)) { mutableListOf() }.add(percentDecode(value))
        }
        return parameters
    }

    /**
     * Resolve [relative] against [base], the way a URL join does.
     *
     * The rules are the ones a join follows: a reference that names a scheme
     * of its own wins outright, a reference that begins with a slash replaces
     * the path, a reference that carries only a query or a fragment keeps the
     * path, and a relative reference replaces the last segment of the path.
     * The dot segments are removed afterwards, because a path that says `..`
     * names a directory above the one it was joined to.
     */
    private fun joinUrl(base: String, relative: String): String {
        if (relative.isEmpty()) return base
        if (SCHEME.containsMatchIn(relative)) return relative
        val baseRef = splitReference(base)
        val relRef = splitReference(relative)
        val authority = if (relRef.authority.isNotEmpty()) relRef.authority else baseRef.authority
        val path = if (relRef.path.isEmpty()) {
            baseRef.path
        } else {
            val merged = if (relRef.path.startsWith("/")) {
                relRef.path
            } else {
                baseRef.path.substringBeforeLast("/") + "/" + relRef.path
            }
            removeDotSegments(merged)
        }
        val query = if (relRef.path.isEmpty() && relRef.query.isEmpty()) {
            baseRef.query
        } else {
            relRef.query
        }
        return buildString {
            append(baseRef.scheme).append("://").append(authority)
            append(path)
            if (query.isNotEmpty()) append('?').append(query)
            if (relRef.fragment.isNotEmpty()) append('#').append(relRef.fragment)
        }
    }

    /** The pieces of a URL reference. */
    private class Reference(
        val scheme: String,
        val authority: String,
        val path: String,
        val query: String,
        val fragment: String,
    )

    /** Split [reference] into the pieces a join works with. */
    private fun splitReference(reference: String): Reference {
        val withoutFragment = reference.substringBefore('#')
        val fragment = if (withoutFragment.length == reference.length) {
            ""
        } else {
            reference.substringAfter('#')
        }
        val withoutQuery = withoutFragment.substringBefore('?')
        val query = if (withoutQuery.length == withoutFragment.length) {
            ""
        } else {
            withoutFragment.substringAfter('?')
        }
        val schemeEnd = withoutQuery.indexOf("://")
        val scheme = if (schemeEnd < 0) "" else withoutQuery.substring(0, schemeEnd)
        val rest = if (schemeEnd < 0) withoutQuery else withoutQuery.substring(schemeEnd + 3)
        // A reference that names no authority is all path: "census" is a path,
        // not a host.
        val namesAuthority = schemeEnd >= 0 || rest.startsWith("//")
        val afterAuthority = if (rest.startsWith("//")) rest.substring(2) else rest
        val authority: String
        val path: String
        if (!namesAuthority) {
            authority = ""
            path = afterAuthority
        } else {
            val pathStart = afterAuthority.indexOf('/')
            authority = if (pathStart < 0) afterAuthority else afterAuthority.substring(0, pathStart)
            path = if (pathStart < 0) "" else afterAuthority.substring(pathStart)
        }
        return Reference(scheme, authority, path, query, fragment)
    }

    /**
     * Remove the `.` and `..` segments from [path].
     *
     * The segments are removed as the path is walked, so a `..` that walks
     * past the root leaves the root rather than escaping it.
     */
    private fun removeDotSegments(path: String): String {
        val out = StringBuilder()
        var input = path
        while (input.isNotEmpty()) {
            when {
                input.startsWith("../") -> input = input.substring(3)
                input.startsWith("./") -> input = input.substring(2)
                input.startsWith("/./") -> input = "/" + input.substring(3)
                input == "/." -> input = "/"
                input.startsWith("/../") -> {
                    input = "/" + input.substring(4)
                    out.setLength(out.toString().substringBeforeLast('/', "").length)
                }

                input == "/.." -> {
                    input = "/"
                    out.setLength(out.toString().substringBeforeLast('/', "").length)
                }

                input == "." || input == ".." -> input = ""
                else -> {
                    val next = if (input.startsWith("/")) input.indexOf('/', 1) else input.indexOf('/')
                    val segment = if (next < 0) input else input.substring(0, next)
                    out.append(segment)
                    input = if (next < 0) "" else input.substring(next)
                }
            }
        }
        return out.toString()
    }

    /** An anchor element and the span of the text it holds. */
    private class Anchor(
        val attributes: Map<String, String>,
        val classes: Set<String>,
        val bodyStart: Int,
        val bodyEnd: Int,
    )

    /** Find the next anchor at or after [from]. */
    private fun findAnchor(document: String, from: Int): Anchor? {
        var index = from
        while (index < document.length) {
            val open = document.indexOf('<', index)
            if (open < 0) return null
            if (document.startsWith("</a", open, ignoreCase = true)) {
                val close = document.indexOf('>', open)
                index = if (close < 0) document.length else close + 1
                continue
            }
            if (document.startsWith("<a", open, ignoreCase = true)) {
                val after = open + 2
                if (after >= document.length || (!document[after].isWhitespace() && document[after] != '>')) {
                    val close = document.indexOf('>', open)
                    index = if (close < 0) document.length else close + 1
                    continue
                }
                val tagEnd = findTagEnd(document, after)
                if (tagEnd < 0) return null
                val attributes = parseAttributes(document.substring(after, tagEnd))
                val bodyStart = tagEnd + 1
                val bodyEnd = findAnchorClose(document, bodyStart)
                val classAttribute = attributes["class"] ?: ""
                return Anchor(
                    attributes = attributes,
                    classes = classAttribute.split(' ', '\t', '\n', '\r').filter { it.isNotEmpty() }.toSet(),
                    bodyStart = bodyStart,
                    bodyEnd = bodyEnd,
                )
            }
            val close = document.indexOf('>', open)
            index = if (close < 0) document.length else close + 1
        }
        return null
    }

    /** The index of the `>` that closes the tag opened before [from]. */
    private fun findTagEnd(document: String, from: Int): Int {
        var index = from
        var quote = ' '
        while (index < document.length) {
            val c = document[index]
            when {
                quote != ' ' -> if (c == quote) quote = ' '
                c == '"' || c == '\'' -> quote = c
                c == '>' -> return index
            }
            index++
        }
        return -1
    }

    /** The index of the `</a>` that closes the anchor opened before [from]. */
    private fun findAnchorClose(document: String, from: Int): Int {
        var index = from
        while (index < document.length) {
            val open = document.indexOf("</a", index, ignoreCase = true)
            if (open < 0) return document.length
            val after = open + 3
            if (after >= document.length || document[after] == '>' || document[after].isWhitespace()) {
                return open
            }
            index = open + 3
        }
        return document.length
    }

    /** The attributes of a start tag, with their values decoded. */
    private fun parseAttributes(raw: String): Map<String, String> {
        val attributes = LinkedHashMap<String, String>()
        val pattern = ATTRIBUTE.findAll(raw)
        for (match in pattern) {
            val name = match.groupValues[1].lowercase()
            val raw = match.groupValues[2]
            val value = if (raw.length >= 2 &&
                ((raw.startsWith('"') && raw.endsWith('"')) ||
                    (raw.startsWith('\'') && raw.endsWith('\'')))
            ) {
                raw.substring(1, raw.length - 1)
            } else {
                raw
            }
            attributes[name] = unescapeHtml(value)
        }
        return attributes
    }

    /** The visible text of an HTML fragment. */
    private fun visibleText(html: String): String =
        unescapeHtml(TAG.replace(html, " "))
            .split(WHITESPACE)
            .filter { it.isNotEmpty() }
            .joinToString(" ")

    /** Decode the HTML entities of [text]. */
    private fun unescapeHtml(text: String): String {
        if (!text.contains('&')) return text
        val out = StringBuilder(text.length)
        var index = 0
        while (index < text.length) {
            val c = text[index]
            if (c != '&') {
                out.append(c)
                index++
                continue
            }
            val semicolon = text.indexOf(';', index + 1)
            if (semicolon < 0 || semicolon - index > 12) {
                out.append(c)
                index++
                continue
            }
            val entity = text.substring(index + 1, semicolon)
            val replacement = when {
                entity == "amp" -> "&"
                entity == "lt" -> "<"
                entity == "gt" -> ">"
                entity == "quot" -> "\""
                entity == "apos" -> "'"
                entity == "nbsp" -> "\u00a0"
                entity.startsWith("#x") || entity.startsWith("#X") ->
                    entity.drop(2).toIntOrNull(16)?.let { codePoint ->
                        if (codePoint in 1..0x10FFFF) String(Character.toChars(codePoint)) else null
                    }

                entity.startsWith("#") ->
                    entity.drop(1).toIntOrNull()?.let { codePoint ->
                        if (codePoint in 1..0x10FFFF) String(Character.toChars(codePoint)) else null
                    }

                else -> null
            }
            if (replacement == null) {
                out.append(c)
                index++
            } else {
                out.append(replacement)
                index = semicolon + 1
            }
        }
        return out.toString()
    }

    /** Percent-encode [value] the way a query string carries it. */
    private fun quotePlus(value: String): String {
        val out = StringBuilder(value.length)
        for (byte in value.toByteArray(Charsets.UTF_8)) {
            val code = byte.toInt() and 0xFF
            val safe = code in 'a'.code..'z'.code ||
                code in 'A'.code..'Z'.code ||
                code in '0'.code..'9'.code ||
                code == '_'.code || code == '.'.code || code == '-'.code || code == '~'.code
            when {
                safe -> out.append(code.toChar())
                code == ' '.code -> out.append('+')
                else -> out.append('%').append("%02X".format(code))
            }
        }
        return out.toString()
    }

    /** Decode a percent-encoded query-string value. */
    private fun percentDecode(value: String): String {
        val bytes = ArrayList<Byte>(value.length)
        var index = 0
        while (index < value.length) {
            val c = value[index]
            when {
                c == '+' -> {
                    bytes.add(' '.code.toByte())
                    index++
                }

                c == '%' && index + 3 <= value.length -> {
                    val parsed = value.substring(index + 1, index + 3).toIntOrNull(16)
                    if (parsed == null) {
                        bytes.add(c.code.toByte())
                        index++
                    } else {
                        bytes.add(parsed.toByte())
                        index += 3
                    }
                }

                else -> {
                    for (byte in c.toString().toByteArray(Charsets.UTF_8)) bytes.add(byte)
                    index++
                }
            }
        }
        return String(bytes.toByteArray(), Charsets.UTF_8)
    }
}

/** An HTML tag, used by the fallback text extractor. */
private val TAG: Regex = Regex("<[^>]+>")

/**
 * A run of whitespace, as Python's `str.split` sees it.
 *
 * The `(?U)` flag makes the class Unicode-aware, so the run may be a no-break
 * space or an ideographic space and not only the four ASCII ones.
 */
private val WHITESPACE: Regex = Regex("(?U)\\s+")

/** The scheme of a URL reference, which makes it a reference of its own. */
private val SCHEME: Regex = Regex("^[A-Za-z][A-Za-z0-9+.\\-]*:")

/** A start tag's attribute, with or without a quoted value. */
private val ATTRIBUTE: Regex = Regex("([A-Za-z_:][-A-Za-z0-9_:.]*)\\s*=\\s*(\"[^\"]*\"|'[^']*'|[^\\s\"'=<>`]+)")
