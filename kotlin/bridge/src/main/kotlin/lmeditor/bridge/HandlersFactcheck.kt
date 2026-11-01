package lmeditor.bridge

import lmeditor.core.json.jsonDouble
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonString
import lmeditor.factcheck.DEFAULT_WEB_MAX_BYTES
import lmeditor.factcheck.DEFAULT_WEB_TIMEOUT_SECONDS
import lmeditor.factcheck.DuckDuckGoSearchProvider
import lmeditor.factcheck.HttpWebPageReader
import lmeditor.factcheck.WebFailure
import lmeditor.factcheck.WebFetcher
import lmeditor.factcheck.WebRequestError
import lmeditor.factcheck.WebResult
import lmeditor.factcheck.WebSearchEndpoints
import lmeditor.factcheck.extractPageText
import lmeditor.ingest.HttpSafetyError
import lmeditor.ingest.PayloadTooLargeError
import lmeditor.ingest.UnsafeSourceError
import lmeditor.ingest.assertPublicUrl
import lmeditor.ingest.fetchLimited
import java.time.Duration
import java.util.Base64

/**
 * The web-evidence half of the fact-check seam.
 *
 * Everything the web module used to decide in Python is decided here now: the
 * address the search request goes to, the query it carries, the parsing of the
 * response, the address a result points at, and the text a page is read as.
 * What crosses the seam is the request and the answer, never a rule.
 *
 * A body crosses as base64, because a search response is not text that can be
 * carried in a JSON string without changing it.
 */
fun factcheckWebHandlers(): Map<String, (Map<String, Any?>) -> Map<String, Any?>> = mapOf(
    "factcheck.web.search" to { params ->
        // The search itself: a literal host, an encoded query, and the results
        // the endpoint reported.
        val provider = DuckDuckGoSearchProvider(
            fetcher = TIER_WEB_FETCHER,
            timeout = webTimeout(params),
            maxBytes = params.jsonInt("max_bytes", DEFAULT_WEB_MAX_BYTES),
        )
        val results = provider.search(params.jsonString("query"), params.jsonInt("limit", 5))
        mapOf("results" to results.map { it.toWireMap() })
    },
    "factcheck.web.fetch_page_text" to { params ->
        // The page-reading path: the address is vetted, the body is bounded,
        // and the visible text of the page is what comes back.
        val reader = HttpWebPageReader(
            fetcher = TIER_WEB_FETCHER,
            timeout = webTimeout(params),
            maxBytes = params.jsonInt("max_bytes", DEFAULT_WEB_MAX_BYTES),
        )
        mapOf("text" to reader.readPageText(params.jsonString("url")))
    },
    "factcheck.web.parse_search_results" to { params ->
        val body = Base64.getDecoder().decode(params.jsonString("body"))
        mapOf("results" to WebSearchEndpoints.parseSearchResults(body).map { it.toWireMap() })
    },
    "factcheck.web.normalise_result_url" to { params ->
        mapOf("url" to WebSearchEndpoints.normaliseResultUrl(params.jsonString("href")))
    },
    "factcheck.web.extract_page_text" to { params ->
        val body = Base64.getDecoder().decode(params.jsonString("body"))
        mapOf("text" to extractPageText(body))
    },
)

/**
 * The transport the web seam fetches over.
 *
 * The address policy and the bounded fetch are the ingest module's; this is
 * the adapter that carries them into the fact-check rule, and it names the
 * four failures the way the rule expects them. The address is vetted before
 * the connection is opened, so a result that names an internal service is
 * refused rather than fetched.
 */
private val TIER_WEB_FETCHER = WebFetcher { url, timeout, maxBytes ->
    try {
        assertPublicUrl(url)
    } catch (exc: UnsafeSourceError) {
        throw WebRequestError(WebFailure.ADDRESS_REFUSED, exc.message ?: "the address was refused")
    }
    try {
        fetchLimited(url, maxBytes, timeout)
    } catch (exc: UnsafeSourceError) {
        throw WebRequestError(WebFailure.REDIRECT_REFUSED, exc.message ?: "the address was refused")
    } catch (exc: PayloadTooLargeError) {
        throw WebRequestError(WebFailure.TOO_LARGE, exc.message ?: "the body was too large")
    } catch (exc: HttpSafetyError) {
        throw WebRequestError(WebFailure.FAILED, exc.message ?: "the request failed")
    }
}

/** A web result as the seam carries it. */
private fun WebResult.toWireMap(): Map<String, Any?> = linkedMapOf(
    "url" to url,
    "title" to title,
    "snippet" to snippet,
)

/** The timeout a web request runs under. */
private fun webTimeout(params: Map<String, Any?>): Duration =
    Duration.ofMillis((params.jsonDouble("timeout", DEFAULT_WEB_TIMEOUT_SECONDS) * 1000).toLong())
